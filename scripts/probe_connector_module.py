"""Execute the real native wheel module loop with deterministic or live proposals."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

import numpy as np

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, execution_hash, wire_json
from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def recovered_errors(trace):
    """Keep failed decisions visible while identifying a later successful retry."""
    recoveries = []
    for index, row in enumerate(trace):
        if row["observation"].get("ok"):
            continue
        recovery = next((later for later in trace[index + 1:]
            if later["decision"].get("tool") == row["decision"].get("tool")
            and later["observation"].get("ok")), None)
        if recovery:
            recoveries.append(dict(failed_step=row["step"], recovered_step=recovery["step"],
                tool=row["decision"]["tool"]))
    return recoveries


def demonstration_operations(*, rolling=False):
    operations = [dict(op="module", name="wheel.ldr", alias="wheel", matrix=np.eye(4).tolist()),
        dict(op="add", part="56145.dat", color=71, parent="@wheel", alias="rim", matrix=np.eye(4).tolist()),
        dict(op="attach", part="61481.dat", color=0, parent="@wheel", alias="tyre",
            source_port="0", target="@rim", target_port="2"),
        dict(op="attach", part="18651.dat", color=0, parent="@wheel", alias="axle",
            source_port="1", target="@rim", target_port="0")]
    if rolling:
        operations.insert(0, dict(op="add", part="3700.dat", color=0,
            alias="bearing", matrix=np.eye(4).tolist()))
        operations.extend([
            dict(op="snap", moving="@wheel", source="@axle", source_port="0",
                target="@bearing", target_port="2"),
            dict(op="articulate", instance="@wheel", source="@axle", source_port="0",
                target="@bearing", target_port="2", limits=[0, 360], purpose="rolling")])
    return operations


def rolling_evidence(tools):
    """Require actual wheel geometry to rotate while its native support stays fixed."""
    graph = tools.assembly.graph()
    nodes = {n["id"]: n for n in graph["instances"]}
    issues, movement = [], {}

    def read(name):
        path = tools.directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    checks, motion = read("checks.json"), read("motion.json")
    for label, report in (("check", checks), ("motion", motion)):
        if report.get("status") != "pass" or not check_current(graph, report):
            issues.append(label + "_missing_failed_or_stale")
    parts = {part: [n for n in nodes.values() if n["kind"] == "part" and n["part"] == part]
        for part in ("56145.dat", "61481.dat", "18651.dat", "3700.dat")}
    if any(len(rows) != 1 for rows in parts.values()) or sum(n["kind"] == "part" for n in nodes.values()) != 4:
        issues.append("requires_exactly_one_rim_tyre_axle_and_support")
    joints = [(name, joint) for name, joint in graph["joints"].items() if joint.get("purpose") == "rolling"]
    if len(joints) != 1 or len(graph["joints"]) != 1:
        issues.append("requires_one_rolling_joint")
    if issues:
        return dict(status="fail", issues=issues, physical="unverified")
    name, joint = joints[0]
    inside = lambda identity: identity == name or identity.startswith(name + "/")
    rotating = [parts[part][0] for part in ("56145.dat", "61481.dat", "18651.dat")]
    support = parts["3700.dat"][0]
    if any(not inside(node["id"]) for node in rotating) or inside(support["id"]):
        issues.append("wheel_parts_must_move_together_with_support_outside")
    sweep = next((row for row in motion["joints"] if row["instance"] == name), {})
    values = sorted({row["value"] for row in sweep.get("poses", [])})
    if len(values) < 9 or values[-1] - values[0] < 360 - 1e-8:
        issues.append("requires_nine_distinct_poses_across_360_degrees")
    for value in values:
        posed = {n["id"]: n for n in posed_graph(graph, {name: value})["instances"]}
        if not np.allclose(posed[support["id"]]["world"], support["world"]):
            issues.append("support_moved")
        for node in rotating:
            geometry, _ = tools.catalog.library.triangles(node["part"])
            vertices = np.asarray(geometry).reshape(-1, 3)
            delta = np.asarray(posed[node["id"]]["world"]) - np.asarray(node["world"])
            displacement = np.linalg.norm(vertices @ delta[:3, :3].T + delta[:3, 3], axis=1).max()
            movement[node["id"]] = max(movement.get(node["id"], 0), float(displacement))
    if any(movement.get(node["id"], 0) <= 1 for node in rotating):
        issues.append("actual_rim_tyre_and_axle_geometry_must_rotate")
    return dict(status="fail" if issues else "pass", issues=sorted(set(issues)),
        poses=len(values), span_degrees=values[-1] - values[0] if values else 0,
        maximum_vertex_displacement_ldu=movement, support_instance=support["id"],
        physical="unverified")


def run(output, native, *, live=False, rolling=False, model=None, timeout=900):
    output, native = Path(output).resolve(), Path(native).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve previous module evidence")
    tools = AssemblyTools(output / "assembly", native)
    parts = {part: tools.tool_query_part(part) for part in ("56145.dat", "61481.dat", "18651.dat")}
    if rolling:
        parts["3700.dat"] = tools.tool_query_part("3700.dat")
    mission = dict(request=dict(goal="Build one native wheel module from the provided rim, tyre and axle. "
        "Use exact observed connectors. Build all three parts, check, preview and inspect actual views, "
        "export and explicitly finish. No whole-car, appearance or physical acceptance claim."),
        native_parts=parts)
    mission["request"]["parts"] = list(parts)
    if rolling:
        mission["request"].update(goal="Build a supported rolling wheel from the provided rim, tyre, axle and "
            "3700 bearing. All four actual parts must be connected. Put rim, tyre and axle inside one moving "
            "module; keep the bearing outside. Use observed native connectors to mount the whole wheel "
            "subtree on the bearing. Use articulate at the verified mounting contact, purpose rolling and "
            "a 360-degree range; the host derives the parent-local axis and pivot. Run check and motion with "
            "at least nine samples, preview and inspect actual images, export and finish. Colour tyre black "
            "(0), rim light bluish gray (71). Preserve standard-part dimensions. This is a mechanical wheel "
            "module trial, not a complete car or physical acceptance.")
        mission["module_task"] = dict(id="supported_rolling_wheel", motion_samples=9, span_degrees=360)
    if live:
        backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
        mission["request"]["goal"] += (
            " Use guide:/build_operations to read signatures. build accepts add/module/attach/snap/articulate. "
            "Earlier aliases resolve as @name; strict defaults true. An initial identity matrix is allowed "
            "for the grounded rim; host attach computes other translations. "
            "Use native_parts:/<part.dat> to read exact bounds and ports. "
            "@aliases exist only within one build call. After build succeeds, inspect its real IDs and "
            "call check, any required motion, preview, export and finish; never rebuild an already successful module. "
            "After querying enough evidence, assemble rather than repeatedly reading the same page.")
    else:
        operations = demonstration_operations(rolling=rolling)
        class Demonstrator:
            name = "executed_connector_demonstration"
            actions = [("build", dict(revision=0, operations=operations)), ("check", {}),
                *([("motion", dict(samples=9))] if rolling else []),
                ("preview", {}), ("export", {}), ("finish", {})]

            def propose(self, payload, step):
                tool, arguments = self.actions[step]
                return dict(tool=tool, arguments_json=wire_json(arguments), reason="Execute connector module diagnostic")
        backend = Demonstrator()
    started = time.monotonic()
    agent = AssemblyAgent(tools, backend, mission, max_steps=32 if rolling else 24, max_seconds=timeout,
        finish_check=(lambda: rolling_evidence(tools)) if rolling else None)
    state = agent.run()
    trace = [json.loads(row) for row in (tools.directory / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    graph = tools.assembly.graph()
    archive = tools.directory / "model.io"
    preview_path = tools.directory / "preview.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8")) if preview_path.is_file() else None
    pixel_feedback = [dict(step=row["step"], tool=row["decision"]["tool"], image_sha256=attachment["sha256"])
        for row in trace for attachment in json.loads(row["input_text"]).get("image_inputs", [])
        if preview and attachment["sha256"] == preview["image"]["sha256"]]
    record = dict(schema="connector-module-probe.v2", live_planner=live, planner=backend.name,
        input_hash=agent.input_hash, execution_hash=agent.contract["execution_hash"],
        execution_unchanged=execution_hash() == agent.contract["execution_hash"],
        protocol_hash=agent.contract["protocol_hash"], planner_context=agent.contract["planner_context"],
        native_parts=parts, native_files=tools.catalog.library.dependency_record(),
        seconds=time.monotonic() - started, stop_reason=state["stop_reason"],
        planner_actions=Counter(row["decision"]["tool"] for row in trace),
        planner_errors=[row for row in trace if not row["observation"].get("ok")],
        bom=tools.assembly.bom(), parts=sum(n["kind"] == "part" for n in graph["instances"]),
        digital=state.get("digital_status"),
        archive_roundtrip=archive.is_file() and IOArchive(archive).assembly.bom() == tools.assembly.bom(),
        model_io_sha256=digest(archive) if archive.is_file() else None,
        preview=preview, pixel_feedback=pixel_feedback,
        rolling=rolling_evidence(tools) if rolling else None,
        acceptance=dict(car=False, appearance="unverified", studio="unverified", physical="unverified"),
        scope="One supported rolling wheel module" if rolling else "One native wheel module",
        limitations="Supplied part types, connector placement chosen by live planner "
            "or explicit deterministic demonstrator. Not complete car or general-object generation.")
    recoveries = recovered_errors(trace)
    recovered_steps = {item["failed_step"] for item in recoveries}
    record["recovered_planner_errors"] = recoveries
    record["unrecovered_planner_errors"] = [row for row in record["planner_errors"]
        if row["step"] not in recovered_steps]
    successful_tools = {row["decision"]["tool"] for row in trace if row["observation"].get("ok")}
    lifecycle_pass = (record["parts"] == (4 if rolling else 3) and record["digital"] == "pass"
        and record["archive_roundtrip"] and state["stop_reason"] == "planner_finished"
        and {"build", "check", "preview", "export", "finish"} <= successful_tools
        and (not rolling or "motion" in successful_tools and record["rolling"]["status"] == "pass")
        and record["execution_unchanged"] and (not live or bool(pixel_feedback)))
    record["first_pass_module_loop_pass"] = lifecycle_pass and not record["planner_errors"]
    record["module_loop_pass"] = lifecycle_pass and not record["unrecovered_planner_errors"]
    write_json(output / "report.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--rolling", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    result = run(args.output, args.native, live=args.live, rolling=args.rolling, model=args.model, timeout=args.timeout)
    print(json.dumps({key: result[key] for key in ("module_loop_pass", "planner_actions", "parts", "digital", "seconds")}))
    if not result["module_loop_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
