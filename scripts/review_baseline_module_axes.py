"""Bind visual source-module labels to immutable evidence and compare mount axes."""
import argparse
from itertools import combinations
import json
from pathlib import Path

import numpy as np

from car2lego3d.common import digest, write_json


def edge_key(edge):
    return tuple(sorted(((edge["a"], edge["port_a"]), (edge["b"], edge["port_b"]))))


def compare_mount_axes(module, issues=(), distance_ldu=.25, minimum_cosine=.9999):
    """Test a rigid module against its known mounts with the surroundings fixed."""
    axes, failures = [], []
    boundaries = {edge_key(edge) for edge in module["boundary_connections"]}
    for hint in module["candidate_axes"]:
        if edge_key(hint["connection"]) not in boundaries:
            raise ValueError("Axis is not a module boundary connection")
        origin, axis = np.asarray(hint["connector_origin_ldu"], float), np.asarray(hint["axis"], float)
        if (origin.shape != (3,) or axis.shape != (3,)
                or not np.isfinite(np.r_[origin, axis]).all() or np.linalg.norm(axis) < 1e-12):
            raise ValueError("Invalid mount axis")
        axes.append((hint, origin, axis / np.linalg.norm(axis)))
    for (left, p, a), (right, q, b) in combinations(axes, 2):
        cosine = float(np.clip(abs(a @ b), 0, 1))
        offset = q - p
        separation = float(max(np.linalg.norm(np.cross(offset, a)), np.linalg.norm(np.cross(offset, b))))
        if cosine < minimum_cosine or separation > distance_ldu:
            failures.append(dict(connections=[left["connection"], right["connection"]],
                angle_degrees=float(np.degrees(np.arccos(cosine))),
                maximum_origin_to_other_axis_ldu=separation))
    hinted = {edge_key(hint["connection"]) for hint, _, _ in axes}
    unsupported = [edge for edge in module["boundary_connections"] if edge_key(edge) not in hinted]
    relevant = set(module["part_instances"])
    relevant.update(endpoint for edge in module["boundary_connections"] for endpoint in (edge["a"], edge["b"]))
    missing = list(module.get("missing_connection_evidence", []))
    for issue in issues:
        if issue.get("instance") in relevant and issue not in missing:
            missing.append(issue)
    if failures:
        status = "conflicting_known_axes"
    elif missing or unsupported:
        status = "incomplete_boundary_evidence"
    elif not axes:
        status = "no_axis_evidence"
    else:
        status = "consistent_known_axis_candidate"
    return dict(status=status, candidate_axes=len(axes), boundary_connections=len(boundaries),
        conflicting_pairs=failures, non_axis_boundaries=unsupported,
        missing_connection_evidence=missing,
        tolerance=dict(distance_ldu=distance_ldu, minimum_absolute_cosine=minimum_cosine),
        articulation="unverified", motion="not_checked",
        assumption="Selected module is rigid; all external connected parts remain fixed.",
        limits="Known boundary constraints only; friction, missing cross-boundary contacts, linkages and swept clearance remain unchecked.")


def verified_presentation(path):
    path = Path(path).resolve()
    presentation = json.loads(path.read_text(encoding="utf-8"))
    for key in ("source", "audit", "baseline_state", "glb"):
        if digest(Path(presentation[key])) != presentation[key + "_sha256"]:
            raise ValueError("Presentation evidence changed: " + key)
    if presentation.get("geometry_modified") is not False:
        raise ValueError("Expected unmodified baseline geometry")
    audit = json.loads(Path(presentation["audit"]).read_text(encoding="utf-8"))
    if (Path(audit["source"]).resolve() != Path(presentation["source"]).resolve()
            or audit["source_sha256"] != presentation["source_sha256"]
            or audit["baseline_state_sha256"] != presentation["baseline_state_sha256"]
            or audit["case"] != presentation["case"] or not audit["baseline_import_matches_source"]):
        raise ValueError("Presentation and audit identities differ")
    for dependency, expected in audit["dependency_hashes"].items():
        if digest(Path(dependency)) != expected:
            raise ValueError("Audited native dependency changed: " + dependency)
    for image in presentation["images"]:
        image_path = (path.parent / image["file"]).resolve()
        if not image_path.is_relative_to(path.parent) or digest(image_path) != image["sha256"]:
            raise ValueError("Presentation image changed or escaped its directory")
    return presentation, audit


def review(path, labels):
    presentation, audit = verified_presentation(path)
    selected = {row["instance"] for row in presentation["modules"]}
    if set(labels) != selected:
        raise ValueError("Visual labels must exactly match the rendered module selection")
    modules = {row["instance"]: row for row in audit["modules"]}
    results = []
    for identity, label in labels.items():
        observed = []
        for name in label["observed_images"]:
            image = next((item for item in presentation["images"] if item["file"] == name), None)
            if image is None or image["module"] != identity:
                raise ValueError("Visual observation is not an image of this module")
            observed.append(dict(file=name, sha256=image["sha256"]))
        if not observed or not label["role"] or not label["observation"]:
            raise ValueError("A visual role requires an observation and image evidence")
        results.append(dict(instance=identity, role=label["role"], observation=label["observation"],
            role_basis="visual_highlight_review", observed_images=observed,
            axis_diagnosis=compare_mount_axes(modules[identity],
                [*audit["connection_unknown"], *audit["port_unknown"]])))
    return dict(schema="baseline-module-axis-review.v1", case=audit["case"],
        source=audit["source"], source_sha256=audit["source_sha256"],
        baseline_state_sha256=audit["baseline_state_sha256"],
        presentation=str(Path(path).resolve()), presentation_sha256=digest(Path(path)),
        audit=presentation["audit"], audit_sha256=digest(Path(presentation["audit"])),
        modules=results, fresh_creation=False, articulation="unverified", motion="not_checked",
        limits="Visual role review and axis diagnostics are not acceptance or proof of free travel.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presentation", type=Path, action="append", required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory")
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    reports = []
    for path in args.presentation:
        case = json.loads(path.read_text(encoding="utf-8"))["case"]
        result = review(path, labels[case])
        result["visual_labels_sha256"] = digest(args.labels)
        result["reviewer_sha256"] = digest(Path(__file__))
        reports.append(result)
    args.output.mkdir(parents=True)
    for result in reports:
        write_json(args.output / (result["case"] + ".json"), result)
        print(json.dumps(dict(case=result["case"], modules=[dict(instance=row["instance"],
            role=row["role"], axis_status=row["axis_diagnosis"]["status"])
            for row in result["modules"]])), flush=True)


if __name__ == "__main__":
    main()
