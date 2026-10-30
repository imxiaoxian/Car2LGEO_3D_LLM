"""Select and build a native steering carrier with a nested rolling wheel."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import time

import numpy as np

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, execution_hash
from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.assembly_mounts import semantic_node
from car2lego3d.assembly_requirements import descendants
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json
from scripts.probe_autonomous_wheel import native_role, records, selection_evidence
from scripts.probe_connector_module import recovered_errors


def evidence(tools, support):
    graph = tools.assembly.graph()
    nodes = {node["id"]: node for node in graph["instances"]}
    issues, reports = [], {}
    if support["id"] not in nodes or semantic_node(nodes[support["id"]]) != semantic_node(support):
        issues.append("fixed_support_changed")
    for label, filename in (("check", "checks.json"), ("motion", "motion.json")):
        path = tools.directory / filename
        report = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        reports[label] = report
        if report.get("status") != "pass" or not check_current(graph, report):
            issues.append(label + "_missing_failed_or_stale")
    parts = {key: node for key, node in nodes.items() if node["kind"] == "part" and key != support["id"]}
    if not 5 <= len(parts) <= 19:
        issues.append("requires_five_to_nineteen_selected_parts")
    selection = selection_evidence(records(tools.directory / "trajectory.jsonl"),
                                   {node["part"] for node in parts.values()})
    if selection["status"] != "pass":
        issues.append("selection_not_observed_before_placement")
    roles, descriptions = {"rim": [], "tyre": [], "axle": []}, {}
    for key, node in parts.items():
        try:
            role, description = native_role(tools.catalog, node["part"])
            descriptions[node["part"]] = description
            if role:
                roles[role].append(key)
        except (OSError, ValueError) as error:
            issues.append("missing_native_evidence:" + node["part"])
            descriptions[node["part"]] = str(error)
    if len(roles["rim"]) != 1 or len(roles["tyre"]) != 1:
        issues.append("requires_one_native_rim_and_tyre")
    joints = {purpose: [key for key, joint in graph["joints"].items() if joint.get("purpose") == purpose]
              for purpose in ("steering", "rolling")}
    if len(graph["joints"]) != 2 or any(len(value) != 1 for value in joints.values()):
        issues.append("requires_one_steering_and_one_rolling_joint")
    summary = dict(roles=roles, descriptions=descriptions, selection=selection,
                   physical_retention="unverified", load_capacity="unverified", assembly_order="unverified")
    if issues:
        return dict(status="fail", issues=sorted(set(issues)), **summary)

    steering, rolling = joints["steering"][0], joints["rolling"][0]
    steering_members, rolling_members = descendants(graph, [steering]), descendants(graph, [rolling])
    if nodes[steering]["kind"] != "module" or nodes[rolling]["kind"] != "module":
        issues.append("joint_roots_must_be_submodels")
    if rolling not in steering_members or steering == rolling:
        issues.append("rolling_wheel_must_descend_from_steering_carrier")
    if support["id"] in steering_members or not set(parts) <= steering_members:
        issues.append("new_parts_must_belong_to_steering_subtree_with_fixed_support_outside")
    if not set(roles["rim"] + roles["tyre"]) <= rolling_members:
        issues.append("rim_and_tyre_must_share_rolling_subtree")
    if not any(key in rolling_members for key in roles["axle"]):
        issues.append("rolling_subtree_requires_native_axle")
    if not any(key in steering_members - rolling_members for key in roles["axle"]):
        issues.append("steering_carrier_requires_distinct_native_kingpin_axle")
    limits = graph["joints"][steering]["limits"]
    if limits[0] > -30 or limits[1] < 30:
        issues.append("steering_must_cover_minus_thirty_to_plus_thirty_degrees")
    axes = {}
    for purpose, key in (("steering", steering), ("rolling", rolling)):
        joint = graph["joints"][key]
        parent = nodes[key]["parent"]
        world = np.eye(4) if parent == "root" else np.asarray(nodes[parent]["world"])
        axes[purpose] = world[:3, :3] @ joint["axis"]
        if joint.get("type", "revolute") != "revolute":
            issues.append(purpose + "_requires_revolute_joint")
    if abs(axes["steering"][1]) < .9999 or abs(axes["rolling"][2]) < .9999:
        issues.append("requires_vertical_steering_axis_and_neutral_transverse_rolling_axis")
    connections = reports["check"].get("connections", [])
    edges = {frozenset((row["a"], row["b"])) for row in connections}
    if frozenset(roles["rim"] + roles["tyre"]) not in edges:
        issues.append("rim_and_tyre_require_native_contact")
    if not any(frozenset((roles["rim"][0], axle)) in edges
               for axle in roles["axle"] if axle in rolling_members):
        issues.append("rim_and_rolling_axle_require_native_contact")
    if not any(support["id"] in (row["a"], row["b"]) and
               (row["b"] if row["a"] == support["id"] else row["a"]) in steering_members
               for row in connections):
        issues.append("steering_carrier_requires_native_support_contact")
    combined = next((row for row in reports["motion"].get("combined", [])
                     if set(row["instances"]) == {steering, rolling}), {})
    poses = combined.get("poses", [])
    expected = {(float(a), float(b)) for a in np.linspace(*limits, 9)
                for b in np.linspace(*graph["joints"][rolling]["limits"], 9)}
    actual = {(p["positions"].get(steering), p["positions"].get(rolling)) for p in poses}
    if (not expected <= actual or len(poses) < 81 or any(p["status"] != "pass" for p in poses)):
        issues.append("requires_passing_combined_steering_and_rolling_grid")

    tyre = roles["tyre"][0]
    vertices, _ = tools.catalog.library.triangles(nodes[tyre]["part"])
    vertices = np.asarray(vertices).reshape(-1, 3)
    displacement = {}
    for purpose, root in (("steering", steering), ("rolling", rolling)):
        sweep = next((row for row in reports["motion"].get("joints", []) if row["instance"] == root), {})
        values = {row["value"] for row in sweep.get("poses", [])}
        if len(values) < 9:
            issues.append(purpose + "_requires_nine_distinct_poses")
        maximum = 0.
        for value in values:
            posed = {node["id"]: node for node in posed_graph(graph, {root: value})["instances"]}
            delta = np.asarray(posed[tyre]["world"]) - np.asarray(nodes[tyre]["world"])
            maximum = max(maximum, float(np.linalg.norm(
                vertices @ delta[:3, :3].T + delta[:3, 3], axis=1).max()))
            if not np.allclose(posed[support["id"]]["world"], support["world"], atol=1e-8):
                issues.append("fixed_support_moved_during_motion")
        displacement[purpose] = maximum
        if maximum <= 1:
            issues.append("actual_tyre_geometry_did_not_respond_to_" + purpose)
    return dict(status="fail" if issues else "pass", issues=sorted(set(issues)), **summary,
                steering=steering, rolling=rolling, combined_poses=len(poses),
                maximum_tyre_vertex_displacement_ldu=displacement)


def run(output, native, *, model=None, timeout=1500):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve earlier steering evidence")
    tools = AssemblyTools(output / "assembly", native)
    matrix = np.eye(4)
    matrix[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    seeded = tools.call("build", dict(revision=0, operations=[dict(op="add", part="3700.dat",
                         color=0, matrix=matrix.tolist())]))
    if not seeded["ok"]:
        raise ValueError(seeded)
    support = deepcopy(next(node for node in tools.assembly.graph()["instances"] if node["kind"] == "part"))
    tools.protected_state = dict(instances={support["id"]: support}, joints={}, bindings={}, mounts=[])
    mission = dict(request=dict(goal=
        "Build one steerable passenger-car wheel on the supplied fixed vertical bearing. "
        "Search English part descriptions and query native bounds/ports before adding every selected type. "
        "Choose the kingpin axle, knuckle/carrier with a perpendicular round bearing, rolling axle, rim "
        "and tyre yourself. No moving part IDs or positions are supplied. Keep the fixed support unchanged. "
        "Put the steering kingpin and carrier in a steering submodel with a revolute joint about the "
        "support's actual vertical round hole, covering [-30,30] degrees. Put the wheel rim, tyre and "
        "horizontal axle in a separate rolling submodel NESTED INSIDE the steering carrier, with a "
        "revolute rolling joint [0,360] at a real round bearing contact. Keyed axle sockets rotate with "
        "their shaft. X is vehicle longitudinal, Y down and Z transverse. All selected parts must be in "
        "the steering subtree. Use 6 to 20 parts total, black tyre and gray rim, unscaled native geometry. "
        "Use actual connector contacts with attach/snap/articulate and search_mounts for uncertain "
        "shaft insertion. strict=false permits drafts, followed by check and repair. Run check and "
        "motion(samples=9); motion checks 81 combined steering/rolling states, so rolling must remain "
        "connected and collision-free WHILE steered. Preview and inspect the actual image, export and "
        "explicitly finish. Read the host module_task evidence via module_progress to repair specific "
        "failures. This evaluates a single seeded steering/rolling module, not autonomous chassis "
        "creation, steering linkage/Ackermann, axle retention, load capacity, physical assembly order, "
        "whole-car appearance or physical acceptance. Aliases exist only within one build call; reuse "
        "returned real IDs and never repeat successful placements."),
        module_task=dict(id="steerable_wheel", motion_samples=9, steering_limits=[-30, 30],
                         rolling_limits=[0, 360], minimum_combined_poses=81))
    write_json(output / "mission.json", mission)
    sources = {str(path): digest(path) for path in (Path(__file__),
               Path(__file__).with_name("probe_autonomous_wheel.py"),
               Path(__file__).with_name("probe_connector_module.py"))}
    backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
    agent = AssemblyAgent(tools, backend, mission, max_steps=80, max_seconds=timeout,
                          finish_check=lambda: evidence(tools, support))
    started, error = time.monotonic(), None
    try:
        state = agent.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        state_path = tools.directory / "agent.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    trace = records(tools.directory / "agent-decisions.jsonl")
    checks = evidence(tools, support)
    preview_path = tools.directory / "preview.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8")) if preview_path.is_file() else None
    pixels = [dict(step=row["step"], sha256=image["sha256"]) for row in trace
              for image in json.loads(row["input_text"]).get("image_inputs", [])
              if preview and image["sha256"] == preview["image"]["sha256"]]
    archive = tools.directory / "model.io"
    content = lambda g: {key: value for key, value in g.items() if key not in {"revision", "source_sha256"}}
    roundtrip = archive.is_file() and content(IOArchive(archive).assembly.graph()) == content(tools.assembly.graph())
    errors = [row for row in trace if not row["observation"].get("ok")]
    recoveries = recovered_errors(trace)
    recovered = {row["failed_step"] for row in recoveries}
    unresolved = [row for row in errors if row["step"] not in recovered]
    successful = {row["decision"]["tool"] for row in trace if row["observation"].get("ok")}
    unchanged = execution_hash() == agent.contract["execution_hash"] and all(
        digest(Path(path)) == expected for path, expected in sources.items())
    passed = (not error and state.get("stop_reason") == "planner_finished" and checks["status"] == "pass"
              and roundtrip and bool(pixels) and unchanged and not unresolved and
              {"search_parts", "query_part", "build", "check", "motion", "preview", "export", "finish"} <= successful)
    result = dict(schema="steerable-wheel-probe.v1", live_planner=True, planner=backend.name,
        seconds=time.monotonic()-started, stop_reason=state.get("stop_reason"), error=error,
        module_loop_pass=passed, first_pass_module_loop_pass=passed and not errors,
        input_hash=agent.input_hash, contract=agent.contract, probe_sources=sources, execution_unchanged=unchanged,
        planner_actions=Counter(row["decision"]["tool"] for row in trace), planner_errors=errors,
        recovered_planner_errors=recoveries, unrecovered_planner_errors=unresolved,
        evidence=checks, bom=tools.assembly.bom(), archive_roundtrip=roundtrip, pixel_feedback=pixels,
        model_io_sha256=digest(archive) if archive.is_file() else None, preview=preview,
        native_files=tools.catalog.library.dependency_record(),
        acceptance=dict(car=False, appearance="unverified", studio="unverified", physical="unverified"),
        scope="One description-selected steerable wheel on a supplied support; steering linkage and chassis unverified")
    write_json(output / "report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=1500)
    args = parser.parse_args()
    report = run(args.output, args.native, model=args.model, timeout=args.timeout)
    print(json.dumps({key: report[key] for key in ("module_loop_pass", "stop_reason", "seconds", "planner_actions")}))
    if not report["module_loop_pass"]:
        raise SystemExit(1)
