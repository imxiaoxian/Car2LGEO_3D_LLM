"""Search native carrier/chassis ports on an immutable engineering copy."""
import argparse
from copy import deepcopy
from functools import cache
from hashlib import sha256
from itertools import permutations, product
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from car2lego3d.assembly_catalog import compatible, shaft_snap_origin, validate, world_ports
from car2lego3d.assembly_layout import TOLERANCE_LDU, wheel_layout
from car2lego3d.assembly_mounts import owned_members
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def rotations():
    for order in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            rotation = np.eye(3)[list(order)] * np.asarray(signs)[:, None]
            if np.linalg.det(rotation) > .99:
                yield rotation


def moved_graph(graph, root, delta):
    result = deepcopy(graph)
    for node in result["instances"]:
        if node["id"] == root or node["id"].startswith(root + "/"):
            node["world"] = (delta @ np.asarray(node["world"])).tolist()
    return result


def run(source, output, native, moving, fixed, max_validated):
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Use a new engineering output directory")
    captured = source.read_bytes()
    state = json.loads(captured)
    if state.get("packages") or state.get("source_archive"):
        raise ValueError("Use an independent native-parts state")
    schedule = source.parent / "module-tasks.json"
    schedule_bytes = schedule.read_bytes()
    contract = json.loads(schedule_bytes)["contract"]["mission"]["vehicle_contract"]
    output.mkdir(parents=True)
    (output / "state.json").write_bytes(captured)
    (output / "source-module-tasks.json").write_bytes(schedule_bytes)
    tools = AssemblyTools(output, native)
    graph = tools.assembly.graph()
    nodes = {node["id"]: node for node in graph["instances"]}
    if nodes[moving]["kind"] != "module":
        raise ValueError("Moving root must be a module")
    fixed_ids = owned_members(graph, tools.module_bindings, fixed)
    wheel_roots = [root for name, binding in tools.module_bindings.items()
                   if name.startswith("wheel_") for root in binding["roots"]]
    moving_nodes = [node for node in nodes.values() if node["kind"] == "part"
                    and node["id"].startswith(moving + "/")
                    and not any(node["id"].startswith(root + "/") for root in wheel_roots)]
    fixed_nodes = [nodes[key] for key in sorted(fixed_ids) if nodes[key]["kind"] == "part"]
    source_ports, source_unknown = world_ports(moving_nodes, tools.catalog)
    target_ports, target_unknown = world_ports(fixed_nodes, tools.catalog)
    baseline = wheel_layout(graph, tools.catalog, contract, tools.module_bindings, require_complete=False)
    # Cache search geometry only. Selected proposals use the real resolver again.
    search_catalog = SimpleNamespace(library=SimpleNamespace(triangles=cache(tools.catalog.library.triangles)))
    stationary = {name: row for name, row in baseline["wheels"].items()
                  if not any(tyre["instance"].startswith(moving + "/") for tyre in row["tyres"])}
    moving_wheels = {name: row for name, row in baseline["wheels"].items() if name not in stationary}
    if not moving_wheels or not stationary or baseline["chassis"] is None:
        raise ValueError("Need actual moving and stationary tyre geometry and chassis bounds")
    tyre_points = {}
    for name, row in moving_wheels.items():
        parts = []
        for tyre in row["tyres"]:
            node = nodes[tyre["instance"]]
            triangles, _ = tools.catalog.library.triangles(node["part"])
            world = np.asarray(node["world"])
            parts.append(np.asarray(triangles).reshape(-1, 3) @ world[:3, :3].T + world[:3, 3])
        tyre_points[name] = np.concatenate(parts)
    fixed_ground = float(np.median([row["ground_y_ldu"] for row in stationary.values()]))
    target_positions = np.asarray([port[2] for port in target_ports])
    target_directions = np.asarray([port[3] for port in target_ports])
    placements, nearest, matched_ports, examined = {}, [], 0, set()
    for rotation_index, rotation in enumerate(rotations()):
        tyre_ground = {name: float((points @ rotation.T)[:, 1].max()) for name, points in tyre_points.items()}
        required_shift_y = fixed_ground - next(iter(tyre_ground.values()))
        if any(abs(value + required_shift_y - fixed_ground) > TOLERANCE_LDU for value in tyre_ground.values()):
            continue
        for a in source_ports:
            position, direction, forward = rotation @ a[2], rotation @ a[3], rotation @ a[4]
            aligned = np.abs(target_directions @ direction) >= .9999
            if a[1].kind != "axle":
                aligned &= np.abs(target_positions[:, 1] - position[1] - required_shift_y) <= TOLERANCE_LDU
            for index in np.flatnonzero(aligned):
                b = target_ports[index]
                if not compatible(a[1], b[1]):
                    continue
                if a[1].kind not in {"hinge", "axle"} and float(direction @ b[3]) < .9999:
                    continue
                translation = target_positions[index] - position
                if a[1].kind == b[1].kind == "axle":
                    try:
                        origin = shaft_snap_origin((a[0], a[1], position + translation, direction, forward), b)
                    except ValueError:
                        continue
                    translation += origin - target_positions[index]
                matched_ports += 1
                deviation = max(abs(value + translation[1] - fixed_ground) for value in tyre_ground.values())
                if len(nearest) < 12 or deviation * .4 < nearest[-1]["delta_ground_mm"]:
                    summary = dict(delta_ground_mm=deviation * .4, source=a[0], source_port=a[1].id,
                                   target=b[0], target_port=b[1].id, rotation=rotation.tolist(),
                                   translation_ldu=translation.tolist())
                    nearest = sorted([*nearest, summary], key=lambda item: item["delta_ground_mm"])[:12]
                if deviation > TOLERANCE_LDU:
                    continue
                delta = np.eye(4)
                delta[:3, :3], delta[:3, 3] = rotation, translation
                key = tuple(np.round(delta, 8).ravel())
                if key not in examined:
                    examined.add(key)
                    candidate = moved_graph(graph, moving, delta)
                    layout = wheel_layout(candidate, search_catalog, contract, tools.module_bindings,
                                          require_complete=False)
                    if layout["status"] != "pass":
                        continue
                    placements[key] = dict(delta=delta, contacts=[])
                if key in placements:
                    placements[key]["contacts"].append(dict(source=a[0], source_port=a[1].id,
                        target=b[0], target_port=b[1].id))
        progress = dict(rotation=rotation_index + 1, rotations=24, native_port_matches=matched_ports,
                        unique_poses=len(examined), stance_compatible_placements=len(placements))
        write_json(output / "search-progress.json", progress)
        print(json.dumps(progress), flush=True)
    ranked = sorted(placements.values(), key=lambda item: (-len(item["contacts"]),
                    float(np.linalg.norm(item["delta"][:3, 3]))))
    validated = []
    for index, proposal in enumerate(ranked[:max_validated]):
        candidate = moved_graph(graph, moving, proposal["delta"])
        layout = wheel_layout(candidate, tools.catalog, contract, tools.module_bindings, require_complete=False)
        unchanged = all(nodes[key]["world"] == next(n["world"] for n in candidate["instances"]
                        if n["id"] == key) for key in fixed_ids)
        if not unchanged:
            raise AssertionError("Fixed module changed")
        report = validate(candidate, tools.catalog)
        record = dict(id=f"mount-{index:03d}", world_delta=proposal["delta"].tolist(),
            native_port_pairs=proposal["contacts"], fixed_module_unchanged=unchanged,
            wheel_layout=layout, check=report,
            motion="unverified", semantic_roles="unverified", vehicle_accepted=False)
        write_json(output / f"{record['id']}.json", record)
        validated.append(dict(id=record["id"], digital=report["status"],
            components=len(report["components"]), collisions=len(report["collisions"]),
            unknown=len(report["unknown"]), mounting_port_pairs=len(proposal["contacts"]),
            world_delta=record["world_delta"]))
        print(json.dumps(validated[-1]), flush=True)
    result = dict(schema="steering-native-mount-probe.v1", source=str(source),
        source_sha256=sha256(captured).hexdigest(), revision=state["revision"],
        source_still_matches_snapshot=digest(source) == sha256(captured).hexdigest(),
        schedule_sha256=sha256(schedule_bytes).hexdigest(),
        schedule_still_matches_snapshot=digest(schedule) == sha256(schedule_bytes).hexdigest(),
        moving=moving, fixed=fixed, rotations=24, source_ports=len(source_ports), target_ports=len(target_ports),
        source_unknown=source_unknown, target_unknown=target_unknown,
        baseline_wheel_layout=baseline, fixed_ground_y_ldu=fixed_ground,
        native_port_matches=matched_ports, stance_compatible_placements=len(placements),
        unique_poses=len(examined), nearest_ground_scope="Axis-aligned native port pairs prefiltered by tyre ground height",
        nearest_ground_matches=nearest, validated=validated,
        vehicle_accepted=False, training_approved=False, planner_evaluation=False,
        limitations=["Engineering pose proposals, never independent planner acceptance.",
            "Search uses 24 axis-aligned rigid rotations and existing native mounting ports only.",
            "No new structural adapters or parts are synthesized.",
            "Missing fourth wheel, joint transforms, motion and semantics still require separate checks."])
    write_json(output / "mount-probe.json", result)
    return {key: result[key] for key in ("revision", "native_port_matches", "stance_compatible_placements",
            "validated", "source_still_matches_snapshot", "schedule_still_matches_snapshot")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--moving", required=True)
    parser.add_argument("--fixed", default="chassis")
    parser.add_argument("--max-validated", type=int, default=12)
    arguments = vars(parser.parse_args())
    if arguments["max_validated"] < 1:
        parser.error("max-validated must be positive")
    print(json.dumps(run(**arguments)))
