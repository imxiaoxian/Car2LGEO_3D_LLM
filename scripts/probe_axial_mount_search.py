"""Compare native seating and bounded axial repair on one damaged rolling chassis."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import time

import numpy as np

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner, execution_hash, wire_json
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_mounts import protection
from car2lego3d.assembly_requirements import identity
from car2lego3d.assembly_search import placement_operations
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json
from scripts.probe_connector_module import recovered_errors
from scripts.probe_rolling_chassis import PARTS, chassis_evidence, demonstration_operations


SIDES = {"fl": ("front", "left"), "fr": ("front", "right"),
         "rl": ("rear", "left"), "rr": ("rear", "right")}


def call(tools, name, arguments):
    response = tools.call(name, arguments)
    if not response["ok"]:
        raise ValueError(f"{name}: {response.get('error')}")
    return response["result"]


def seed(tools, side):
    ids = call(tools, "build", dict(revision=0, operations=demonstration_operations()))["aliases"]
    tools.module_bindings = {"chassis": dict(roots=[ids["chassis"]], roles={})}
    for prefix, (axle, position) in SIDES.items():
        tools.module_bindings[f"wheel_{axle}_{position}"] = dict(
            roots=[ids[prefix]], roles=dict(tyre=[ids[prefix + "tyre"]]))
    tools.vehicle_contract = dict(modules={key: {} for key in tools.module_bindings})
    baseline = call(tools, "check", {})
    if baseline["status"] != "pass":
        raise ValueError("Reference chassis failed native checks")
    shift = np.eye(4)
    shift[2, 3] = 40 if side.endswith("l") else -40
    call(tools, "edit", dict(revision=tools.assembly.revision,
        operations=placement_operations(tools.assembly.graph(), ids[side], shift)))
    damaged = call(tools, "check", {})
    if not damaged["collisions"]:
        raise ValueError("Perturbation did not reproduce the inboard-wheel collision")
    frozen = {key: binding for key, binding in tools.module_bindings.items()
              if ids[side] not in binding["roots"]}
    tools.protected_state = protection(tools.assembly.graph(), frozen)
    return ids, baseline, damaged


def repair_evidence(tools, initial, moving):
    graph = tools.assembly.graph()
    before = {row["id"]: row for row in initial["instances"]}
    after = {row["id"]: row for row in graph["instances"]}
    subtree = {key for key in before if key == moving or key.startswith(moving + "/")}
    fixed = set(before) - subtree
    issues = []
    if set(before) != set(after):
        issues.append("instance_set_changed")
    if any(after.get(key) != before[key] for key in fixed):
        issues.append("fixed_frame_or_other_wheels_changed")
    if any({k: v for k, v in after.get(key, {}).items() if k != "world"} !=
           {k: v for k, v in before[key].items() if k != "world"} for key in subtree - {moving}):
        issues.append("wheel_internal_geometry_changed")
    if {k: v for k, v in after.get(moving, {}).items() if k not in {"local", "world"}} != {
            k: v for k, v in before[moving].items() if k not in {"local", "world"}}:
        issues.append("moving_root_identity_changed")
    if {k: v for k, v in graph["joints"].get(moving, {}).items() if k not in {"origin", "axis"}} != {
            k: v for k, v in initial["joints"][moving].items() if k not in {"origin", "axis"}}:
        issues.append("rolling_joint_contract_changed")
    if {k: v for k, v in graph["joints"].items() if k != moving} != {
            k: v for k, v in initial["joints"].items() if k != moving}:
        issues.append("other_joints_changed")
    chassis = chassis_evidence(tools)
    if chassis["status"] != "pass":
        issues.append("chassis_evidence_failed")
    return dict(status="fail" if issues else "pass", issues=issues, chassis=chassis,
                physical="unverified")


def run(output, native, *, side="fl", live=False, model=None, timeout=1200):
    output, native = Path(output).resolve(), Path(native).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve earlier repair evidence")
    if side not in SIDES:
        raise ValueError("Unknown wheel position")
    tools = AssemblyTools(output / "assembly", native)
    ids, baseline, damaged = seed(tools, side)
    initial = deepcopy(tools.assembly.graph())
    state_hash = digest(tools.directory / "state.json")
    args = dict(revision=initial["revision"], moving=ids[side], targets=[ids[side + "bearing"]],
        limit=1, max_port_pairs=1, max_evaluations=33,
        source_ports=[dict(instance=ids[side + "axle"], port="0")],
        target_ports=[dict(instance=ids[side + "bearing"], port="2")])
    native_only = call(tools, "search_mounts", {**args, "max_axial_samples": 1})
    sampled = call(tools, "search_mounts", args)
    readonly = tools.assembly.graph() == initial and digest(tools.directory / "state.json") == state_hash
    comparison = dict(initial_graph_sha256=identity(initial), search_readonly=readonly,
        baseline=baseline, damaged=damaged, native_only=native_only, sampled=sampled)
    write_json(output / "comparison.json", comparison)
    if native_only["proposals"] or not sampled["proposals"] or not readonly:
        raise ValueError("Native-only/sampled comparison failed; inspect comparison.json")
    mission = dict(request=dict(goal=
        "Repair the existing inboard wheel mounting on this seeded four-wheel chassis. "
        "Inspect actual native ports, use search_mounts for bounded mounting proposals, and replay "
        "a proposal's build_arguments at its base revision. Move only the assigned wheel subtree, "
        "preserving its internal part transforms and the fixed frame plus the other three wheels. "
        "All four tyres must retain a common ground plane, axle alignment and vehicle centreline. "
        "Keep the rolling joint axis/pivot consistent. Run check and motion(samples=9), preview "
        "and inspect actual pixels, export current revision, then finish. Do not add/remove/replace "
        "parts or change colours. This is a seeded fixed-part repair diagnostic, not autonomous "
        "chassis creation, physical retention, load capacity, whole-car or appearance acceptance.",
        moving=ids[side], targets=[ids[side + "bearing"]]),
        repair_scope=dict(wheel=ids[side], axle=ids[side + "axle"], bearing=ids[side + "bearing"]),
        vehicle_contract=tools.vehicle_contract,
        module_task=dict(id="wheel_" + "_".join(SIDES[side]), motion_samples=9))
    if live:
        backend = CodexPlanner(output / "planner", model=model, timeout=timeout)
    else:
        class Demonstrator:
            name = "executed_axial_repair_demonstration"

            def propose(self, payload, step):
                if step == 0:
                    name, arguments = "search_mounts", args
                elif step == 1:
                    found = tools.context_resources["observations"]["0"]["result"]
                    name, arguments = "build", found["proposals"][0]["build_arguments"]
                else:
                    name = ("check", "motion", "preview", "export", "finish")[step - 2]
                    arguments = dict(samples=9) if name == "motion" else {}
                return dict(tool=name, arguments_json=wire_json(arguments), reason="Execute seeded repair diagnostic")
        backend = Demonstrator()
    probe_hash = digest(Path(__file__))
    started = time.monotonic()
    agent = AssemblyAgent(tools, backend, mission, max_steps=48, max_seconds=timeout,
        finish_check=lambda: repair_evidence(tools, initial, ids[side]))
    state = agent.run()
    trace = [json.loads(row) for row in (tools.directory / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    errors = [row for row in trace if not row["observation"].get("ok")]
    recoveries = recovered_errors(trace)
    recovered = {row["failed_step"] for row in recoveries}
    successful = {row["decision"]["tool"] for row in trace if row["observation"].get("ok")}
    proposals, replayed = [], []
    for row in trace:
        if not row["observation"].get("ok"):
            continue
        if row["decision"]["tool"] == "search_mounts":
            proposals.extend(row["observation"]["result"]["proposals"])
        elif row["decision"]["tool"] == "build":
            arguments = json.loads(row["decision"]["arguments_json"])
            replayed.extend(proposal for proposal in proposals if proposal["build_arguments"] == arguments)
    archive = tools.directory / "model.io"
    roundtrip = False
    if archive.is_file():
        restored = IOArchive(archive).assembly.graph()
        graph = tools.assembly.graph()
        # Reload provenance names the exported archive; all assembly data must match.
        roundtrip = all(restored[key] == value for key, value in graph.items() if key != "source_sha256")
    preview_path = tools.directory / "preview.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8")) if preview_path.is_file() else None
    pixels = [dict(step=row["step"], tool=row["decision"]["tool"], sha256=image["sha256"])
        for row in trace for image in json.loads(row["input_text"]).get("image_inputs", [])
        if preview and image["sha256"] == preview["image"]["sha256"]]
    evidence = repair_evidence(tools, initial, ids[side])
    record = dict(schema="axial-mount-repair-probe.v1", live_planner=live, planner=backend.name,
        side=side, seconds=time.monotonic() - started, stop_reason=state["stop_reason"],
        input_hash=agent.input_hash, execution_hash=agent.contract["execution_hash"],
        execution_unchanged=execution_hash() == agent.contract["execution_hash"],
        protocol_hash=agent.contract["protocol_hash"], skill_hash=agent.contract["skill_hash"],
        planner_context=agent.contract["planner_context"], probe_sha256=probe_hash,
        probe_unchanged=digest(Path(__file__)) == probe_hash,
        native_files=tools.catalog.library.dependency_record(),
        comparison=dict(file="comparison.json", sha256=digest(output / "comparison.json"),
            search_readonly=readonly, native_only_proposals=len(native_only["proposals"]),
            sampled_proposals=len(sampled["proposals"]), statistics=sampled["statistics"]),
        planner_actions=Counter(row["decision"]["tool"] for row in trace), planner_errors=errors,
        recovered_planner_errors=recoveries,
        unrecovered_planner_errors=[row for row in errors if row["step"] not in recovered],
        replayed_proposals=replayed, digital=state.get("digital_status"), archive_roundtrip=roundtrip,
        model_io_sha256=digest(archive) if archive.is_file() else None, preview=preview, pixel_feedback=pixels,
        repair=evidence, acceptance=dict(car=False, appearance="unverified", studio="unverified", physical="unverified"),
        scope="One seeded native four-wheel chassis repair; fixed part types/counts and one perturbed wheel. "
            "No autonomous selection, vehicle, physical retention, load capacity or general-object acceptance.")
    passed = (evidence["status"] == "pass" and record["digital"] == "pass" and roundtrip and readonly
        and state["stop_reason"] == "planner_finished" and record["execution_unchanged"] and record["probe_unchanged"]
        and {"search_mounts", "build", "check", "motion", "preview", "export", "finish"} <= successful
        and any(p["seating"]["offset_ldu"] != 0 for p in replayed) and (not live or bool(pixels)))
    record["first_pass_module_loop_pass"] = passed and not errors
    record["module_loop_pass"] = passed and not record["unrecovered_planner_errors"]
    write_json(output / "report.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--side", choices=SIDES, default="fl")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    record = run(args.output, args.native, side=args.side, live=args.live, model=args.model, timeout=args.timeout)
    print(json.dumps({key: record[key] for key in ("module_loop_pass", "planner_actions", "digital", "seconds")}))
    if not record["module_loop_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
