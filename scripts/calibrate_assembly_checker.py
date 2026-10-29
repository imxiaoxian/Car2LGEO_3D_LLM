"""Native positive/negative fixtures; these are checker calibration, not vehicle samples."""
import argparse
import json
from pathlib import Path

import numpy as np

from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def placement(x=0, y=0, z=0, rotation=None):
    matrix = np.eye(4)
    if rotation is not None:
        matrix[:3, :3] = rotation
    matrix[:3, 3] = (x, y, z)
    return matrix.tolist()


def seed(output, native, parts):
    tools = AssemblyTools(output, native)
    result = tools.call("edit", dict(revision=0, operations=[
        dict(op="add", part=part, color=color, matrix=matrix) for part, color, matrix in parts]))
    if not result["ok"]:
        raise ValueError(result["error"])
    return tools


def calibrate(output, native):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new output directory; preserve calibration history")
    records = []

    def record(name, tools, expected, *, gate=None, value=None):
        report = tools.tool_check()
        actual = report["gates"][gate] if gate else report["status"]
        passed = actual == (expected if value is None else value)
        records.append(dict(id=name, expectation=expected, actual=actual, calibrated=passed,
            graph_sha256=report["graph_sha256"], checks=str(tools.directory / "checks.json")))
        tools.call("export", {})
        return report

    wheel_parts = [("56145.dat", 71, placement()), ("61481.dat", 0, placement())]
    wheel = seed(output / "wheel-seated", native, wheel_parts)
    record("native_wheel_and_tyre_seated", wheel, "pass")
    mounted = seed(output / "wheel-mounted", native, [*wheel_parts,
        ("18651.dat", 0, placement(z=120, rotation=[[0, 0, -1], [0, 1, 0], [1, 0, 0]]))])
    wheel_ids = [n["id"] for n in mounted.assembly.graph()["instances"]]
    snapped = mounted.call("snap", dict(revision=mounted.assembly.revision,
        source=wheel_ids[2], source_port="1", target=wheel_ids[0], target_port="0"))
    if not snapped["ok"]:
        raise ValueError(snapped["error"])
    record("native_tyre_rim_axle_installed", mounted, "pass")
    misplaced = seed(output / "wheel-axial-gap", native,
        [wheel_parts[0], ("61481.dat", 0, placement(z=80))])
    record("tyre_not_installed", misplaced, "fail", gate="connections")
    collision = seed(output / "wheel-penetration", native,
        [wheel_parts[0], ("61481.dat", 0, placement(x=35))])
    record("tyre_penetrates_rim", collision, "fail", gate="collisions")
    missing = seed(output / "missing-dependency", native, [("missing-calibration-part.dat", 0, placement())])
    record("missing_geometry_and_connectors", missing, "unverified")

    hinge = seed(output / "hinge", native, [("4275b.dat", 15, placement()),
        ("4276b.dat", 15, placement(x=60, rotation=np.diag([-1, 1, -1])))])
    record("native_hinge_mated", hinge, "pass")
    moving = hinge.assembly.graph()["instances"][1]["id"]
    result = hinge.call("edit", dict(revision=hinge.assembly.revision, operations=[dict(op="joint",
        instance=moving, type="revolute", origin=[30, 4, 0], axis=[0, 0, 1], limits=[0, 90], purpose="door")]))
    if not result["ok"]:
        raise ValueError(result["error"])
    motion = hinge.call("motion", dict(samples=9))
    records.append(dict(id="hinge_nine_positions", expectation="pass",
        actual=motion.get("result", {}).get("status", "error"),
        calibrated=motion.get("result", {}).get("status") == "pass", checks=str(hinge.directory / "motion.json")))

    door = AssemblyTools(output / "door-with-glass", native)
    created = door.call("edit", dict(revision=0, operations=[
        dict(op="add", part="4275b.dat", color=15, matrix=placement()),
        dict(op="module", alias="door", name="moving-door.ldr", matrix=placement()),
        dict(op="add", parent="@door", part="4276b.dat", color=15,
             matrix=placement(x=60, rotation=np.diag([-1, 1, -1]))),
        dict(op="add", parent="@door", part="3023.dat", color=15,
             matrix=placement(x=60, y=-8, rotation=np.diag([-1, 1, -1]))),
        dict(op="module", alias="window", parent="@door", name="window.ldr", matrix=placement()),
        dict(op="add", parent="@window", part="60593.dat", color=15,
             matrix=placement(x=60, y=-80, rotation=np.diag([-1, 1, -1]))),
        dict(op="add", alias="glass", parent="@window", part="60602.dat", color=47,
             matrix=placement(x=60, y=-80, rotation=np.diag([-1, 1, -1]))),
        dict(op="joint", instance="@door", type="revolute", origin=[30, 4, 0],
             axis=[0, 0, 1], limits=[0, 90], purpose="door")]))
    if not created["ok"]:
        raise ValueError(created["error"])
    record("nested_door_panel_frame_glass", door, "pass")
    door_motion = door.call("motion", dict(samples=9))
    records.append(dict(id="door_and_glass_nine_positions", expectation="pass",
        actual=door_motion.get("result", {}).get("status", "error"),
        calibrated=door_motion.get("result", {}).get("status") == "pass",
        checks=str(door.directory / "motion.json"),
        limitations=["Native nested door/glass fixture; not Countach styling or physical certification"]))
    negative_door = AssemblyTools(output / "door-glass-detached", native)
    negative_door.import_file(door.directory / "model.io")
    detached = negative_door.call("edit", dict(revision=negative_door.assembly.revision, operations=[dict(op="transform",
        instance=created["result"]["aliases"]["glass"],
        matrix=placement(x=60, y=-80, z=100, rotation=np.diag([-1, 1, -1])))]))
    if not detached["ok"]:
        raise ValueError(detached["error"])
    record("door_glass_detached_negative", negative_door, "fail", gate="connections")

    lamp = seed(output / "transparent-layers", native, [("3023.dat", color, placement(y=y))
        for color, y in ((0, 0), (80, -8), (47, -16))])
    record("native_housing_reflector_transparent_layer_stack", lamp, "pass")
    rendered = lamp.call("render", {})
    records.append(dict(id="transparent_native_material", expectation="pass",
        actual=rendered.get("result", {}).get("status", "error"),
        calibrated=rendered.get("result", {}).get("status") == "pass",
        limitations=["Layer contact/material fixture; not a realistic vehicle lamp module"]))

    before = digest(wheel.directory / "state.json")
    ids = [n["id"] for n in wheel.assembly.graph()["instances"]]
    rollback = wheel.call("edit", dict(revision=wheel.assembly.revision, strict=True,
        operations=[dict(op="transform", instance=ids[1], matrix=placement(x=35))]))
    restored = not rollback["ok"] and digest(wheel.directory / "state.json") == before
    records.append(dict(id="failed_edit_rollback", expectation="unchanged", actual="unchanged" if restored else "changed",
        calibrated=restored))

    result = dict(schema="assembly-checker-calibration.v1", cases=records,
        status="pass" if all(row["calibrated"] for row in records) else "needs_diagnosis",
        execution_scope="native_geometry_connectors_colliders_and_host_tools", vehicle_accepted=False,
        training_approved=False, limitations=["These fixtures do not certify whole vehicles, appearance or physical durability."])
    write_json(output / "calibration.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    args = parser.parse_args()
    report = calibrate(args.output, args.native)
    print(json.dumps(dict(status=report["status"], cases=report["cases"])))
