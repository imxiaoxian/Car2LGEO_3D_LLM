"""Read baseline interfaces without turning submodel names into mechanism proof."""
import argparse
from collections import Counter, defaultdict
from itertools import product
import json
from pathlib import Path

import numpy as np

from car2lego3d.assembly_catalog import NativeCatalog, connections, world_ports
from car2lego3d.assembly_graph import Assembly, IOArchive
from car2lego3d.assembly_inspection import part_bounds
from car2lego3d.common import digest, write_json
from car2lego3d.delivery_targets import TARGETS


def ancestors(identity):
    tokens = identity.split("/")
    return ["/".join(tokens[:end]) for end in range(2, len(tokens))]


def world_bounds(node, bounds):
    low, high = bounds
    corners = np.array(list(product(*zip(low, high))))
    matrix = np.asarray(node["world"])
    points = corners @ matrix[:3, :3].T + matrix[:3, 3]
    return points.min(axis=0), points.max(axis=0)


def axis_hint(edge, ports):
    a = ports[(edge["a"], edge["port_a"])]
    b = ports[(edge["b"], edge["port_b"])]
    pa, pb = a[1], b[1]
    if pa.kind == pb.kind == "hinge":
        basis = "matched_native_hinge"
    elif pa.kind == pb.kind == "axle" and not (pa.cross_type and pb.cross_type):
        basis = "matched_shaft_with_unkeyed_member"
    else:
        return None
    return dict(connection=edge, basis=basis, connector_origin_ldu=a[2].tolist(),
        axis=a[3].tolist(), semantics=[pa.semantic, pb.semantic],
        status="unverified", limits_degrees=None,
        note="Candidate axis only; other mounts, friction, ownership and clearance may prevent motion.")


def module_interfaces(graph, edges, unknown, ports, bounds):
    nodes = {node["id"]: node for node in graph["instances"]}
    modules = {key: node for key, node in nodes.items() if node["kind"] == "module"}
    members, boundaries, missing = defaultdict(list), defaultdict(list), defaultdict(list)
    for node in nodes.values():
        if node["kind"] == "part":
            for parent in ancestors(node["id"]):
                if parent in modules:
                    members[parent].append(node)
    for edge in edges:
        parents_a, parents_b = set(ancestors(edge["a"])), set(ancestors(edge["b"]))
        for parent in parents_a ^ parents_b:
            if parent in modules:
                boundaries[parent].append(edge)
    for issue in unknown:
        for parent in ancestors(issue["instance"]):
            if parent in modules:
                missing[parent].append(issue)
    port_map = {(row[0], row[1].id): row for row in ports}
    result = []
    for identity, module in modules.items():
        parts, contacts = members[identity], boundaries[identity]
        boxes, unavailable = [], []
        for node in parts:
            if node["part"] in bounds:
                boxes.append(world_bounds(node, bounds[node["part"]]))
            else:
                unavailable.append(node["id"])
        hints = [hint for edge in contacts if (hint := axis_hint(edge, port_map))]
        result.append(dict(instance=identity, parent=module["parent"], section=module["part"],
            part_instances=[node["id"] for node in parts], part_count=len(parts),
            bom=dict(Counter(node["part"] for node in parts)),
            world_bounds_ldu=[np.min([b[0] for b in boxes], axis=0).tolist(),
                np.max([b[1] for b in boxes], axis=0).tolist()] if boxes else None,
            bounds_kind="union_of_transformed_local_geometry_aabbs",
            missing_geometry=unavailable, boundary_connections=contacts,
            boundary_kind_counts=dict(Counter(edge["kind"] for edge in contacts)),
            candidate_axes=hints, missing_connection_evidence=missing[identity],
            semantic_role="unverified", articulation="unverified", motion="not_checked"))
    return result


def audit(source, baseline, library, case):
    source, baseline = Path(source), Path(baseline)
    expected = TARGETS[case]["source_sha256"]
    if digest(source) != expected:
        raise ValueError("Frozen source hash changed: " + case)
    state_path = baseline / "state.json"
    state_hash = digest(state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state["source_sha256"] != expected or state["revision"] != 0 or state.get("joints"):
        raise ValueError("Expected an unedited baseline import: " + case)
    assembly = Assembly(state["text"], expected)
    original = IOArchive(source).assembly.graph()
    graph = assembly.graph()
    # Imported dependency sections may differ; leaf identities and placements must not.
    fields = ("id", "part", "color", "world")
    leaves = lambda value: [{key: n[key] for key in fields} for n in value["instances"] if n["kind"] == "part"]
    if leaves(original) != leaves(graph):
        raise ValueError("Baseline import differs from source instances: " + case)
    packages = []
    for relative in state.get("packages", []):
        package = (baseline / relative).resolve()
        if not package.is_relative_to(baseline.resolve()):
            raise ValueError("Package escaped the baseline import")
        packages.append(package)
    catalog = NativeCatalog(library, packages)
    nodes = [node for node in graph["instances"] if node["kind"] == "part"]
    edges, unknown = connections(nodes, catalog)
    ports, port_unknown = world_ports(nodes, catalog)
    bounds, geometry_errors, dependencies = {}, {}, {}
    for part in sorted({node["part"] for node in nodes}):
        try:
            bounds[part] = part_bounds(catalog, part)["bounds"]
        except (ValueError, OSError) as error:
            geometry_errors[part] = str(error)
        for folder, suffix in (("connectivity", ".conn"), ("collider", ".col")):
            try:
                path = catalog.evidence(part, folder, suffix)
                dependencies[str(path.resolve())] = digest(path)
            except (ValueError, OSError):
                pass
    for package in packages:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                dependencies[str(path.resolve())] = digest(path)
    modules = module_interfaces(graph, edges, unknown, ports, bounds)
    unchanged = digest(source) == expected and digest(state_path) == state_hash
    unchanged = unchanged and all(digest(Path(path)) == value for path, value in dependencies.items())
    if not unchanged:
        raise ValueError("Source or baseline evidence changed during audit")
    return dict(schema="baseline-articulation-audit.v1", case=case,
        source=str(source.resolve()), source_sha256=expected, source_unchanged=True,
        baseline_state_sha256=state_hash, baseline_import_matches_source=True,
        fresh_creation=False, part_instances=len(nodes), modules=modules,
        connections=edges, connection_unknown=unknown, port_unknown=port_unknown,
        geometry_errors=geometry_errors, dependency_hashes=dependencies,
        summary=dict(modules=len(modules), matched_connections=len(edges),
            modules_with_axis_hints=sum(bool(m["candidate_axes"]) for m in modules),
            modules_without_matched_boundary=sum(not m["boundary_connections"] for m in modules),
            missing_connection_records=len(unknown), missing_geometry_parts=len(geometry_errors)),
        articulation="unverified", motion="not_checked", digital_assembly="unverified",
        limits="Matched interfaces and axis hints do not prove door/cover identity, free travel, stability or absence of a mechanism.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=TARGETS)
    parser.add_argument("--source", type=Path, default=Path("D:/lego/result"))
    parser.add_argument("--baselines", type=Path, default=Path("runs/four-vehicle-acceptance-20261018/baselines"))
    parser.add_argument("--library", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory to preserve earlier evidence")
    args.output.mkdir(parents=True)
    summaries = []
    for case in args.case or ["nissan-r34", "bugatti-chiron-pur-sport"]:
        sources = [p for p in args.source.glob("*.io") if "[" + TARGETS[case]["set_num"] + "]" in p.name]
        if len(sources) != 1:
            raise ValueError("Baseline source missing or ambiguous: " + case)
        report = audit(sources[0], args.baselines / (case + "-v2"), args.library, case)
        write_json(args.output / (case + ".json"), report)
        summary = dict(case=case, **report["summary"], source_unchanged=report["source_unchanged"],
            articulation=report["articulation"], motion=report["motion"])
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
    write_json(args.output / "summary.json", dict(schema="baseline-articulation-audit.v1", results=summaries))


if __name__ == "__main__":
    main()
