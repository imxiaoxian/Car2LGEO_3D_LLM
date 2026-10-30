"""Native four-bar fixture for closed-contact steering kinematics, not a car."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from car2lego3d.assembly_agent import execution_hash
from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_layout import wheel_layout
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.assembly_requirements import descendants
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def matrix(x=0, y=0, z=0, rotation=None):
    value = np.eye(4)
    value[:3, 3] = [x, y, z]
    if rotation is not None:
        value[:3, :3] = rotation
    return value


def operations(wheels=False, chassis=False):
    result = [dict(op="module", name="frame.ldr", alias="frame", matrix=matrix().tolist()),
        dict(op="add", part="32316.dat", alias="ground", parent="@frame", matrix=matrix().tolist())]
    arm_rotation = Rotation.from_euler("y", 90, degrees=True).as_matrix()
    pin_rotation = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    left = matrix(y=-15, z=-40, rotation=arm_rotation)
    for name, z in (("left", -40), ("right", 40)):
        arm = matrix(y=-15, z=z, rotation=arm_rotation)
        result.extend([
            dict(op="module", name=name + ".ldr", alias=name, matrix=arm.tolist()),
            dict(op="add", part="6632.dat", alias=name + "arm", parent="@" + name,
                matrix=matrix().tolist()),
            dict(op="add", part="3705.dat", alias=name + "axle", parent="@" + name,
                matrix=(np.linalg.inv(arm) @ matrix(z=z, rotation=pin_rotation)).tolist()),
            dict(op="add", part="32002.dat", alias=name + "pin", parent="@" + name,
                matrix=(np.linalg.inv(arm) @ matrix(x=20, y=-20, z=z, rotation=pin_rotation)).tolist()),
            dict(op="articulate", instance="@" + name, source="@" + name + "axle",
                source_port="0", target="@ground", target_port="4" if name == "left" else "0",
                limits=[-30, 30] if name == "left" else [-45, 45], purpose="steering")])
    result.extend([
        dict(op="module", name="tie.ldr", alias="tie", parent="@left",
            matrix=(np.linalg.inv(left) @ matrix(x=20, y=-30)).tolist()),
        dict(op="add", part="32316.dat", alias="tiebeam", parent="@tie", matrix=matrix().tolist()),
        dict(op="articulate", instance="@tie", source="@tiebeam", source_port="4",
            target="@leftpin", target_port="0", limits=[-45, 45], purpose="steering")])
    if wheels or chassis:
        bearing_rotation = np.array([[0, 1, 0], [1, 0, 0], [0, 0, -1]])
        axle_rotation = Rotation.from_euler("y", -90, degrees=True).as_matrix()
        if chassis:
            result.append(dict(op="add", part="32316.dat", alias="rear", parent="@frame", matrix=matrix(x=200).tolist()))
            for side, z in (("l", -20), ("r", 20)):
                result.append(dict(op="add", part="32525.dat", alias="rail" + side,
                    parent="@frame", matrix=matrix(x=100, y=-50, z=z, rotation=arm_rotation).tolist()))
                for end, x in (("f", 0), ("b", 200)):
                    result.append(dict(op="add", part="3705.dat", alias="framepin" + side + end,
                        parent="@frame", matrix=matrix(x=x, y=-25, z=z, rotation=pin_rotation).tolist()))
            for side, z in (("left", -40), ("right", 40)):
                result.append(dict(op="add", part="3705.dat", alias="rear" + side + "axle",
                    parent="@frame", matrix=matrix(x=200, z=z, rotation=pin_rotation).tolist()))
        positions = [("left", 0, -40, -1), ("right", 0, 40, 1)]
        if chassis:
            positions.extend([("rearleft", 200, -40, -1), ("rearright", 200, 40, 1)])
        for name, x, z, sign in positions:
            parent = "@" + name if x == 0 else "@frame"
            arm = matrix(y=-15, z=z, rotation=arm_rotation) if x == 0 else np.eye(4)
            wheel = matrix(x=x + 20, y=30, z=z + sign * 70)
            result.extend([
                dict(op="add", part="6536.dat", alias=name + "bearing", parent=parent,
                    matrix=(np.linalg.inv(arm) @ matrix(x=x, y=30, z=z, rotation=bearing_rotation)).tolist()),
                dict(op="module", name=name + "wheel.ldr", alias=name + "wheel", parent=parent,
                    matrix=(np.linalg.inv(arm) @ wheel).tolist()),
                dict(op="add", part="56145.dat", alias=name + "rim", parent="@" + name + "wheel",
                    color=71, matrix=matrix().tolist()),
                dict(op="attach", part="61481.dat", alias=name + "tyre", parent="@" + name + "wheel",
                    color=0, source_port="0", target="@" + name + "rim", target_port="2"),
                dict(op="add", part="3706.dat", alias=name + "shaft", parent="@" + name + "wheel",
                    matrix=(np.linalg.inv(wheel) @ matrix(x=x + 20, y=30, z=z + sign * 30,
                        rotation=axle_rotation)).tolist()),
                dict(op="articulate", instance="@" + name + "wheel", source="@" + name + "shaft",
                    source_port="0", target="@" + name + "bearing", target_port="0",
                    limits=[0, 360], purpose="rolling")])
    return result


def response_evidence(tools, aliases, report, chassis=False):
    graph = tools.assembly.graph()
    nodes = {n["id"]: n for n in graph["instances"]}
    fixed = descendants(graph, [aliases["frame"]])
    sides = [side for side in ("left", "right", "rearleft", "rearright") if side + "wheel" in aliases]
    for side in sides:
        fixed -= descendants(graph, [aliases[side + "wheel"]])
    measurements, issues = {}, []
    for side in sides:
        tyre, wheel = aliases[side + "tyre"], aliases[side + "wheel"]
        vertices, _ = tools.catalog.library.triangles(nodes[tyre]["part"])
        vertices = np.asarray(vertices).reshape(-1, 3)
        joint = graph["joints"][wheel]
        def axis(pose):
            return np.asarray(pose[nodes[wheel]["parent"]]["world"])[:3, :3] @ joint["axis"]
        neutral_axis = axis(nodes)
        steering, rolling, axis_angle = 0., 0., 0.
        for group, poses in (("steering", report.get("poses", [])),
                ("rolling", next((row["poses"] for row in report.get("combined", [])
                    if row["rolling"] == wheel), []))):
            for row in poses:
                posed = {n["id"]: n for n in posed_graph(graph, row["positions"])["instances"]}
                if any(not np.allclose(posed[key]["world"], nodes[key]["world"], atol=1e-8, rtol=0) for key in fixed):
                    issues.append("fixed_frame_moved")
                delta = np.asarray(posed[tyre]["world"]) - np.asarray(nodes[tyre]["world"])
                displacement = float(np.linalg.norm(vertices @ delta[:3, :3].T + delta[:3, 3], axis=1).max())
                if group == "steering":
                    steering = max(steering, displacement)
                    cosine = np.dot(axis(posed), neutral_axis) / (np.linalg.norm(axis(posed)) * np.linalg.norm(neutral_axis))
                    axis_angle = max(axis_angle, float(np.degrees(np.arccos(np.clip(cosine, -1, 1)))))
                else:
                    # Isolate rolling response from the same solved steering pose.
                    unrolled = {n["id"]: n for n in posed_graph(graph,
                        {key: value for key, value in row["positions"].items() if key != wheel})["instances"]}
                    delta = np.asarray(posed[tyre]["world"]) - np.asarray(unrolled[tyre]["world"])
                    rolling = max(rolling, float(np.linalg.norm(vertices @ delta[:3, :3].T + delta[:3, 3], axis=1).max()))
        if side in {"left", "right"} and (steering <= 1 or axis_angle < 29.99):
            issues.append(side + "_steering_response_missing")
        if side.startswith("rear") and (steering > 1e-6 or axis_angle > 1e-6):
            issues.append(side + "_moved_during_steering")
        if rolling <= 1:
            issues.append(side + "_rolling_response_missing")
        measurements[side] = dict(max_steering_vertex_displacement_ldu=steering,
            max_rolling_vertex_displacement_ldu=rolling, max_steering_axis_change_degrees=axis_angle)
    if chassis and len(sides) != 4:
        issues.append("four_wheels_required")
    return dict(status="fail" if issues else "pass", issues=sorted(set(issues)), wheels=measurements)


def run(directory, catalog, wheels=False, chassis=False, preview=False):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a new output directory; preserve previous evidence")
    tools = AssemblyTools(directory, catalog)
    sources = dict(execution=execution_hash(), probe=digest(Path(__file__)))
    build = tools.call("build", dict(revision=0, operations=operations(wheels, chassis)))
    write_json(directory / "build-result.json", build)
    if not build["ok"]:
        return build
    aliases = build["result"]["aliases"]
    exported = tools.call("export", {})
    rolling = [aliases[side + "wheel"] for side in
        (["left", "right", "rearleft", "rearright"] if chassis else ["left", "right"])] if wheels or chassis else []
    result = tools.call("linkage_motion", dict(driver=aliases["left"],
        passive=[aliases["right"], aliases["tie"]],
        **(dict(rolling=rolling) if rolling else {})))
    graph = tools.assembly.graph()
    layout = None
    if chassis:
        bindings = {"wheel_" + position: dict(roots=[aliases[side + "wheel"]],
            roles=dict(tyre=[aliases[side + "tyre"]])) for position, side in
            (("front_left", "left"), ("front_right", "right"), ("rear_left", "rearleft"), ("rear_right", "rearright"))}
        bindings["chassis"] = dict(roots=[aliases["frame"]], roles={})
        layout = wheel_layout(graph, tools.catalog, dict(modules={key: {} for key in bindings}), bindings)
    response = response_evidence(tools, aliases, result.get("result", {}), chassis) if rolling else None
    archive = directory / "model.io"
    content = lambda g: {key: value for key, value in g.items() if key not in {"revision", "source_sha256"}}
    roundtrip = archive.is_file() and content(IOArchive(archive).assembly.graph()) == content(graph)
    rendered = tools.call("preview", {}) if preview else None
    summary = dict(schema="native-linkage-probe.v2",
        construction="deterministic_fixture_not_planner_selected", parts=sum(n["kind"] == "part" for n in graph["instances"]),
        sources=sources, sources_unchanged=sources == dict(execution=execution_hash(), probe=digest(Path(__file__))),
        linkage_status=result.get("result", {}).get("status"),
        linkage_current=check_current(graph, result.get("result", {})),
        export_ok=exported["ok"], archive_roundtrip=roundtrip,
        model_io_sha256=digest(archive) if archive.is_file() else None,
        response=response, layout=layout, preview=rendered, physical="unverified", appearance="unverified")
    passed = (result.get("ok") and summary["linkage_status"] == "pass" and summary["linkage_current"]
        and summary["sources_unchanged"] and exported["ok"] and roundtrip
        and (response is None or response["status"] == "pass")
        and (not chassis or layout["status"] == "pass")
        and (not preview or rendered.get("ok") and rendered.get("result", {}).get("status") == "pass"))
    summary["probe_pass"] = bool(passed)
    write_json(directory / "probe-summary.json", summary)
    write_json(directory / "probe-result.json", result)
    return {**result, "probe_pass": bool(passed)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--catalog", default="D:/lego/Studio 2.0/ldraw")
    parser.add_argument("--wheels", action="store_true")
    parser.add_argument("--chassis", action="store_true")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    result = run(args.output, args.catalog, args.wheels, args.chassis, args.preview)
    print(json.dumps(dict(ok=result.get("ok"), status=result.get("result", {}).get("status"),
        probe_pass=result.get("probe_pass", False), error=result.get("error"), output=args.output), ensure_ascii=False))
    raise SystemExit(0 if result.get("probe_pass") else 1)
