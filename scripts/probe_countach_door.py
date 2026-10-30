"""Measure a description-selected Countach scissor-door mechanism on a fixed mount."""
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
from car2lego3d.assembly_materials import palette, rgba
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.assembly_mounts import semantic_node
from car2lego3d.assembly_requirements import descendants, vehicle_contract
from car2lego3d.assembly_schedule import module_check
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json
from scripts.probe_autonomous_wheel import records, selection_evidence
from scripts.probe_connector_module import recovered_errors


def native_check_passed(report):
    gates = report.get("gates", {})
    native = {"parts", "transforms", "connections", "collisions", "digital_assembly"}
    excluded = {"appearance", "studio", "physical", "wheel_layout"}
    return (report.get("status") in {"pass", "unverified"}
        and all(gates.get(key) == "pass" for key in native)
        and gates.get("wheel_layout", "pass") in {"pass", "pending"}
        and all(value == "pass" for key, value in gates.items() if key not in excluded))


def native_motion_passed(report):
    joints = report.get("joints", [])
    poses = [pose for joint in joints for pose in joint.get("poses", [])]
    if (not joints or any(joint.get("range_issues") or not joint.get("poses") for joint in joints)
            or not poses or any(pose.get("status") != "pass" for pose in poses)
            or any(not row.get("poses") or any(pose.get("status") != "pass" for pose in row["poses"])
                   for row in report.get("combined", []))
            or report.get("unknown_joint_purposes")):
        return False
    if report.get("status") == "pass":
        return True
    # Preserve the full vehicle report; absent wheels do not measure this door's motion.
    missing = [row for row in report.get("mechanism_response", {}).values() if row.get("status") != "pass"]
    return (report.get("status") == "fail" and bool(missing)
        and all(row.get("wheels") and all(wheel.get("reason") == "wheel_not_installed"
            for wheel in row["wheels"]) for row in missing))


def evidence(tools, protected, *, selection_start=0):
    """This local diagnostic cannot complete a vehicle or its candidate comparison."""
    graph = tools.assembly.graph()
    nodes = {node["id"]: node for node in graph["instances"]}
    issues, reports = [], {}
    for key, original in protected.items():
        if key not in nodes or semantic_node(nodes[key]) != semantic_node(original):
            issues.append("fixed_mount_changed")
    for label, filename in (("check", "checks.json"), ("motion", "motion.json")):
        path = tools.directory / filename
        reports[label] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        passed = native_check_passed(reports[label]) if label == "check" else native_motion_passed(reports[label])
        if not passed or not check_current(graph, reports[label]):
            issues.append(label + "_missing_failed_or_stale")
    parts = {key: node for key, node in nodes.items() if node["kind"] == "part" and key not in protected}
    selected = selection_evidence(records(tools.directory / "trajectory.jsonl")[selection_start:],
                                  {node["part"] for node in parts.values()})
    if not parts or selected["status"] != "pass":
        issues.append("selection_not_observed_before_placement")
    binding = tools.module_bindings.get("door_left", {})
    measured = module_check(tools, "door_left", compare=False)
    if measured["status"] != "pass":
        issues.append("door_module_not_mechanically_passed")
    joints = [key for key, joint in graph["joints"].items() if joint.get("purpose") == "door"]
    if len(graph["joints"]) != 1 or len(joints) != 1:
        issues.append("requires_one_door_joint")
    summary = dict(selection=selected, selection_trace_start=selection_start, module_measurement=measured,
        host_check_status=reports["check"].get("status"), host_motion_status=reports["motion"].get("status"),
        structural_comparison="unverified", semantic_roles="unverified", appearance="unverified",
        physical_retention="unverified", load_capacity="unverified", vehicle_completion_ready=False)
    if issues:
        return dict(status="fail", issues=sorted(set(issues)), **summary)

    root = joints[0]
    joint = graph["joints"][root]
    members = descendants(graph, [root])
    roles = binding["roles"]
    if nodes[root]["kind"] != "module" or not set(parts) <= members or set(protected) & members:
        issues.append("selected_parts_must_share_door_submodel_with_fixed_mount_outside")
    parent = nodes[root]["parent"]
    world = np.eye(4) if parent == "root" else np.asarray(nodes[parent]["world"])
    axis = world[:3, :3] @ joint["axis"]
    low, high = joint["limits"]
    if joint.get("type", "revolute") != "revolute" or abs(axis[2]) < .9999:
        issues.append("scissor_door_requires_native_transverse_revolute_axis")
    if low > 0 or high < 0 or max(abs(low), abs(high)) < 90 - 1e-8:
        issues.append("door_must_open_ninety_degrees_from_neutral")
    colors = palette(tools.catalog_root)
    for key in roles["panel"]:
        if nodes[key]["color"] != 15:
            issues.append("countach_exterior_panel_must_be_white")
    for key in roles["glass"]:
        color = rgba(nodes[key]["color"], colors)
        if color is None or not 0 < color[3] < 255:
            issues.append("glass_requires_visible_native_transparency")
    sweep = next((row for row in reports["motion"].get("joints", []) if row["instance"] == root), {})
    values = {row.get("value") for row in sweep.get("poses", []) if row.get("status") == "pass"}
    expected = {float(value) for value in np.linspace(low, high, 9)}
    if not expected <= values:
        issues.append("requires_nine_distinct_passing_poses_covering_joint_limits")
    endpoint = high if abs(high) >= abs(low) else low
    values = sorted(value for value in values if isinstance(value, (int, float)))
    measurements = {}
    for role in ("panel", "glass"):
        for key in roles[role]:
            vertices = np.asarray(tools.catalog.library.triangles(nodes[key]["part"])[0]).reshape(-1, 3)
            if not len(vertices) or not np.isfinite(vertices).all():
                issues.append("missing_native_surface:" + key)
                continue
            neutral = np.asarray(nodes[key]["world"])
            maximum, upward = 0., None
            for value in values:
                posed = {node["id"]: node for node in posed_graph(graph, {root: value})["instances"]}
                if any(not np.allclose(posed[p]["world"], nodes[p]["world"], atol=1e-8, rtol=0)
                       for p in protected):
                    issues.append("fixed_mount_moved_during_opening")
                delta = np.asarray(posed[key]["world"]) - neutral
                displacement = vertices @ delta[:3, :3].T + delta[:3, 3]
                maximum = max(maximum, float(np.linalg.norm(displacement, axis=1).max()))
                if np.isclose(value, endpoint, atol=1e-8, rtol=0):
                    upward = float(-displacement[:, 1].mean())
            if maximum <= 1 or upward is None or upward <= 1:
                issues.append(role + "_native_geometry_must_rise_with_door:" + key)
            measurements[key] = dict(role=role, maximum_vertex_displacement_ldu=maximum,
                                     endpoint_mean_upward_displacement_ldu=upward)
    return dict(status="fail" if issues else "pass", issues=sorted(set(issues)), **summary,
                door=root, joint_limits=joint["limits"], tested_poses=len(values), measurements=measurements)


def target_context(inputs):
    """Use the existing Blender semantic source; never feed reference IO geometry."""
    from car2lego3d.vehicle_assembly_trials import scene_index
    inputs = Path(inputs).resolve()
    request_path, semantic_path = inputs / "request.json", inputs / "semantic.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if "countach" not in request.get("model", "").casefold():
        raise ValueError("This diagnostic requires the existing Countach request")
    index = scene_index(json.loads(semantic_path.read_text(encoding="utf-8")))
    roots = [key for key, value in index["nodes"].items()
             if value.get("role") == "door_assembly" and "left" in key.casefold()]
    if len(roots) != 1:
        raise ValueError("Require one left-door semantic assembly")
    keys, queue = set(), list(roots)
    while queue:
        key = queue.pop()
        if key not in keys:
            keys.add(key)
            queue.extend(index["nodes"][key].get("children", []))
    context = dict(request=request, semantic_source_sha256=digest(semantic_path),
        nodes={key: index["nodes"][key] for key in sorted(keys)},
        coordinates="Source is Blender metres, Z up; assembly is LDraw LDU, Y down, Z transverse.",
        sizing="About 1:12 is advisory detail budget; do not scale native parts or certify exact dimensions.")
    sources = {str(path): digest(path) for path in (request_path, semantic_path)}
    return request, context, sources


def probe_sources():
    folder = Path(__file__).parent
    return {str(folder / name): digest(folder / name) for name in (
        "probe_countach_door.py", "probe_autonomous_wheel.py", "probe_connector_module.py")}


def run(output, native, inputs, *, model=None, timeout=1800, max_steps=100):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve earlier door evidence")
    request, context, input_sources = target_context(inputs)
    tools = AssemblyTools(output / "assembly", native)
    seeded = tools.call("build", dict(revision=0, operations=[
        dict(op="module", name="fixed_door_mount.ldr", alias="mount", matrix=np.eye(4).tolist()),
        dict(op="add", part="3700.dat", parent="@mount", color=0, matrix=np.eye(4).tolist())]))
    if not seeded["ok"]:
        raise ValueError(seeded)
    protected = {node["id"]: deepcopy(node) for node in tools.assembly.graph()["instances"]}
    selection_start = len(records(tools.directory / "trajectory.jsonl"))
    tools.protected_state = dict(instances=protected, joints={}, bindings={}, mounts=[])
    contract = vehicle_contract(request, context["semantic_source_sha256"])
    # This request explicitly asks for moving glass, even if the baseline permits an open frame.
    contract["modules"]["door_left"]["roles"] = ["panel", "glass", "hinge"]
    mission = dict(request=dict(data=request), vehicle_contract=contract, door_target=context,
        module_task=dict(id="door_left", diagnostic="countach_scissor_door", motion_samples=9,
            goal="Build one white Countach left scissor door with visible transparent glass on the supplied "
            "fixed native bearing. Select every moving part using English description searches and native "
            "queries before placement. Read door_target for the existing Blender semantic door and request. "
            "Keep the supplied mount unchanged and outside the moving door submodel. Build a substantial "
            "white door panel, upper window/frame and native hinge or axle mechanism; X goes front to rear, "
            "Y down, Z transverse. The door extends toward +X from its front pivot; glass is above its panel. "
            "The native transverse bearing allows a scissor opening: move the panel and glass UP together "
            "through 90 degrees from neutral. Choose the sign of limits from the actual articulate axis. "
            "Use unscaled native parts, actual contacts, attach/snap/articulate; no supplied moving part IDs. "
            "Bind door_left to distinct actual panel/glass/hinge role parts in one moving submodel. Other "
            "vehicle modules remain absent; do not invent them or claim vehicle completion. Draft "
            "strict=false is allowed while building, followed by check and repair. Run check and "
            "motion(samples=9), keep all contacts and no collisions, then module_progress for this "
            "diagnostic's module_task result. The full vehicle check can remain unverified for absent "
            "wheels, and the full motion report can fail for absent steering wheels; do not invent those "
            "modules. Every native door pose must still pass. Preview and inspect current pixels, export "
            "current revision, finish. "
            "This measures one mechanism on a supplied mount, not cabin integration, three-candidate "
            "comparison, appearance, Studio, physical retention, loading or full vehicle acceptance."))
    write_json(output / "mission.json", mission)
    sources = probe_sources()
    backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
    agent = AssemblyAgent(tools, backend, mission, max_steps=max_steps, max_seconds=timeout,
                          finish_check=lambda: evidence(tools, protected, selection_start=selection_start), finalize=False)
    started, error = time.monotonic(), None
    try:
        state = agent.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        path = tools.directory / "agent.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    trace = records(tools.directory / "agent-decisions.jsonl")
    checks = evidence(tools, protected, selection_start=selection_start)
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
    unchanged = (execution_hash() == agent.contract["execution_hash"] and sources == probe_sources()
                 and all(digest(Path(path)) == expected for path, expected in input_sources.items()))
    passed = (not error and state.get("stop_reason") == "planner_finished" and checks["status"] == "pass"
        and roundtrip and bool(pixels) and unchanged and not unresolved
        and {"search_parts", "query_part", "build", "bind_module", "check", "motion",
             "preview", "export", "finish"} <= successful)
    result = dict(schema="countach-door-probe.v1", live_planner=True, planner=backend.name,
        seconds=time.monotonic() - started, stop_reason=state.get("stop_reason"), error=error,
        planner_door_diagnostic_pass=bool(passed), input_hash=agent.input_hash, contract=agent.contract,
        probe_sources=sources, input_sources=input_sources, execution_unchanged=unchanged,
        planner_actions=Counter(row["decision"]["tool"] for row in trace), planner_errors=errors,
        recovered_planner_errors=recoveries, unrecovered_planner_errors=unresolved,
        evidence=checks, bom=tools.assembly.bom(), archive_roundtrip=roundtrip, pixel_feedback=pixels,
        model_io_sha256=digest(archive) if archive.is_file() else None, preview=preview,
        native_files=tools.catalog.library.dependency_record(),
        acceptance=dict(vehicle_completion_ready=False, structural_comparison="unverified",
                        appearance="unverified", studio="unverified", physical="unverified"),
        scope="One Countach door mechanism on a supplied mount; no cabin or vehicle acceptance")
    write_json(output / "report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--inputs", type=Path, default=Path(
        "runs/four-vehicle-acceptance-20261018/countach-v4/shared/inputs"))
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-steps", type=int, default=100)
    args = parser.parse_args()
    report = run(args.output, args.native, args.inputs, model=args.model,
                 timeout=args.timeout, max_steps=args.max_steps)
    print(json.dumps({key: report[key] for key in (
        "planner_door_diagnostic_pass", "stop_reason", "seconds", "planner_actions")}))
    raise SystemExit(0 if report["planner_door_diagnostic_pass"] else 1)
