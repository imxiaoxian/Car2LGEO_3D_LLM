"""Evaluate a description-selected four-wheel chassis from an empty scene."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, execution_hash
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json
from scripts.probe_autonomous_wheel import records, selection_evidence
from scripts.probe_connector_module import recovered_errors
from scripts.probe_rolling_chassis import chassis_evidence


def evidence(tools):
    chassis = chassis_evidence(tools, autonomous=True)
    selected = selection_evidence(records(tools.directory / "trajectory.jsonl"),
        {node["part"] for node in tools.assembly.graph()["instances"] if node["kind"] == "part"})
    issues = list(chassis["issues"])
    if selected["status"] != "pass":
        issues.append("selection_not_observed_before_placement")
    return dict(status="fail" if issues else "pass", issues=issues, chassis=chassis, selection=selected)


def probe_sources():
    folder = Path(__file__).parent
    return {name: digest(folder / name) for name in (
        "probe_autonomous_chassis.py", "probe_autonomous_wheel.py",
        "probe_rolling_chassis.py", "probe_connector_module.py")}


def run(output, native, *, model=None, timeout=1500):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve previous chassis evidence")
    tools = AssemblyTools(output / "assembly", native)
    mission = dict(request=dict(goal=
        "Build a connected compact passenger-car four-wheel rolling chassis from the empty scene. "
        "Select all native parts yourself using English description searches: fixed frame, four distinct "
        "bearing supports, and compatible rims, tyres and axles. No part IDs or placements are supplied. "
        "Query every selected part type for exact bounds, native ports and validation before first placement. "
        "Keep standard native dimensions. Use 17 to 64 parts. Start with a simple rigid frame in a fixed "
        "submodel. Each of four independent moving wheel submodels must contain exactly one rim, tyre and "
        "axle with actual rim-tyre and rim-axle connections. Each axle must contact one distinct fixed bearing "
        "with a round pin socket or round hole, so keyed sockets rotate with the axle. All parts must connect "
        "with no collider interference. X is front-to-rear, Y down, Z left-to-right; align axle pairs, "
        "separate left and right wheels, share one centreline and ground plane, and keep the frame above ground. "
        "Build and validate one wheel, then reuse that subtree with clone when helpful. clone returns descendant "
        "IDs in clones[].instances and omits the source root joint: mount and articulate each copied root on "
        "its own support. Matrices are parent-local; orient connectors using observed ports. A draft build "
        "may use strict=false while combining modules, followed by check and repair. Use search_mounts if "
        "ordinary snap cannot find a collision-free axial seating. Keep tyres black and rims gray. "
        "Declare four independent rolling joints with articulate and limits [0,360]. Run check and "
        "motion(samples=9), preview and inspect actual pixels, export the current revision and explicitly "
        "finish. If finish is blocked, use its specific evidence to repair. This is a structural module "
        "trial, not a complete vehicle, steering, suspension, axle retention, load or appearance acceptance. "
        "Aliases are local to one build call; retain returned real IDs. A successful build must not be "
        "repeated. Narrow searches rather than reading all candidates. Read guide:/build_operations/<op> "
        "only for unresolved signatures; act using known parts, IDs and previous evidence."),
        module_task=dict(id="autonomous_four_wheel_chassis", motion_samples=9, span_degrees=360))
    write_json(output / "mission.json", mission)
    sources = probe_sources()
    backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
    agent = AssemblyAgent(tools, backend, mission, max_steps=80, max_seconds=timeout,
        finish_check=lambda: evidence(tools))
    started, error = time.monotonic(), None
    try:
        state = agent.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        state_path = tools.directory / "agent.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    trace = records(tools.directory / "agent-decisions.jsonl")
    trajectory = records(tools.directory / "trajectory.jsonl")
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
    checks = evidence(tools)
    errors = [row for row in trace if not row["observation"].get("ok")]
    recoveries = recovered_errors(trace)
    recovered = {row["failed_step"] for row in recoveries}
    unresolved = [row for row in errors if row["step"] not in recovered]
    successful = {row["decision"]["tool"] for row in trace if row["observation"].get("ok")}
    unchanged = execution_hash() == agent.contract["execution_hash"] and sources == probe_sources()
    passed = (not error and state.get("stop_reason") == "planner_finished" and checks["status"] == "pass"
        and roundtrip and bool(pixels) and unchanged and not unresolved
        and {"search_parts", "query_part", "build", "check", "motion", "preview", "export", "finish"} <= successful)
    result = dict(schema="autonomous-chassis-probe.v1", live_planner=True, planner=backend.name,
        seconds=time.monotonic() - started, stop_reason=state.get("stop_reason"), error=error,
        module_loop_pass=passed, first_pass_module_loop_pass=passed and not errors,
        input_hash=agent.input_hash, contract=agent.contract, probe_sources=sources, execution_unchanged=unchanged,
        planner_actions=Counter(row["decision"]["tool"] for row in trace), planner_errors=errors,
        recovered_planner_errors=recoveries, unrecovered_planner_errors=unresolved,
        cloned_subassemblies=sum(len(row.get("result", {}).get("clones", [])) for row in trajectory if row.get("ok")),
        evidence=checks, bom=tools.assembly.bom(), archive_roundtrip=roundtrip, pixel_feedback=pixels,
        model_io_sha256=digest(archive) if archive.is_file() else None, preview=preview,
        native_files=tools.catalog.library.dependency_record(),
        acceptance=dict(car=False, appearance="unverified", studio="unverified", physical="unverified"),
        scope="One description-selected native four-wheel chassis from an empty scene; no whole-car claim")
    write_json(output / "report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=1500)
    args = parser.parse_args()
    result = run(args.output, args.native, model=args.model, timeout=args.timeout)
    print(json.dumps({key: result[key] for key in (
        "module_loop_pass", "stop_reason", "seconds", "planner_actions", "cloned_subassemblies")}))
    if not result["module_loop_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
