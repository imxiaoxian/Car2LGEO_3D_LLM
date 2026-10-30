"""Measure a description-selected cabin installed on a protected linked chassis."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re
import time

import numpy as np

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, execution_hash
from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.assembly_mounts import semantic_node
from car2lego3d.assembly_requirements import descendants, vehicle_contract
from car2lego3d.assembly_schedule import module_check
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json
from scripts.probe_autonomous_wheel import records, selection_evidence
from scripts.probe_connector_module import recovered_errors
from scripts.probe_countach_door import native_check_passed, native_motion_passed


def contact_keys(rows):
    return {tuple(sorted(((row["a"], row["port_a"]), (row["b"], row["port_b"]))))
            for row in rows}


def linkage_issues(graph, report, profile):
    """Require fresh coverage of the original mechanism with the interior present."""
    issues = []
    driver, passive, rolling = (profile[key] for key in ("driver", "passive", "rolling"))
    if (report.get("status") != "pass" or report.get("driver") != driver
            or set(report.get("passive", [])) != set(passive)
            or set(report.get("rolling", [])) != set(rolling)):
        issues.append("original_linkage_not_passed")
    expected = {float(value) for value in np.linspace(*graph["joints"][driver]["limits"], 9)}
    poses = list(report.get("poses", []))
    if not expected <= {row.get("positions", {}).get(driver) for row in poses}:
        issues.append("requires_nine_original_linked_steering_poses")
    groups = report.get("combined", [])
    if len(groups) != len(rolling) or {row.get("rolling") for row in groups} != set(rolling):
        issues.append("requires_four_distinct_rolling_sweeps")
    for root in rolling:
        sweep = next((row.get("poses", []) for row in groups if row.get("rolling") == root), [])
        requested = {(a, float(b)) for a in expected
                     for b in np.linspace(*graph["joints"][root]["limits"], 9)}
        actual = {(row.get("positions", {}).get(driver), row.get("positions", {}).get(root)) for row in sweep}
        if not requested <= actual:
            issues.append("requires_324_original_rolling_combinations")
        poses.extend(sweep)
    if (not poses or any(row.get("status") != "pass" or row.get("lost_contacts")
                        or not native_check_passed(row.get("check", {})) for row in poses)):
        issues.append("native_linkage_pose_failed_or_missing")
    if not contact_keys(profile["contacts"]) <= contact_keys(report.get("contacts", [])):
        issues.append("original_native_contacts_missing")
    return issues, poses


def cabin_geometry(tools, graph, parts, roles, envelope):
    """Check limited native role and placement evidence, not vehicle appearance."""
    issues, surfaces, descriptions = [], {}, {}
    for key in {part for values in roles.values() for part in values}:
        if key not in parts:
            issues.append("cabin_roles_require_new_native_parts")
            continue
        node = parts[key]
        with tools.catalog.library.resolve(node["part"]).open(encoding="utf-8-sig", errors="replace") as stream:
            descriptions[key] = stream.readline().strip().removeprefix("0 ")
        vertices = np.asarray(tools.catalog.library.triangles(node["part"])[0]).reshape(-1, 3)
        if not len(vertices) or not np.isfinite(vertices).all():
            issues.append("missing_native_cabin_surface:" + key)
            continue
        world = np.asarray(node["world"])
        points = vertices @ world[:3, :3].T + world[:3, 3]
        low, high = points.min(0), points.max(0)
        surfaces[key] = dict(low=low.tolist(), high=high.tolist(), center=((low + high) / 2).tolist())
    seats, dash, wheels = (roles.get(key, []) for key in ("seats", "dashboard", "steering_wheel"))
    if len(seats) != 2 or len(wheels) != 1 or not dash:
        issues.append("requires_two_seats_dashboard_and_one_steering_wheel")
    for key in seats:
        if not re.search(r"\bseat\b", descriptions.get(key, ""), re.I):
            issues.append("seat_role_requires_native_seat:" + key)
    for key in wheels:
        if not re.search(r"\bsteering wheel\b", descriptions.get(key, ""), re.I):
            issues.append("steering_wheel_requires_native_steering_wheel:" + key)
    for key in dash:
        if not re.search(r"\b(slope|tile|panel)\b", descriptions.get(key, ""), re.I):
            issues.append("dashboard_requires_native_fascia_surface:" + key)
    if issues or any(key not in surfaces for key in seats + dash + wheels):
        return issues, dict(native_descriptions=descriptions, surfaces=surfaces)
    centres = np.asarray([surfaces[key]["center"] for key in seats])
    if not (centres[:, 2].min() < envelope["center_z"] < centres[:, 2].max()):
        issues.append("two_seats_must_straddle_chassis_centerline")
    for key in seats + dash + wheels:
        low, high = (np.asarray(surfaces[key][field]) for field in ("low", "high"))
        if (low[0] < envelope["front_x"] or high[0] > envelope["rear_x"]
                or low[2] < envelope["left_z"] or high[2] > envelope["right_z"]
                or high[1] > envelope["ground_y"]):
            issues.append("cabin_role_outside_axles_track_or_ground:" + key)
    for key in dash + wheels:
        if surfaces[key]["center"][0] >= centres[:, 0].min():
            issues.append("dashboard_and_wheel_must_be_ahead_of_both_seats")
    driver_z = surfaces[wheels[0]]["center"][2]
    if min(abs(driver_z - z) for z in centres[:, 2]) > 20:
        issues.append("steering_wheel_must_align_with_one_seat")
    width = max(surfaces[key]["high"][2] for key in dash) - min(surfaces[key]["low"][2] for key in dash)
    if width < 40:
        issues.append("dashboard_requires_forty_ldu_transverse_surface")
    return issues, dict(native_descriptions=descriptions, surfaces=surfaces,
                        dashboard_width_ldu=width, envelope=envelope)


def evidence(tools, seed, profile, envelope, *, selection_start=0):
    graph = tools.assembly.graph()
    nodes = {node["id"]: node for node in graph["instances"]}
    protected = {node["id"]: node for node in seed["instances"]}
    issues, reports = [], {}
    if (any(key not in nodes or semantic_node(nodes[key]) != semantic_node(node)
            for key, node in protected.items()) or graph["joints"] != seed["joints"]):
        issues.append("protected_chassis_or_joints_changed")
    for label, filename in (("check", "checks.json"), ("motion", "motion.json"),
                            ("linkage", "linkage-motion.json")):
        path = tools.directory / filename
        reports[label] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not reports[label] or not check_current(graph, reports[label]):
            issues.append(label + "_missing_or_stale")
    if not native_check_passed(reports["check"]):
        issues.append("native_static_check_not_passed")
    sweeps = reports["motion"].get("joints", [])
    coverage = (len(sweeps) == len(seed["joints"])
        and {row.get("instance") for row in sweeps} == set(seed["joints"])
        and all({float(value) for value in np.linspace(*seed["joints"][row["instance"]]["limits"], 9)}
                <= {pose.get("value") for pose in row.get("poses", [])} for row in sweeps))
    if not native_motion_passed(reports["motion"]) or not coverage:
        issues.append("native_ordinary_motion_not_passed")
    parts = {key: node for key, node in nodes.items() if node["kind"] == "part" and key not in protected}
    if not 4 <= len(parts) <= 40:
        issues.append("requires_four_to_forty_new_native_parts")
    selected = selection_evidence(records(tools.directory / "trajectory.jsonl")[selection_start:],
                                  {node["part"] for node in parts.values()})
    if not parts or selected["status"] != "pass":
        issues.append("new_parts_require_prior_description_selection")
    binding = tools.module_bindings.get("cabin", {})
    measured = module_check(tools, "cabin", compare=False)
    if measured["status"] != "pass":
        issues.append("installed_cabin_not_mechanically_passed")
    summary = dict(selection=selected, module_measurement=measured,
        host_check_status=reports["check"].get("status"), host_motion_status=reports["motion"].get("status"),
        structural_comparison="unverified", semantic_roles="unverified", appearance="unverified",
        steering_wheel_linkage="unverified", physical_retention="unverified", load_capacity="unverified",
        vehicle_completion_ready=False)
    if "protected_chassis_or_joints_changed" in issues:
        return dict(status="fail", issues=sorted(set(issues)), **summary)
    try:
        members = descendants(graph, binding.get("roots", []))
        if (not set(parts) <= members or set(protected) & members
                or any(nodes[root]["parent"] != "root" for root in binding["roots"])
                or any(node["id"] not in protected and node["id"] not in members for node in nodes.values())):
            issues.append("all_new_geometry_must_belong_to_independent_fixed_cabin")
    except ValueError:
        issues.append("cabin_binding_missing_or_invalid")
    extra, poses = linkage_issues(graph, reports["linkage"], profile)
    issues.extend(extra)
    if any(key not in parts for values in binding.get("roles", {}).values() for key in values):
        issues.append("cabin_roles_require_new_native_parts")
    geometry_issues, geometry = cabin_geometry(tools, graph, parts, binding.get("roles", {}), envelope)
    issues.extend(geometry_issues)
    # Static connectivity alone could mount a cabin onto a steering carrier.
    for row in poses:
        posed = {node["id"]: node for node in posed_graph(graph, row["positions"])["instances"]}
        if any(not np.allclose(posed[key]["world"], node["world"], atol=1e-8, rtol=0)
               for key, node in parts.items()):
            issues.append("cabin_geometry_moved_with_chassis_mechanism")
            break
    return dict(status="fail" if issues else "pass", issues=sorted(set(issues)), **summary,
                geometry=geometry, new_part_count=len(parts), linked_pose_count=len(reports["linkage"].get("poses", [])),
                rolling_pose_count=sum(len(row.get("poses", [])) for row in reports["linkage"].get("combined", [])))


def seed_context(source):
    """Import an actually passed native chassis, preserving all source artifacts."""
    source = Path(source).resolve()
    paths = [source / "report.json", source / "assembly/model.io", source / "assembly/state.json",
             source / "assembly/checks.json", source / "assembly/linkage-motion.json"]
    sources = {str(path): digest(path) for path in paths}
    report = json.loads(paths[0].read_text(encoding="utf-8"))
    if not report.get("planner_linkage_diagnostic_pass") or report.get("model_io_sha256") != sources[str(paths[1])]:
        raise ValueError("Require a passed autonomous chassis with the recorded IO hash")
    graph = IOArchive(paths[1]).assembly.graph()
    expected = json.loads(paths[2].read_text(encoding="utf-8"))
    from car2lego3d.assembly_graph import Assembly
    state_graph = Assembly(expected["text"], expected["source_sha256"])
    state_graph.joints, state_graph.revision = expected["joints"], expected["revision"]
    content = lambda value: {key: val for key, val in value.items() if key not in {"revision", "source_sha256"}}
    if content(graph) != content(state_graph.graph()):
        raise ValueError("Seed archive and checkpoint disagree")
    wheels = report["evidence"]["wheels"]
    centres = np.asarray([row["center"] for row in wheels])
    envelope = dict(front_x=float(centres[:, 0].min()), rear_x=float(centres[:, 0].max()),
        left_z=float(centres[:, 2].min()), right_z=float(centres[:, 2].max()),
        center_z=float((centres[:, 2].min() + centres[:, 2].max()) / 2),
        ground_y=float(max(row["ground_y_ldu"] for row in report["evidence"]["layout"]["wheels"].values())))
    linkage = json.loads(paths[4].read_text(encoding="utf-8"))
    profile = {key: linkage[key] for key in ("driver", "passive", "rolling", "contacts")}
    return paths[1], graph, profile, envelope, sources


def probe_sources():
    return {name: digest(Path(__file__).parent / name) for name in (
        "probe_cabin_integration.py", "probe_countach_door.py", "probe_autonomous_wheel.py", "probe_connector_module.py")}


def run(output, native, source, *, model=None, timeout=1800, max_steps=100):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve earlier cabin evidence")
    archive, seed, profile, envelope, inputs = seed_context(source)
    tools = AssemblyTools(output / "assembly", native)
    tools.import_file(archive)
    tools.protected_state = dict(instances={node["id"]: deepcopy(node) for node in seed["instances"]},
                                joints=deepcopy(seed["joints"]), bindings={}, mounts=[])
    selection_start = len(records(tools.directory / "trajectory.jsonl"))
    mission = dict(request=dict(goal="Install a compact two-seat passenger-car cabin on the supplied linked chassis."),
        vehicle_contract=vehicle_contract(dict(brand="Generic", model="two-seat integration diagnostic"), inputs[str(archive)]),
        chassis_context=dict(source_archive_sha256=inputs[str(archive)], protected=True, envelope_ldu=envelope,
                             linkage=profile, original_vehicle_bindings="absent"),
        module_task=dict(id="cabin", diagnostic="protected_linked_chassis_cabin", motion_samples=9,
            goal="Preserve EVERY existing chassis instance, transform, joint and limit exactly. Query the fixed frame "
            "and build ONE independent root cabin submodel connected to it with native contacts. Select EVERY NEW "
            "part type by English description search then successful query_part BEFORE placement, including types "
            "already present in the seed. Use actual native bounds and ports. Include exactly TWO native seat parts "
            "on opposite sides of the centreline, a substantial dashboard fascia using native slope/tile/panel parts "
            "spanning at least 40 LDU across Z, and one native steering wheel aligned within 20 LDU of a seat centre. "
            "Put the dashboard and steering wheel ahead of both seats. All role surfaces stay between the two axle "
            "planes, within wheel-centre track and above ground. X front-to-rear, Y down, Z transverse; see "
            "chassis_context.envelope_ldu. Keep native dimensions and at most 40 new parts. Use actual seats, not "
            "renamed bricks. Build every new part in the fixed cabin subtree; no added joints. Use unscaled parts, "
            "attach/snap/build, and keep all original chassis contacts. Bind cabin to actual distinct seats, dashboard "
            "and steering_wheel parts. Draft strict=false is permitted during construction, followed by repairs. "
            "Run check and ordinary motion(samples=9), then linkage_motion(samples=9) with the ORIGINAL driver, "
            "passive and four rolling IDs in chassis_context: require all 9 steering poses and 324 individually "
            "rolling combinations with the cabin installed. Every original contact stays engaged; the cabin stays "
            "fixed and clears the wheels and tie bar. Original wheel role bindings are absent; preserve full "
            "vehicle reports while this task checks native geometry locally. Read module_progress diagnostics, "
            "preview, inspect actual pixels, export current revision and finish. Do not install doors or bodywork "
            "in this probe. This measures one integrated cabin only; three-candidate comparison, semantic role "
            "review, steering-wheel mechanical linkage, appearance, Studio, retention/load, physical assembly "
            "and whole-car completion remain unverified."))
    write_json(output / "mission.json", mission)
    sources = probe_sources()
    backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
    agent = AssemblyAgent(tools, backend, mission, max_steps=max_steps, max_seconds=timeout,
        finish_check=lambda: evidence(tools, seed, profile, envelope, selection_start=selection_start), finalize=False)
    started, error = time.monotonic(), None
    try:
        state = agent.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        path = tools.directory / "agent.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    trace = records(tools.directory / "agent-decisions.jsonl")
    checks = evidence(tools, seed, profile, envelope, selection_start=selection_start)
    path = tools.directory / "preview.json"
    preview = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    pixels = [dict(step=row["step"], sha256=item["sha256"]) for row in trace
        for item in json.loads(row["input_text"]).get("image_inputs", [])
        if preview and item["sha256"] == preview["image"]["sha256"]]
    exported = tools.directory / "model.io"
    content = lambda g: {key: value for key, value in g.items() if key not in {"revision", "source_sha256"}}
    roundtrip = exported.is_file() and content(IOArchive(exported).assembly.graph()) == content(tools.assembly.graph())
    errors = [row for row in trace if not row["observation"].get("ok")]
    recoveries = recovered_errors(trace)
    recovered = {row["failed_step"] for row in recoveries}
    unresolved = [row for row in errors if row["step"] not in recovered]
    successful = {row["decision"]["tool"] for row in trace if row["observation"].get("ok")}
    unchanged = (execution_hash() == agent.contract["execution_hash"] and sources == probe_sources()
                 and all(digest(Path(path)) == expected for path, expected in inputs.items()))
    passed = (not error and state.get("stop_reason") == "planner_finished" and checks["status"] == "pass"
        and roundtrip and bool(pixels) and unchanged and not unresolved
        and {"search_parts", "query_part", "build", "bind_module", "check", "motion", "linkage_motion",
             "preview", "export", "finish"} <= successful)
    result = dict(schema="cabin-integration-probe.v1", live_planner=True, planner=backend.name,
        seconds=time.monotonic() - started, stop_reason=state.get("stop_reason"), error=error,
        planner_cabin_integration_pass=bool(passed), input_hash=agent.input_hash, contract=agent.contract,
        probe_sources=sources, input_sources=inputs, execution_and_seed_unchanged=unchanged,
        planner_actions=Counter(row["decision"]["tool"] for row in trace), planner_errors=errors,
        recovered_planner_errors=recoveries, unrecovered_planner_errors=unresolved,
        evidence=checks, bom=tools.assembly.bom(), archive_roundtrip=roundtrip, pixel_feedback=pixels,
        model_io_sha256=digest(exported) if exported.is_file() else None, preview=preview,
        native_files=tools.catalog.library.dependency_record(),
        acceptance=dict(vehicle_completion_ready=False, structural_comparison="unverified", semantic_roles="unverified",
            steering_wheel_linkage="unverified", appearance="unverified", studio="unverified", physical="unverified"),
        scope="One new fixed cabin on a supplied autonomous linked chassis; not empty-scene or whole-car acceptance")
    write_json(output / "report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("runs/car-modules-20261030/autonomous-linkage-live-v2"))
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-steps", type=int, default=100)
    args = parser.parse_args()
    report = run(args.output, args.native, args.source, model=args.model, timeout=args.timeout, max_steps=args.max_steps)
    print(json.dumps({key: report[key] for key in ("planner_cabin_integration_pass", "stop_reason", "seconds", "planner_actions")}))
    raise SystemExit(0 if report["planner_cabin_integration_pass"] else 1)
