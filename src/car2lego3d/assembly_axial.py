"""Continuous native-OBB clearance along one already engaged shaft contact."""
from itertools import chain
from pathlib import Path

import numpy as np

from .assembly_catalog import intersects, shaft_match, world_ports
from .assembly_evidence import files_current, snapshot_files
from .assembly_graph import rigid
from .assembly_motion import posed_graph
from .assembly_requirements import identity

TOLERANCE = .35


def sweep_obb(moving, fixed, direction, travel, tolerance=TOLERANCE):
    """Return the open collision interval clipped to [0, travel], or None.

    With fixed orientations each separating-axis projection is linear in time,
    so intersecting its intervals covers every translation, including thin stops.
    """
    ca, aa, ha = moving
    cb, ab, hb = fixed
    lower, upper = 0., float(travel)
    for axis in chain(aa.T, ab.T, (np.cross(x, y) for x in aa.T for y in ab.T)):
        length = np.linalg.norm(axis)
        if length < 1e-8:
            continue
        axis = axis / length
        radius = float(abs(aa.T @ axis) @ ha + abs(ab.T @ axis) @ hb) - tolerance
        offset, speed = float((cb - ca) @ axis), float(direction @ axis)
        if radius <= 0:
            return None
        if abs(speed) < 1e-12:
            if abs(offset) >= radius:
                return None
            continue
        enter, leave = sorted(((offset - radius) / speed, (offset + radius) / speed))
        lower, upper = max(lower, enter), min(upper, leave)
        if lower >= upper:
            return None
    return lower, upper


def axial_clearance(graph, catalog, revision, moving, source, source_port, target, target_port,
                    max_travel=400., max_pairs=100000, checkpoint=None):
    if revision != graph["revision"]:
        raise ValueError("Stale assembly revision")
    if not np.isfinite(max_travel) or not 0 < max_travel <= 2000:
        raise ValueError("max_travel must be finite and in (0, 2000] LDU")
    if type(max_pairs) is not int or not 1 <= max_pairs <= 100000:
        raise ValueError("max_pairs must be in 1..100000")

    def tick():
        if checkpoint is not None:
            checkpoint()

    tick()
    nodes = {node["id"]: node for node in graph["instances"]}
    if any(name not in nodes for name in (moving, source, target)):
        raise ValueError("Unknown moving subtree or connector part")
    inside = lambda name: name == moving or name.startswith(moving + "/")
    if not inside(source) or inside(target):
        raise ValueError("Source must be inside moving subtree and target outside it")
    if any(nodes[name]["kind"] != "part" for name in (source, target)):
        raise ValueError("Select concrete connector-bearing parts")
    if any(not rigid(node[key]) for node in nodes.values() for key in ("local", "world")):
        raise ValueError("Axial clearance requires rigid local and world transforms")
    parts = [node for node in nodes.values() if node["kind"] == "part"]
    paths, unknown = {Path(__file__), Path(__file__).with_name("assembly_catalog.py"),
                      Path(__file__).with_name("native_ports.py")}, []
    for node in parts:
        tick()
        try:
            paths.add(catalog.evidence(node["part"], "collider", ".col"))
        except (ValueError, OSError) as error:
            unknown.append(dict(instance=node["id"], issue=str(error)))
    for name in (source, target):
        paths.add(catalog.evidence(nodes[name]["part"], "connectivity", ".conn"))
    evidence = snapshot_files(paths)
    ports, port_unknown = world_ports([nodes[source], nodes[target]], catalog)
    if port_unknown:
        raise ValueError("Selected connector evidence is incomplete: " + str(port_unknown))
    selected = []
    for name, port_id in ((source, source_port), (target, target_port)):
        port = next((p for p in ports if p[0] == name and p[1].id == port_id), None)
        if port is None or port[1].kind != "axle":
            raise ValueError("Select observed native shaft ports")
        selected.append(port)
    a, b = selected
    if not shaft_match(a, b):
        raise ValueError("Selected ports have no verified native shaft contact")

    # Raw lengths include guards: travel ends beyond both entire port intervals.
    axis = a[3]
    offset, facing = float((b[2] - a[2]) @ axis), float(np.sign(axis @ b[3]))
    low, high = sorted((offset, offset - facing * b[1].length))
    distances = {"positive": high + a[1].length + .25, "negative": -low + .25}
    directions = {name: dict(status="unverified", direction_world=(sign * axis).tolist(),
        disengagement_distance_ldu=distances[name], swept_distance_ldu=min(distances[name], max_travel),
        full_disengagement_covered=distances[name] <= max_travel, first_blocker=None)
        for name, sign in (("positive", 1), ("negative", -1))}

    boxes = {True: [], False: []}
    for node in parts:
        tick()
        try:
            matrix = np.asarray(node["world"])
            for index, (center, axes, half) in enumerate(catalog.boxes(node["part"])):
                boxes[inside(node["id"])].append((node["id"], index,
                    (matrix[:3, :3] @ center + matrix[:3, 3], matrix[:3, :3] @ axes, half)))
        except (ValueError, OSError) as error:
            unknown.append(dict(instance=node["id"], issue=str(error)))
    total = len(boxes[True]) * len(boxes[False])
    evaluated, candidates, initial = 0, 0, []
    fixed_centers = np.array([box[0] for _, _, box in boxes[False]]).reshape(-1, 3)
    fixed_extents = np.array([abs(box[1]) @ box[2] for _, _, box in boxes[False]]).reshape(-1, 3)
    positive = directions["positive"]["swept_distance_ldu"] * axis
    negative = -directions["negative"]["swept_distance_ldu"] * axis
    for mid, mi, ma in boxes[True]:
        tick()
        extent = abs(ma[1]) @ ma[2]
        # A conservative swept AABB rejects only pairs separated throughout both
        # translations. The pair budget applies to the remaining exact SAT tests.
        low = ma[0] - extent + np.minimum(positive, negative)
        high = ma[0] + extent + np.maximum(positive, negative)
        indices = np.flatnonzero(np.all((fixed_centers + fixed_extents >= low - 1e-9)
            & (fixed_centers - fixed_extents <= high + 1e-9), axis=1))
        candidates += len(indices)
        for index in indices:
            if evaluated >= max_pairs:
                break
            fid, fi, fb = boxes[False][index]
            if evaluated % 128 == 0:
                tick()
            evaluated += 1
            pair = dict(moving=mid, moving_collider=mi, fixed=fid, fixed_collider=fi)
            if intersects(ma, fb, TOLERANCE):
                if len(initial) < 20:
                    initial.append(pair)
            for row in directions.values():
                interval = sweep_obb(ma, fb, np.asarray(row["direction_world"]), row["swept_distance_ldu"])
                if interval is not None and (row["first_blocker"] is None
                        or interval[0] < row["first_blocker"]["entry_distance_ldu"]):
                    row["first_blocker"] = dict(**pair, entry_distance_ldu=float(interval[0]),
                        exit_distance_ldu=float(interval[1]))
    tick()
    unchanged = files_current(evidence)
    issues = []
    if unknown:
        issues.append("missing_native_collider_evidence")
    if evaluated < candidates:
        issues.append("collider_pair_budget_exhausted")
    if initial:
        issues.append("initial_cross_subtree_collision")
    if not unchanged:
        issues.append("evidence_changed_during_diagnostic")
    for row in directions.values():
        if not issues:
            row["status"] = "blocked" if row["first_blocker"] else (
                "clear" if row["full_disengagement_covered"] else "unverified")
        row["issues"] = [*issues, *([] if row["full_disengagement_covered"] else ["travel_limit_reached"])]
    return dict(schema="assembly-axial-clearance.v1", status="diagnostic_only", revision=revision,
        graph_sha256=identity(graph), evidence_files=evidence, evidence_unchanged=unchanged,
        moving=moving, contact=dict(source=source, source_port=source_port, target=target, target_port=target_port,
            source_semantic=a[1].semantic, target_semantic=b[1].semantic,
            source_caps=[a[1].start_capped, a[1].end_capped], target_caps=[b[1].start_capped, b[1].end_capped]),
        axis_world=axis.tolist(), tolerance_ldu=TOLERANCE, directions=directions,
        coverage=dict(moving_colliders=len(boxes[True]), fixed_colliders=len(boxes[False]),
            total_pairs=total, broadphase_rejected_pairs=total-candidates, candidate_pairs=candidates,
            evaluated_pairs=evaluated, max_pairs=max_pairs, max_travel_ldu=max_travel),
        initial_collisions=initial, unknown=unknown, issues=issues,
        scope="Rigid translation of the selected subtree against all outside native OBBs at the current pose. "
              "Clear means the entire path to the selected port's interval disengagement is clear within "
              "the stated collider tolerance. Blocked means a collider obstructs this straight path. "
              "Connector cap flags are reported, not treated as invented collider geometry. Other contacts, "
              "rotation/alternative release paths, friction, deformation, retention strength and load are not evaluated.",
        physical_retention="unverified", load_capacity="unverified")


def axial_motion(graph, catalog, revision, joint, moving, source, source_port, target, target_port,
                 samples=9, max_travel=400., max_pairs=100000, checkpoint=None):
    """Inspect one explicit subtree cut at sampled positions of an existing joint."""
    if revision != graph["revision"]:
        raise ValueError("Stale assembly revision")
    if type(samples) is not int or not 9 <= samples <= 181:
        raise ValueError("Axial motion needs 9 to 181 samples")
    if joint not in graph["joints"]:
        raise ValueError("Select an existing joint")
    if moving != joint and not moving.startswith(joint + "/"):
        raise ValueError("Moving cut must be the joint subtree or one of its descendants")
    declaration = graph["joints"][joint]
    limits = np.asarray(declaration["limits"], dtype=float)
    if limits.shape != (2,) or not np.isfinite(limits).all() or limits[0] >= limits[1]:
        raise ValueError("Joint needs finite increasing limits")

    arguments = dict(revision=revision, moving=moving, source=source, source_port=source_port,
        target=target, target_port=target_port, max_travel=max_travel, max_pairs=max_pairs,
        checkpoint=checkpoint)
    baseline = axial_clearance(graph, catalog, **arguments)
    evidence = sorted([*baseline["evidence_files"],
        *snapshot_files([Path(__file__).with_name("assembly_motion.py")])], key=lambda row: Path(row["file"]))
    poses, issues = [], []
    for value in np.linspace(*limits, samples):
        if checkpoint is not None:
            checkpoint()
        posed = posed_graph(graph, {joint: float(value)})
        try:
            report = axial_clearance(posed, catalog, **arguments)
        except ValueError as error:
            poses.append(dict(value=float(value), graph_sha256=identity(posed),
                directions={}, issues=["contact_unverified_at_pose"], error=str(error)))
            continue
        poses.append(dict(value=float(value), **{key: report[key] for key in
            ("graph_sha256", "directions", "issues", "initial_collisions", "unknown", "coverage")}))
    unchanged = files_current(evidence)
    if not unchanged:
        issues.append("evidence_changed_during_diagnostic")
    # Check the baseline too: a declared range need not include the saved pose.
    if baseline["issues"] or any(row["status"] == "unverified" for row in baseline["directions"].values()):
        issues.append("baseline_unverified")
    incomplete = bool(issues) or any(row["issues"] or any(
        direction["status"] == "unverified" for direction in row["directions"].values()) for row in poses)
    release = [dict(value=row["value"], direction=name,
        disengagement_distance_ldu=direction["disengagement_distance_ldu"])
        for row in poses for name, direction in row["directions"].items() if direction["status"] == "clear"]
    classification = ("unverified" if incomplete else "release_path_observed" if release
        else "blocked_both_at_sampled_poses")
    return dict(schema="assembly-axial-motion.v1", status="diagnostic_only", revision=revision,
        graph_sha256=identity(graph), evidence_files=evidence, evidence_unchanged=unchanged,
        joint=joint, moving=moving, contact=baseline["contact"], limits=limits.tolist(),
        unit="LDU" if declaration.get("type") == "prismatic" else "degrees", samples=samples,
        classification=classification, poses=poses, release_paths=release, issues=issues,
        baseline=dict(issues=baseline["issues"], directions=baseline["directions"]),
        scope="Continuous straight translation to selected port disengagement at sampled positions of "
              "one joint; all other joints remain neutral. The moving cut is explicit: a whole module "
              "result does not certify its individual parts. Intermediate angles, combinations, alternate "
              "release or insertion paths, friction, deformation and load are unverified.",
        physical_retention="unverified", load_capacity="unverified")
