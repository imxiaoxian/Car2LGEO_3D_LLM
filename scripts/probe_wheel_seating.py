"""Diagnose shaft seating and full rolling travel on an immutable state copy."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

import numpy as np

from car2lego3d.assembly_catalog import shaft_match, shaft_snap_origin
from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def run(source, output, native, module):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new diagnostic directory; preserve prior evidence")
    source_hash = digest(source)
    state = json.loads(source.read_text(encoding="utf-8"))
    if state.get("packages") or state.get("source_archive"):
        raise ValueError("This diagnostic requires an independent native-parts state")
    output.mkdir(parents=True)
    shutil.copyfile(source, output / "source-state.json")
    shutil.copyfile(source, output / "state.json")
    tools = AssemblyTools(output, native)
    graph = tools.assembly.graph()
    nodes = {n["id"]: n for n in graph["instances"]}
    binding = state["module_bindings"][module]
    rim, tyre, hub = (binding["roles"][role][0] for role in ("rim", "tyre", "hub"))

    def ports(identity):
        node = nodes[identity]
        world = np.asarray(node["world"])
        return [(identity, port, world[:3, 3] + world[:3, :3] @ port.position,
                 world[:3, :3] @ port.direction, world[:3, :3] @ port.forward)
                for port in tools.catalog.ports(node["part"]) if port.kind == "axle"]

    before = tools.tool_check()
    options = []
    for a in ports(rim):
        for b in ports(hub):
            try:
                origin = shaft_snap_origin(a, b)
            except ValueError:
                continue
            delta = origin - a[2]
            if shaft_match((a[0], a[1], origin, a[3], a[4]), b):
                options.append((float(np.linalg.norm(delta)), delta, a, b))
    if not options:
        raise ValueError("No compatible legal rim-to-hub seating")
    _, delta, a, b = min(options, key=lambda row: row[0])
    operations = []
    for identity in (rim, tyre):
        node = nodes[identity]
        parent = np.eye(4) if node["parent"] == "root" else np.asarray(nodes[node["parent"]]["world"])
        world = np.asarray(node["world"])
        world[:3, 3] += delta
        operations.append(dict(op="transform", instance=identity, matrix=(np.linalg.inv(parent) @ world).tolist()))
    edited = tools.call("edit", dict(revision=tools.assembly.revision, operations=operations))
    if not edited["ok"]:
        raise ValueError(edited["error"])
    seated = tools.tool_check()
    original_motion = tools.tool_motion(samples=9)
    full_joints = []
    for identity, joint in tools.assembly.joints.items():
        if joint.get("purpose") == "rolling":
            full_joints.append(dict(deepcopy(joint), op="joint", instance=identity, limits=[0, 360]))
    if not full_joints:
        raise ValueError("No declared rolling joint to test")
    edited = tools.call("edit", dict(revision=tools.assembly.revision, operations=full_joints))
    if not edited["ok"]:
        raise ValueError(edited["error"])
    full_check = tools.tool_check()
    full_motion = tools.tool_motion(samples=9)
    rendered, exported = tools.call("render", {}), tools.call("export", {})

    def summary(report):
        return dict(status=report["status"], components=len(report["components"]),
                    contacts=len(report["connections"]), collisions=len(report["collisions"]),
                    unknown=len(report["unknown"]))

    result = dict(schema="wheel-seating-probe.v1", source=str(source), source_sha256=source_hash,
        source_unchanged=digest(source) == source_hash, source_revision=state["revision"],
        module=module, rim=nodes[rim]["part"], hub=nodes[hub]["part"],
        ports=dict(rim=a[1].id, hub=b[1].id), delta_ldu=delta.tolist(), delta_mm=(delta * .4).tolist(),
        before=summary(before), seated=summary(seated), full_check=summary(full_check),
        original_joint_limits=[j["limits"] for j in graph["joints"].values()],
        original_motion=original_motion["status"], full_motion=full_motion["status"],
        full_pose_statuses=[dict(value=p["value"], status=p["status"], connectivity=p["connectivity"],
            collisions=len(p["collisions"])) for row in full_motion["joints"] for p in row["poses"]],
        render=rendered["ok"], export=exported["ok"], training_approved=False, vehicle_accepted=False,
        limits="Engineering diagnostic on a preserved state; not independent planner output or vehicle acceptance.")
    write_json(output / "diagnosis.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--module", default="wheel_front_left")
    print(json.dumps(run(**vars(parser.parse_args())), indent=2))
