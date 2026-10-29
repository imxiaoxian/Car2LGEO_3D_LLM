"""Probe audited baseline mounts in memory without certifying or editing the IO."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from car2lego3d.assembly_catalog import NativeCatalog, colliders_intersect, connections
from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.common import digest, write_json
from review_baseline_module_axes import compare_mount_axes, edge_key, verified_presentation


def parent_local_joint(graph, identity, origin, axis, limits):
    nodes = {node["id"]: node for node in graph["instances"]}
    node = nodes[identity]
    if node["kind"] != "module":
        raise ValueError("An articulated probe requires a module")
    parent = np.eye(4) if node["parent"] == "root" else np.asarray(nodes[node["parent"]]["world"])
    inverse = np.linalg.inv(parent)
    origin, axis = np.asarray(origin, float), np.asarray(axis, float)
    if (origin.shape != (3,) or axis.shape != (3,)
            or not np.isfinite(np.r_[origin, axis]).all() or np.linalg.norm(axis) < 1e-12):
        raise ValueError("Invalid world joint axis")
    local_axis = inverse[:3, :3] @ axis
    local_axis /= np.linalg.norm(local_axis)
    return dict(type="revolute", purpose="baseline_diagnostic",
        origin=(inverse @ np.r_[origin, 1])[:3].tolist(), axis=local_axis.tolist(),
        limits=list(limits))


def boundary_contacts(edges, moving):
    return {edge_key(edge): edge for edge in edges if (edge["a"] in moving) != (edge["b"] in moving)}


def world_boxes(node, boxes):
    matrix = np.asarray(node["world"])
    resolved = [(matrix[:3, 3] + matrix[:3, :3] @ center,
                 matrix[:3, :3] @ axes, half) for center, axes, half in boxes]
    if not resolved:
        raise ValueError("Empty native collider")
    low = np.min([center - abs(axes) @ half for center, axes, half in resolved], axis=0)
    high = np.max([center + abs(axes) @ half for center, axes, half in resolved], axis=0)
    return resolved, low, high


class CrossBoundaryCollisions:
    """Check every known moving/fixed collider pair without truncating results."""
    def __init__(self, nodes, moving, catalog):
        self.moving = moving
        self.local, self.fixed, self.unknown = {}, {}, []
        for node in nodes:
            try:
                if node["part"] not in self.local:
                    self.local[node["part"]] = catalog.boxes(node["part"])
                if node["id"] not in moving:
                    self.fixed[node["id"]] = world_boxes(node, self.local[node["part"]])
            except (ValueError, OSError) as error:
                self.unknown.append(dict(instance=node["id"], part=node["part"], issue=str(error)))
        self.identities = sorted(self.fixed)
        self.centres = np.array([(self.fixed[key][1] + self.fixed[key][2]) / 2 for key in self.identities])
        self.radii = np.array([np.linalg.norm((self.fixed[key][2] - self.fixed[key][1]) / 2)
                              for key in self.identities])
        self.tree = cKDTree(self.centres) if self.identities else None

    def inspect(self, nodes):
        found = []
        if self.tree is None:
            return found
        for node in nodes:
            if node["id"] not in self.moving or node["part"] not in self.local:
                continue
            boxes, low, high = world_boxes(node, self.local[node["part"]])
            center, radius = (low + high) / 2, np.linalg.norm((high - low) / 2)
            for index in sorted(self.tree.query_ball_point(center, radius + self.radii.max() + 1e-9)):
                identity = self.identities[index]
                other, other_low, other_high = self.fixed[identity]
                if np.any(np.minimum(high, other_high) < np.maximum(low, other_low) - 1e-9):
                    continue
                if colliders_intersect(boxes, other):
                    found.append(tuple(sorted((node["id"], identity))))
        return sorted(set(found))


def probe(graph, module, catalog, *, limits=(-60, 60), samples=9, refinement=2):
    if (type(samples) is not int or not 9 <= samples <= 181
            or type(refinement) is not int or not 0 <= refinement <= 2
            or len(limits) != 2 or not np.isfinite(limits).all() or not limits[0] < 0 < limits[1]):
        raise ValueError("Use 9..181 samples, up to two refinements, and finite limits straddling zero")
    diagnosis = compare_mount_axes(module)
    if diagnosis["status"] != "consistent_known_axis_candidate":
        raise ValueError("Module lacks one consistent, fully evidenced boundary axis")
    identity = module["instance"]
    moving = set(module["part_instances"])
    nodes = [node for node in graph["instances"] if node["kind"] == "part"]
    actual = {node["id"] for node in nodes if node["id"].startswith(identity + "/")}
    if not moving or actual != moving:
        raise ValueError("Audited module membership differs from the current graph")
    original = {edge_key(edge): edge for edge in module["boundary_connections"]}
    endpoints = {endpoint for edge in original.values() for endpoint in (edge["a"], edge["b"])}
    relevant = moving | endpoints
    axis = module["candidate_axes"][0]
    articulated = deepcopy(graph)
    articulated["joints"] = {identity: parent_local_joint(graph, identity,
        axis["connector_origin_ldu"], axis["axis"], limits)}
    collider = CrossBoundaryCollisions(nodes, moving, catalog)

    def measure(value, baseline=None):
        posed = posed_graph(articulated, {identity: value})
        parts = [node for node in posed["instances"] if node["kind"] == "part"]
        matched, unknown = connections([node for node in parts if node["id"] in relevant], catalog)
        contacts = boundary_contacts(matched, moving)
        missing = [original[key] for key in sorted(original.keys() - contacts.keys())]
        overlaps = collider.inspect(parts)
        introduced = overlaps if baseline is None else sorted(set(overlaps) - set(baseline))
        return dict(value_degrees=float(value), boundary_connections_retained=len(original) - len(missing),
            lost_boundary_connections=missing, newly_matched_boundary_connections=[contacts[key]
                for key in sorted(contacts.keys() - original.keys())], connector_unknown=unknown,
            native_overlap_pairs=overlaps, newly_overlapping_pairs=introduced,
            hypothesis_status="known_failure" if missing or introduced else "unverified"
                if unknown or collider.unknown or overlaps else "no_known_failure_in_sampled_pose")

    zero = measure(0)
    if zero["lost_boundary_connections"]:
        raise ValueError("Audited boundary contacts cannot be reproduced at zero degrees")
    initial = zero["native_overlap_pairs"]
    poses = {float(value): measure(float(value), initial) for value in np.linspace(*limits, samples)}
    poses[0.] = measure(0., initial)
    for _ in range(refinement):
        ordered = sorted(poses)
        extra = {(a + b) / 2 for a, b in zip(ordered, ordered[1:])
                 if poses[a]["native_overlap_pairs"] or poses[b]["native_overlap_pairs"]}
        for value in sorted(extra - poses.keys()):
            poses[value] = measure(value, initial)
    records = [poses[value] for value in sorted(poses)]
    return dict(instance=identity, part_count=len(moving), candidate_axis=axis,
        parent_local_joint=articulated["joints"][identity],
        proposed_limits_degrees=list(limits), limits_basis="symmetric_diagnostic_hypothesis_not_official_travel",
        original_boundary_connections=list(original.values()), initial_native_overlap_pairs=initial,
        collider_unknown=collider.unknown, poses=records,
        summary=dict(sampled_poses=len(records),
            poses_losing_boundary_contacts=sum(bool(row["lost_boundary_connections"]) for row in records),
            poses_with_new_native_overlaps=sum(bool(row["newly_overlapping_pairs"]) for row in records),
            poses_with_any_native_overlaps=sum(bool(row["native_overlap_pairs"]) for row in records),
            missing_collider_records=len(collider.unknown)),
        articulation="unverified", motion_acceptance="unverified", physical="unverified",
        scope="audited boundary contacts and moving_vs_fixed_native_colliders; no pair-count truncation",
        limits="Existing overlaps are retained, not forgiven. Missing records, collider fidelity, internal "
            "module rigidity, unseen contacts, linked motion and physical friction are not certified.")


def verified_inputs(review_path):
    review_path = Path(review_path).resolve()
    before = digest(review_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    presentation, audit = verified_presentation(review["presentation"])
    checks = {"presentation": review["presentation_sha256"], "audit": review["audit_sha256"]}
    for key, expected in checks.items():
        if digest(Path(review[key])) != expected:
            raise ValueError("Reviewed evidence changed: " + key)
    if (review["case"] != audit["case"] or review["source_sha256"] != audit["source_sha256"]
            or review["baseline_state_sha256"] != audit["baseline_state_sha256"]):
        raise ValueError("Review identity differs from audited source")
    baseline = Path(presentation["baseline_state"]).parent
    state = json.loads(Path(presentation["baseline_state"]).read_text(encoding="utf-8"))
    if state["revision"] != 0 or state.get("joints") or state["source_sha256"] != audit["source_sha256"]:
        raise ValueError("Expected an unedited baseline import")
    packages = []
    for relative in state.get("packages", []):
        path = (baseline / relative).resolve()
        if not path.is_relative_to(baseline):
            raise ValueError("Native package escaped the audited baseline")
        packages.append(path)
    graph = Assembly(state["text"], state["source_sha256"]).graph()
    return review, presentation, audit, graph, packages, before


def run(review_path, library, identities=(), *, samples=9, refinement=2):
    review, presentation, audit, graph, packages, before = verified_inputs(review_path)
    modules = {row["instance"]: row for row in audit["modules"]}
    labels = {row["instance"]: row for row in review["modules"]}
    selected = list(identities) or [key for key, row in labels.items()
        if row["axis_diagnosis"]["status"] == "consistent_known_axis_candidate"]
    if not selected or len(set(selected)) != len(selected) or set(selected) - labels.keys():
        raise ValueError("Select distinct visually reviewed modules")
    catalog = NativeCatalog(library, packages)
    dependencies = {}
    for part in {node["part"] for node in graph["instances"] if node["kind"] == "part"}:
        for folder, suffix in (("collider", ".col"), ("connectivity", ".conn")):
            try:
                path = catalog.evidence(part, folder, suffix).resolve()
            except (ValueError, OSError):
                continue
            expected = audit["dependency_hashes"].get(str(path))
            if expected is None or digest(path) != expected:
                raise ValueError("Native dependency differs from the audited snapshot: " + str(path))
            dependencies[str(path)] = expected
    results = []
    for identity in selected:
        row = modules[identity]
        diagnosis = compare_mount_axes(row, [*audit["connection_unknown"], *audit["port_unknown"]])
        if diagnosis != labels[identity]["axis_diagnosis"]:
            raise ValueError("Axis review differs from current evidence")
        if diagnosis["status"] != "consistent_known_axis_candidate":
            raise ValueError("Cannot sweep a conflicting or unverified mount axis: " + identity)
        result = probe(graph, row, catalog, samples=samples, refinement=refinement)
        result["visually_reviewed_role"] = labels[identity]["role"]
        results.append(result)
        print(json.dumps(dict(case=review["case"], instance=identity, role=labels[identity]["role"],
            **result["summary"])), flush=True)
    verified_inputs(review_path)
    if digest(Path(review_path)) != before or any(digest(Path(path)) != value for path, value in dependencies.items()):
        raise ValueError("Baseline evidence changed during the probe")
    return dict(schema="baseline-module-motion-probe.v1", case=review["case"],
        source=review["source"], source_sha256=review["source_sha256"],
        baseline_state=presentation["baseline_state"], baseline_state_sha256=review["baseline_state_sha256"],
        review=str(Path(review_path).resolve()), review_sha256=before,
        probe_sha256=digest(Path(__file__)), native_dependency_hashes=dependencies,
        modules=results, source_unchanged=True, geometry_modified=False, fresh_creation=False,
        imported_transform_warnings=graph.get("warnings", []),
        digital_assembly="unverified", appearance="unverified", motion_acceptance="unverified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--module", action="append", default=[])
    parser.add_argument("--library", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--refinement", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output path to preserve earlier evidence")
    result = run(args.review, args.library, args.module, samples=args.samples, refinement=args.refinement)
    write_json(args.output, result)


if __name__ == "__main__":
    main()
