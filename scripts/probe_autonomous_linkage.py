"""Observe a live planner building a linked-steering chassis from an empty scene."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

import numpy as np

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, execution_hash
from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_layout import wheel_layout
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.assembly_requirements import descendants
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json
from scripts.probe_autonomous_wheel import native_role, records, selection_evidence
from scripts.probe_connector_module import recovered_errors


def evidence(tools):
    """Measure this diagnostic only; ordinary vehicle acceptance stays separate."""
    graph = tools.assembly.graph()
    nodes = {node["id"]: node for node in graph["instances"]}
    parts = {key: node for key, node in nodes.items() if node["kind"] == "part"}
    joints = graph["joints"]
    issues, reports, descriptions, roles = [], {}, {}, {}
    for label, filename in (("check", "checks.json"), ("motion", "motion.json"),
                            ("linkage", "linkage-motion.json")):
        path = tools.directory / filename
        reports[label] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not reports[label] or not check_current(graph, reports[label]):
            issues.append(label + "_missing_or_stale")
    for label in ("check", "linkage"):
        if reports[label].get("status") != "pass":
            issues.append(label + "_not_passed")
    selected = selection_evidence(records(tools.directory / "trajectory.jsonl"),
                                  {node["part"] for node in parts.values()})
    if selected["status"] != "pass":
        issues.append("selection_not_observed_before_placement")
    if not 20 <= len(parts) <= 64:
        issues.append("requires_twenty_to_sixty_four_native_parts")
    for part in {node["part"] for node in parts.values()}:
        try:
            roles[part], descriptions[part] = native_role(tools.catalog, part)
        except (OSError, ValueError) as exc:
            issues.append("missing_native_part_evidence:" + part)
            descriptions[part] = str(exc)
    rolling = {key for key, joint in joints.items() if joint.get("purpose") == "rolling"}
    steering = set(joints) - rolling
    if len(rolling) != 4:
        issues.append("requires_four_rolling_joints")
    if not 3 <= len(steering) <= 9 or any(joints[key].get("purpose") != "steering" for key in steering):
        issues.append("requires_linked_steering_carriers_and_tie_joint")
    if any(joint.get("type", "revolute") != "revolute" for joint in joints.values()):
        issues.append("requires_revolute_joints")
    linkage = reports["linkage"]
    passive = linkage.get("passive", [])
    if ({linkage.get("driver"), *passive} != steering or len(set(passive)) != len(passive)
            or linkage.get("driver") in passive):
        issues.append("linkage_must_cover_every_nonrolling_joint")
    summary = dict(selection=selected, native_descriptions=descriptions,
        ordinary_motion_status=reports["motion"].get("status", "unverified"),
        vehicle_completion_ready=False, appearance="unverified", physical="unverified")
    if issues:
        return dict(status="fail", issues=sorted(set(issues)), **summary)

    contacts = reports["check"].get("connections", [])
    edges = {frozenset((row["a"], row["b"])) for row in contacts}
    wheels, moving, supports = [], set(), set()
    for root in sorted(rolling):
        members = set(parts) & descendants(graph, [root])
        by_role = {role: [key for key in members if roles.get(parts[key]["part"]) == role]
                   for role in ("rim", "tyre", "axle")}
        if (nodes[root]["kind"] != "module" or len(members) != 3
                or any(len(value) != 1 for value in by_role.values()) or moving & members):
            issues.append("requires_four_disjoint_rim_tyre_axle_submodels")
            continue
        moving.update(members)
        by_role = {role: value[0] for role, value in by_role.items()}
        if any(frozenset((by_role[a], by_role[b])) not in edges
               for a, b in (("rim", "tyre"), ("rim", "axle"))):
            issues.append("wheel_native_axle_rim_tyre_contacts_missing")
        bearings = []
        for contact in contacts:
            if by_role["axle"] not in (contact["a"], contact["b"]):
                continue
            other, port_id = ((contact["b"], contact["port_b"]) if contact["a"] == by_role["axle"]
                              else (contact["a"], contact["port_a"]))
            if other not in parts or other in members:
                continue
            port = next((p for p in tools.catalog.ports(parts[other]["part"]) if p.id == port_id), None)
            if port and port.kind == "axle" and port.semantic in {"pin_socket", "round_hole"}:
                bearings.append((other, port))
        if len(bearings) != 1:
            issues.append("wheel_requires_one_distinct_round_native_bearing")
        else:
            support, port = bearings[0]
            supports.add(support)
            parent = nodes[root]["parent"]
            parent_world = np.eye(4) if parent == "root" else np.asarray(nodes[parent]["world"])
            axis = parent_world[:3, :3] @ joints[root]["axis"]
            pivot = (parent_world @ [*joints[root]["origin"], 1])[:3]
            support_world = np.asarray(parts[support]["world"])
            bearing_axis = support_world[:3, :3] @ port.direction
            offset = pivot - (support_world @ [*port.position, 1])[:3]
            if (abs(axis[2]) < .9999 or abs(axis @ bearing_axis) < .9999
                    or np.linalg.norm(np.cross(offset, bearing_axis)) > .25):
                issues.append("rolling_axis_must_follow_actual_transverse_bearing")
        tyre = parts[by_role["tyre"]]
        vertices = np.asarray(tools.catalog.library.triangles(tyre["part"])[0]).reshape(-1, 3)
        world = np.asarray(tyre["world"])
        surface = vertices @ world[:3, :3].T + world[:3, 3]
        wheels.append(dict(root=root, **by_role, center=((surface.min(0) + surface.max(0)) / 2).tolist()))
    if len(supports) != 4 or supports & moving:
        issues.append("requires_four_separate_bearings_outside_rolling_subtrees")
    if len(wheels) != 4:
        return dict(status="fail", issues=sorted(set(issues)), **summary)

    wheels.sort(key=lambda row: (row["center"][0], row["center"][2]))
    fixed_parts = set(parts)
    for root in steering | rolling:
        fixed_parts -= descendants(graph, [root])
    frame_roots = set()
    for key in fixed_parts:
        root = nodes[key]["parent"]
        if root == "root":
            issues.append("fixed_frame_requires_submodel_roots")
            continue
        while nodes[root]["parent"] != "root":
            root = nodes[root]["parent"]
        frame_roots.add(root)
    if not fixed_parts:
        issues.append("requires_nonmoving_frame_parts")
    bindings = {"chassis": dict(roots=sorted(frame_roots))}
    for axle, pair in (("front", wheels[:2]), ("rear", wheels[2:])):
        for side, wheel in zip(("left", "right"), sorted(pair, key=lambda row: row["center"][2])):
            bindings[f"wheel_{axle}_{side}"] = dict(roots=[wheel["root"]], roles=dict(tyre=[wheel["tyre"]]))
            wheel["position"] = axle + "_" + side
    layout = wheel_layout(graph, tools.catalog, dict(modules={key: {} for key in bindings}), bindings)
    if layout["status"] != "pass" or not layout["complete"]:
        issues.append("native_wheel_layout_failed")
    for wheel in wheels:
        ancestors = {root for root in steering if wheel["root"] in descendants(graph, [root])}
        if wheel["position"].startswith("front"):
            if len(ancestors) != 1:
                issues.append("each_front_wheel_requires_one_distinct_steering_carrier")
            for carrier in ancestors:
                parent = nodes[carrier]["parent"]
                world = np.eye(4) if parent == "root" else np.asarray(nodes[parent]["world"])
                axis = world[:3, :3] @ joints[carrier]["axis"]
                if abs(axis[1]) < .9999:
                    issues.append("front_steering_carrier_requires_vertical_kingpin")
            wheel["carriers"] = sorted(ancestors)
        elif ancestors:
            issues.append("rear_wheel_must_not_descend_from_steering")
    front = [wheel for wheel in wheels if wheel["position"].startswith("front")]
    if (len({root for wheel in front for root in wheel["carriers"]}) != 2
            or linkage["driver"] not in {root for wheel in front for root in wheel["carriers"]}):
        issues.append("driver_and_distinct_passive_must_steer_the_two_front_wheels")
    driver_values = {float(v) for v in np.linspace(*joints[linkage["driver"]]["limits"], 9)}
    poses = linkage.get("poses", [])
    if (joints[linkage["driver"]]["limits"][0] > -30 or joints[linkage["driver"]]["limits"][1] < 30
            or not driver_values <= {row["positions"].get(linkage["driver"]) for row in poses}
            or any(row.get("status") != "pass" for row in poses)):
        issues.append("requires_nine_linked_poses_covering_minus_thirty_to_plus_thirty")
    measurements = {}
    for wheel in wheels:
        root, tyre = wheel["root"], wheel["tyre"]
        vertices = np.asarray(tools.catalog.library.triangles(parts[tyre]["part"])[0]).reshape(-1, 3)
        parent = nodes[root]["parent"]
        def axis(pose):
            world = np.eye(4) if parent == "root" else np.asarray(pose[parent]["world"])
            return world[:3, :3] @ joints[root]["axis"]
        neutral_axis = axis(nodes)
        angle, steering_distance = 0., 0.
        for pose in poses:
            posed = {node["id"]: node for node in posed_graph(graph, pose["positions"])["instances"]}
            if any(not np.allclose(posed[key]["world"], nodes[key]["world"], atol=1e-8, rtol=0)
                   for key in fixed_parts):
                issues.append("fixed_frame_moved")
            cosine = np.dot(axis(posed), neutral_axis) / (np.linalg.norm(axis(posed)) * np.linalg.norm(neutral_axis))
            angle = max(angle, float(np.degrees(np.arccos(np.clip(cosine, -1, 1)))))
            delta = np.asarray(posed[tyre]["world"]) - np.asarray(nodes[tyre]["world"])
            steering_distance = max(steering_distance, float(np.linalg.norm(
                vertices @ delta[:3, :3].T + delta[:3, 3], axis=1).max()))
        if wheel["position"].startswith("front") and (angle < 20 or steering_distance <= 1):
            issues.append("front_wheel_actual_steering_response_missing")
        if wheel["position"].startswith("rear") and (angle > 1e-6 or steering_distance > 1e-6):
            issues.append("rear_wheel_moved_during_steering")
        combined = next((row for row in linkage.get("combined", []) if row["rolling"] == root), {})
        expected = {(a, float(b)) for a in driver_values for b in np.linspace(*joints[root]["limits"], 9)}
        actual = {(row["positions"].get(linkage["driver"]), row["positions"].get(root))
                  for row in combined.get("poses", [])}
        if (not expected <= actual or joints[root]["limits"][1] - joints[root]["limits"][0] < 360
                or any(row.get("status") != "pass" for row in combined.get("poses", []))):
            issues.append("requires_each_wheel_rolling_through_all_nine_linked_poses")
        rolling_distance = {key: 0. for key in (wheel["rim"], tyre, wheel["axle"])}
        for pose in combined.get("poses", []):
            posed = {node["id"]: node for node in posed_graph(graph, pose["positions"])["instances"]}
            unrolled = {node["id"]: node for node in posed_graph(graph,
                {key: value for key, value in pose["positions"].items() if key != root})["instances"]}
            for key in rolling_distance:
                vertices = np.asarray(tools.catalog.library.triangles(parts[key]["part"])[0]).reshape(-1, 3)
                delta = np.asarray(posed[key]["world"]) - np.asarray(unrolled[key]["world"])
                rolling_distance[key] = max(rolling_distance[key], float(np.linalg.norm(
                    vertices @ delta[:3, :3].T + delta[:3, 3], axis=1).max()))
        if min(rolling_distance.values()) <= 1:
            issues.append("actual_rim_tyre_axle_rolling_response_missing")
        measurements[wheel["position"]] = dict(steering_axis_degrees=angle,
            steering_displacement_ldu=steering_distance, rolling_displacement_ldu=rolling_distance,
            combined_poses=len(combined.get("poses", [])))
    return dict(status="fail" if issues else "pass", issues=sorted(set(issues)), **summary,
                wheels=wheels, measurements=measurements, layout=layout)


def probe_sources():
    folder = Path(__file__).parent
    return {name: digest(folder / name) for name in (
        "probe_autonomous_linkage.py", "probe_autonomous_wheel.py", "probe_connector_module.py")}


def run(output, native, *, model=None, timeout=2400, max_steps=140):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve previous autonomous linkage evidence")
    tools = AssemblyTools(output / "assembly", native)
    mission = dict(request=dict(goal=
        "Build a compact four-wheel passenger-car chassis from the EMPTY scene. Select EVERY native part "
        "yourself using English description searches, then query bounds/ports before first placement. "
        "Use 20..64 unscaled parts: a fixed frame submodel, two front steering carriers with vertical "
        "kingpins and a real closed-contact tie linkage, and four separate rolling rim/tyre/axle submodels. "
        "Each front wheel belongs inside its steering carrier; rear bearings stay fixed. Each rolling axle "
        "uses one distinct ROUND native bearing, not a fixed keyed hole. X front-to-rear, Y down, Z left-right. "
        "Front X is smaller than rear X. Align axle pairs, share ground/centreline, keep frame above ground. "
        "Black tyres, gray rims. Use native contacts and articulate; aliases last only one build call. "
        "Use draft strict=false only during incomplete construction, then repair check. A physical tie part "
        "must link the front carriers, not two independently commanded wheels. Declare all nonrolling "
        "joints purpose=steering, one front carrier driver with limits [-30,30], other steering/tie joints "
        "passive with ranges including zero. Use linkage_motion(samples=9,driver,passive,rolling=[all four "
        "rolling IDs]) to preserve every original contact and check 324 combinations. Both front wheel "
        "axes must turn at least 20 degrees; rear wheels and frame stay still while steering. Separately "
        "run ordinary motion(samples=9), retaining its actual status. Linkage diagnostics never replace "
        "ordinary motion or vehicle acceptance. Read module_progress for this diagnostic's remaining "
        "issues. Preview, inspect actual pixels, export current revision, explicitly finish. No complete "
        "vehicle, appearance, Studio, retention, load or physical acceptance is claimed."),
        module_task=dict(id="autonomous_linked_four_wheel_chassis", motion_samples=9,
                         combined_poses=324, ordinary_vehicle_gate="unchanged"))
    write_json(output / "mission.json", mission)
    sources = probe_sources()
    backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
    agent = AssemblyAgent(tools, backend, mission, max_steps=max_steps, max_seconds=timeout,
                          finish_check=lambda: evidence(tools), finalize=False)
    started, error = time.monotonic(), None
    try:
        state = agent.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        path = tools.directory / "agent.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    trace = records(tools.directory / "agent-decisions.jsonl")
    checks = evidence(tools)
    path = tools.directory / "preview.json"
    preview = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    pixels = [dict(step=row["step"], sha256=item["sha256"]) for row in trace
        for item in json.loads(row["input_text"]).get("image_inputs", [])
        if preview and item["sha256"] == preview["image"]["sha256"]]
    archive = tools.directory / "model.io"
    content = lambda g: {key: value for key, value in g.items() if key not in {"revision", "source_sha256"}}
    roundtrip = archive.is_file() and content(IOArchive(archive).assembly.graph()) == content(tools.assembly.graph())
    errors = [row for row in trace if not row["observation"].get("ok")]
    recoveries = recovered_errors(trace)
    recovered = {row["failed_step"] for row in recoveries}
    unresolved = [row for row in errors if row["step"] not in recovered]
    successful = {row["decision"]["tool"] for row in trace if row["observation"].get("ok")}
    unchanged = execution_hash() == agent.contract["execution_hash"] and sources == probe_sources()
    passed = (not error and state.get("stop_reason") == "planner_finished" and checks["status"] == "pass"
        and roundtrip and bool(pixels) and unchanged and not unresolved
        and {"search_parts", "query_part", "build", "check", "motion", "linkage_motion",
             "preview", "export", "finish"} <= successful)
    result = dict(schema="autonomous-linkage-probe.v1", live_planner=True, planner=backend.name,
        seconds=time.monotonic() - started, stop_reason=state.get("stop_reason"), error=error,
        planner_linkage_diagnostic_pass=bool(passed), input_hash=agent.input_hash, contract=agent.contract,
        probe_sources=sources, execution_unchanged=unchanged,
        planner_actions=Counter(row["decision"]["tool"] for row in trace), planner_errors=errors,
        recovered_planner_errors=recoveries, unrecovered_planner_errors=unresolved,
        evidence=checks, bom=tools.assembly.bom(), archive_roundtrip=roundtrip, pixel_feedback=pixels,
        model_io_sha256=digest(archive) if archive.is_file() else None, preview=preview,
        native_files=tools.catalog.library.dependency_record(),
        acceptance=dict(vehicle_completion_ready=False, appearance="unverified", studio="unverified",
                        physical="unverified", ordinary_motion=checks["ordinary_motion_status"]),
        scope="Live selection from an empty scene; closed-linkage diagnostic, not whole-car acceptance")
    write_json(output / "report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--max-steps", type=int, default=140)
    args = parser.parse_args()
    report = run(args.output, args.native, model=args.model, timeout=args.timeout, max_steps=args.max_steps)
    print(json.dumps({key: report[key] for key in (
        "planner_linkage_diagnostic_pass", "stop_reason", "seconds", "planner_actions")}))
    raise SystemExit(0 if report["planner_linkage_diagnostic_pass"] else 1)
