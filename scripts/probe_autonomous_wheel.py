"""Evaluate description-based part selection and native supported-wheel assembly."""
import argparse
from collections import Counter
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
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def records(path):
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines() if row.strip()] if path.is_file() else []


def native_role(catalog, part):
    """Infer wheel roles from the actual library, never planner labels."""
    with catalog.library.resolve(part).open(encoding="utf-8-sig", errors="replace") as stream:
        description = stream.readline().strip().removeprefix("0 ")
    words = set(re.findall(r"[a-z]+", description.casefold()))
    if words.intersection({"tyre", "tire"}):
        role = "tyre"
    elif "wheel" in words and not words.intersection({"steering", "holder"}):
        role = "rim"
    elif "axle" in words and any(port.semantic in {"axle", "axle_guard"}
            for port in catalog.ports(part)):
        role = "axle"
    else:
        role = None
    return role, description


def selection_evidence(trace, parts):
    """Require a description search and verified query before each part is added."""
    searched, queried, introduced, evidence = {}, {}, set(), {}
    for step, row in enumerate(trace):
        if not row.get("ok"):
            continue
        tool, result = row["tool"], row.get("result", {})
        if tool == "search_parts":
            query = row["arguments"]["query"].casefold()
            words = re.findall(r"[^\W_]+", query)
            for item in result["parts"]:
                name = item["part"]
                if (re.search(r"[a-z]{3,}", query) and Path(name).stem.casefold() not in words
                        and name.casefold() not in query):
                    searched.setdefault(name, dict(step=step, query=row["arguments"]["query"]))
        elif tool == "query_part" and result.get("status") == "pass":
            queried[result["part"]] = step
        if tool not in {"build", "edit", "attach"}:
            continue
        operations = row["arguments"].get("operations", [row["arguments"]])
        for operation in operations:
            name = operation.get("part")
            if name not in parts or name in introduced:
                continue
            introduced.add(name)
            selected = name in searched and name in queried and searched[name]["step"] < queried[name]
            evidence[name] = dict(search=searched.get(name), query_step=queried.get(name), added_step=step,
                status="pass" if selected else "fail")
    issues = [name for name in sorted(parts) if evidence.get(name, {}).get("status") != "pass"]
    return dict(status="fail" if issues else "pass", missing_prior_selection=issues, parts=evidence,
        scope="Observed description search and native query before placement; not unseen-part generalization")


def wheel_evidence(tools, support):
    graph = tools.assembly.graph()
    nodes = {node["id"]: node for node in graph["instances"]}
    issues, roles, movement = [], {role: [] for role in ("rim", "tyre", "axle")}, {}
    if support["id"] not in nodes or semantic_node(nodes[support["id"]]) != semantic_node(support):
        issues.append("fixed_support_changed")
    reports = {}
    for label, filename in (("check", "checks.json"), ("motion", "motion.json")):
        path = tools.directory / filename
        reports[label] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if reports[label].get("status") != "pass" or not check_current(graph, reports[label]):
            issues.append(label + "_missing_failed_or_stale")
    joints = list(graph["joints"].items())
    if len(joints) != 1 or joints[0][1].get("purpose") != "rolling":
        issues.append("requires_one_rolling_joint")
    descriptions = {}
    new_parts = [node for node in nodes.values() if node["kind"] == "part" and node["id"] != support["id"]]
    if not 3 <= len(new_parts) <= 11:
        issues.append("requires_three_to_eleven_selected_parts")
    for node in new_parts:
        try:
            role, description = native_role(tools.catalog, node["part"])
        except (OSError, ValueError) as exc:
            issues.append("missing_native_part_evidence:" + node["part"])
            descriptions[node["part"]] = str(exc)
            continue
        descriptions[node["part"]] = description
        if role is not None:
            roles[role].append(node["id"])
    if any(len(values) != 1 for values in roles.values()):
        issues.append("requires_one_native_rim_tyre_and_axle")
    selection = selection_evidence(records(tools.directory / "trajectory.jsonl"), {n["part"] for n in new_parts})
    if selection["status"] != "pass":
        issues.append("selection_not_observed_before_placement")
    if issues:
        return dict(status="fail", issues=issues, roles=roles, descriptions=descriptions, selection=selection,
            physical="unverified")
    name, joint = joints[0]
    moving = lambda identity: identity == name or identity.startswith(name + "/")
    rotating = [identity for values in roles.values() for identity in values]
    if not all(moving(identity) for identity in rotating) or moving(support["id"]):
        issues.append("wheel_parts_must_share_moving_subtree_with_support_outside")
    edges = {frozenset((row["a"], row["b"])) for row in reports["check"]["connections"]}
    for a, b in ((roles["rim"][0], roles["tyre"][0]), (roles["axle"][0], roles["rim"][0]),
                 (roles["axle"][0], support["id"])):
        if frozenset((a, b)) not in edges:
            issues.append("required_native_contact_missing")
    sweep = next((row for row in reports["motion"]["joints"] if row["instance"] == name), {})
    values = sorted({row["value"] for row in sweep.get("poses", [])})
    if len(values) < 9 or values[-1] - values[0] < 360 - 1e-8:
        issues.append("requires_nine_poses_across_360_degrees")
    for value in values:
        posed = {node["id"]: node for node in posed_graph(graph, {name: value})["instances"]}
        if not np.allclose(posed[support["id"]]["world"], support["world"]):
            issues.append("support_moved_during_motion")
        for identity in rotating:
            triangles, _ = tools.catalog.library.triangles(nodes[identity]["part"])
            vertices = triangles.reshape(-1, 3)
            delta = np.asarray(posed[identity]["world"]) - np.asarray(nodes[identity]["world"])
            displacement = np.linalg.norm(vertices @ delta[:3, :3].T + delta[:3, 3], axis=1).max()
            movement[identity] = max(movement.get(identity, 0), float(displacement))
    if any(movement.get(identity, 0) <= 1 for identity in rotating):
        issues.append("actual_wheel_geometry_did_not_rotate")
    return dict(status="fail" if issues else "pass", issues=sorted(set(issues)), roles=roles,
        descriptions=descriptions, selection=selection, poses=len(values),
        maximum_vertex_displacement_ldu=movement, physical="unverified")


def run(output, native, *, model=None, timeout=1200):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve previous evidence")
    tools = AssemblyTools(output / "assembly", native)
    seeded = tools.call("build", dict(revision=0, operations=[dict(op="add", part="3700.dat",
        color=0, alias="support", matrix=np.eye(4).tolist())]))
    if not seeded["ok"]:
        raise ValueError(seeded)
    support = next(node for node in tools.assembly.graph()["instances"] if node["kind"] == "part")
    tools.protected_state = dict(instances={support["id"]: support}, joints={}, bindings={}, mounts=[])
    mission = dict(request=dict(goal="Build one compact passenger-car rolling wheel on the fixed native support. "
        "Choose a compatible tyre, wheel rim and axle yourself from search_parts description searches. "
        "No moving-part IDs, placements or counts are supplied. Query each selected part for exact native "
        "bounds, connectors and validation before adding it. Keep the support unchanged. Put tyre, rim "
        "and axle in one moving module, mount at the actual observed contact and articulate purpose rolling "
        "over 360 degrees. The support has a round bearing hole; keyed axle sockets must rotate with the axle. "
        "Use black tyre and gray rim. Keep standard native dimensions. Limit the completed module to twelve "
        "parts including the support. Run check and motion(samples=9), preview and inspect actual images, "
        "export and explicitly finish. This is a selection/mechanism trial, not appearance or physical acceptance. "
        "Read needed build operation signatures at guide:/build_operations/<op>. Use build and observed ports; "
        "aliases are scoped to one build call. A successful build must not be repeated. "
        "search_parts uses English description tokens and returns at most 50 candidates; narrow the description "
        "or dimensions when needed. Do not read every candidate; query plausible interfaces and assemble.",
        fixed_support=support), native_parts={support["part"]: tools.tool_query_part(support["part"])},
        module_task=dict(id="autonomous_supported_wheel", motion_samples=9, span_degrees=360))
    write_json(output / "mission.json", mission)
    probe_hash = digest(Path(__file__))
    backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
    agent = AssemblyAgent(tools, backend, mission, max_steps=48, max_seconds=timeout,
        finish_check=lambda: wheel_evidence(tools, support))
    started = time.monotonic()
    error = None
    try:
        state = agent.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        state = json.loads((tools.directory / "agent.json").read_text(encoding="utf-8"))
    trace = records(tools.directory / "agent-decisions.jsonl")
    actions = Counter(row["decision"]["tool"] for row in trace)
    evidence = wheel_evidence(tools, support)
    preview_path = tools.directory / "preview.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8")) if preview_path.is_file() else None
    pixels = [dict(step=row["step"], sha256=image["sha256"]) for row in trace
        for image in json.loads(row["input_text"]).get("image_inputs", [])
        if preview and image["sha256"] == preview["image"]["sha256"]]
    archive = tools.directory / "model.io"
    roundtrip = False
    if archive.is_file():
        def content(graph):
            return {key: value for key, value in graph.items() if key not in {"revision", "source_sha256"}}
        roundtrip = content(IOArchive(archive).assembly.graph()) == content(tools.assembly.graph())
    successful = {row["decision"]["tool"] for row in trace if row["observation"].get("ok")}
    unchanged = execution_hash() == agent.contract["execution_hash"] and digest(Path(__file__)) == probe_hash
    passed = (state["stop_reason"] == "planner_finished" and evidence["status"] == "pass" and roundtrip
        and bool(pixels) and unchanged and {"search_parts", "query_part", "build", "check", "motion", "preview", "export", "finish"} <= successful)
    report = dict(schema="autonomous-wheel-probe.v1", live_planner=True, planner=backend.name,
        seconds=time.monotonic() - started, stop_reason=state["stop_reason"], error=error,
        module_loop_pass=passed, input_hash=agent.input_hash, contract=agent.contract,
        probe_sha256=probe_hash, execution_unchanged=unchanged, planner_actions=actions,
        planner_errors=[row for row in trace if not row["observation"].get("ok")], wheel=evidence,
        bom=tools.assembly.bom(), archive_roundtrip=roundtrip, pixel_feedback=pixels,
        model_io_sha256=digest(archive) if archive.is_file() else None, preview=preview,
        native_files=tools.catalog.library.dependency_record(),
        acceptance=dict(car=False, appearance="unverified", studio="unverified", physical="unverified"),
        scope="One description-selected native rolling wheel on a supplied fixed bearing; no whole-car claim")
    write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    result = run(args.output, args.native, model=args.model, timeout=args.timeout)
    print(json.dumps({key: result[key] for key in ("module_loop_pass", "stop_reason", "seconds", "planner_actions")}))
    if not result["module_loop_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
