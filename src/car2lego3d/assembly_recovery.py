"""Carry same-planner checkpoints into a new executor revision with fresh checks."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

from .assembly_requirements import binding_conflicts, identity
from .common import digest, write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def completed_history(source, schedule):
    """Recover completion intent lost by a migration that made no state edits."""
    tasks, history = deepcopy(schedule.get("tasks", {})), []
    seen = {source}
    while (source / "recovery.json").is_file():
        path = source / "recovery.json"
        record = read(path)
        previous = Path(record.get("source", "")).resolve()
        hashes = record.get("source_files", {})
        if (record.get("schema") != "assembly-revision-recovery.v1" or previous in seen
                or set(hashes) != {"state.json", "module-tasks.json"}
                or hashes["state.json"] != digest(source / "state.json")
                or any(not (previous / name).is_file() or digest(previous / name) != expected
                    for name, expected in hashes.items())):
            break
        old = read(previous / "module-tasks.json")
        if (old.get("planner") != schedule.get("planner")
                or any(old["contract"].get(key) != schedule["contract"].get(key)
                    for key in ("snapshot", "exclusions"))
                or any(old["contract"]["mission"].get(key) != schedule["contract"]["mission"].get(key)
                    for key in ("request", "semantic", "specifications", "references", "vehicle_contract", "detail_standard", "shape_evidence"))):
            break
        recovered = []
        for name, task in old.get("tasks", {}).items():
            if task.get("status") == "assembled" and tasks.get(name, {}).get("status") != "assembled":
                tasks[name] = deepcopy(task)
                recovered.append(name)
        history.append(dict(file=str(path), sha256=digest(path), source=str(previous),
            source_files=hashes, recovered_completion_intent=recovered))
        source = previous
        seen.add(source)
    return tasks, history


def record_launch(arguments, bound):
    from .assembly_agent import execution_hash
    values = {key: str(value.resolve()) if isinstance(value, Path) else value
        for key, value in arguments.items() if key not in {"checkpoint", "seed_from", "retry"}}
    write_json(Path(arguments["output"]) / "launch.json", dict(schema="assembly-launch.v1",
        arguments=values, bound=bound, execution_hash=execution_hash()))


def resume_arguments(directory, artifacts):
    """Resume only the exact recorded inputs, branching when executor code changed."""
    from uuid import uuid4
    from .assembly_agent import PLANNERS, execution_hash
    directory, artifacts = Path(directory).resolve(), Path(artifacts).resolve()
    if artifacts == directory or not artifacts.is_relative_to(directory):
        raise ValueError("Invalid assembly artifact directory")
    output = artifacts / "03-lego/agent"
    path = output / "launch.json"
    if not path.is_file():
        pending_path = output / "pending-recovery.json"
        if pending_path.is_file():
            pending = read(pending_path)
            source = Path(pending.get("source", "")).resolve()
            launch = source / "launch.json"
            if (pending.get("schema") != "assembly-pending-recovery.v1"
                    or source == output or not source.is_relative_to(directory)
                    or source.parts[-2:] != ("03-lego", "agent")
                    or not launch.is_file() or digest(launch) != pending.get("source_launch_sha256")
                    or not isinstance(pending.get("source_files"), dict)
                    or set(pending["source_files"]) != {"state.json", "module-tasks.json"}):
                raise ValueError("Invalid pending assembly recovery source")
            for name, expected in pending["source_files"].items():
                if (name not in {"state.json", "module-tasks.json"} or not (source / name).is_file()
                        or digest(source / name) != expected):
                    raise ValueError("Pending assembly recovery checkpoint changed")
            # Discard no evidence: fork again from the immutable, validated source.
            return resume_arguments(directory, source.parent.parent)
        raise ValueError("历史装配缺少完整启动记录，需迁移到新修订后恢复。")
    record = read(path)
    if record.get("schema") != "assembly-launch.v1":
        raise ValueError("Unsupported assembly launch record")
    args = dict(record["arguments"])
    allowed = {"request", "semantic", "specifications", "references", "output", "backend",
        "snapshot", "corpus_root", "native_root", "max_steps", "publish_artifacts", "context_profile", "shape_evidence"}
    if (set(args) - allowed or args.get("backend") not in PLANNERS
            or args.get("context_profile") not in {"production", "shared-text"}
            or type(args.get("max_steps")) is not int or not 1 <= args["max_steps"] <= 2000
            or not args.get("snapshot")):
        raise ValueError("Invalid assembly launch arguments")
    for name in ("request", "semantic", "specifications", "references", "output", "publish_artifacts"):
        args[name] = Path(args[name]).resolve()
        if not args[name].is_relative_to(directory):
            raise ValueError("Assembly inputs must remain inside their job")
    if args.get("shape_evidence"):
        args["shape_evidence"] = Path(args["shape_evidence"]).resolve()
        if not args["shape_evidence"].is_relative_to(directory):
            raise ValueError("Shape evidence must remain inside its job")
    if args["output"] != output or args["publish_artifacts"] != artifacts:
        raise ValueError("Assembly launch revision differs from the selected artifacts")
    bound = record.get("bound", {})
    if any(str(args[name]) not in bound for name in ("request", "semantic", "specifications", "references")):
        raise ValueError("Assembly launch has incomplete input hashes")
    for name, expected in bound.items():
        source = Path(name).resolve()
        if not source.is_relative_to(directory) or not source.is_file() or digest(source) != expected:
            raise ValueError("装配输入已改变或缺失，不能沿用旧检查点：" + source.name)
    if record["execution_hash"] != execution_hash():
        source_files = {name: digest(output / name) for name in ("state.json", "module-tasks.json")
            if (output / name).is_file()}
        if set(source_files) != {"state.json", "module-tasks.json"}:
            raise ValueError("Assembly recovery source has an incomplete checkpoint")
        target = directory / "revisions" / ("assembly-recovery-" + uuid4().hex) / "artifacts"
        for folder in ("00-recipe", "01-image", "02-mesh", "02-shape"):
            if (artifacts / folder).is_dir():
                shutil.copytree(artifacts / folder, target / folder)
        for name in ("semantic", "specifications", "references", "shape_evidence"):
            if not args.get(name):
                continue
            if args[name].is_relative_to(artifacts):
                destination = target / args[name].relative_to(artifacts)
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if args[name].is_dir():
                        shutil.copytree(args[name], destination)
                    else:
                        shutil.copyfile(args[name], destination)
                args[name] = destination
        destination = target / "03-lego/agent"
        write_json(destination / "pending-recovery.json", dict(schema="assembly-pending-recovery.v1",
            source=str(output), source_launch_sha256=digest(path), source_files=source_files))
        args.update(output=destination, publish_artifacts=target, seed_from=output)
    args["retry"] = True
    return args


def seed_checkpoint(tools, source, mission, planner, snapshot, request):
    from .assembly_graph import Assembly
    from .job_runtime import executing, owns_assembly_revision
    source, request = Path(source).resolve(), Path(request).resolve()
    if source == tools.directory or tools.directory.is_relative_to(source) or source.is_relative_to(tools.directory):
        raise ValueError("Recovery needs an independent revision directory")
    if (tools.directory / "state.json").exists():
        raise ValueError("Recovery destination already has state; resume it normally")
    runtime_path = request.parent / "runtime.json"
    if runtime_path.is_file():
        runtime = read(runtime_path)
        if executing(runtime) and not owns_assembly_revision(request.parent, tools.directory.parent.parent, runtime):
            raise ValueError("A live worker still owns this vehicle; do not seed a competing run")
    schedule, state = read(source / "module-tasks.json"), read(source / "state.json")
    contract = schedule["contract"]
    if schedule["planner"] != planner or contract["snapshot"] != snapshot or contract["exclusions"] != tools.exclusions:
        raise ValueError("Recovery planner, corpus or held-out exclusions differ")
    for key in ("request", "specifications", "semantic", "references", "vehicle_contract", "detail_standard", "shape_evidence"):
        if contract["mission"].get(key) != mission.get(key):
            raise ValueError("Recovery vehicle inputs changed: " + key)
    if state.get("packages") or state.get("source_archive"):
        raise ValueError("Checkpoint recovery currently supports native independent assemblies only")
    before = {name: digest(source / name) for name in ("state.json", "module-tasks.json")}
    completion_tasks, completion_history = completed_history(source, schedule)
    assembly = Assembly(state["text"], state["source_sha256"])
    assembly.revision, assembly.joints = state["revision"], state["joints"]
    tools.assembly, tools.module_bindings = assembly, deepcopy(state.get("module_bindings", {}))
    from .assembly_mounts import wheel_mount_issues
    from .assembly_requirements import descendants
    graph = assembly.graph()
    from .assembly_layout import wheel_layout
    layout_diagnostics = wheel_layout(graph, tools.catalog, mission["vehicle_contract"], tools.module_bindings)
    retired = []
    for name, binding in list(tools.module_bindings.items()):
        issues = wheel_mount_issues(graph, mission["vehicle_contract"], tools.module_bindings, name)
        if not issues:
            continue
        members = descendants(graph, binding["roots"])
        for other, value in tools.module_bindings.items():
            if other != name and (members.intersection(value["roots"])
                    or members.intersection(i for ids in value["roles"].values() for i in ids)):
                raise ValueError("Incompatible wheel overlaps another module; explicit migration required")
        retired.append(dict(module=name, binding=deepcopy(binding), reasons=issues,
            source_state_sha256=before["state.json"], geometry_preserved_in_source=True))
        assembly.transact(assembly.revision, [dict(op="remove", instance=root) for root in binding["roots"]])
        del tools.module_bindings[name]
    completed = {name: binding for name, binding in tools.module_bindings.items()
        if completion_tasks.get(name, {}).get("status") == "assembled"}
    quarantined = []
    for name, binding in list(tools.module_bindings.items()):
        if name in completed:
            continue
        conflicts = binding_conflicts(name, binding, completed)
        if conflicts:
            quarantined.append(dict(module=name, binding=deepcopy(binding), conflicts=conflicts,
                reason="unfinished_binding_reuses_completed_instances", geometry_preserved=True))
            del tools.module_bindings[name]
    if quarantined:
        tools.assembly.revision += 1
    tools.save()
    record = dict(schema="assembly-revision-recovery.v1", source=str(source), source_files=before,
        source_execution_hash=contract["execution_hash"], source_input_hash=schedule["input_hash"],
        planner=planner, snapshot=snapshot, source_revision=state["revision"],
        original_schedule=schedule, rechecked_candidates=[], carried_modules=[],
        completion_tasks=completion_tasks, completion_history=completion_history,
        retired_modules=retired,
        source_wheel_layout=layout_diagnostics,
        quarantined_bindings=quarantined, destination_revision=tools.assembly.revision,
        acceptance="unverified", original_results_preserved=True)
    write_json(tools.directory / "recovery.json", record)
    return record


def restore_safe_boundary(scheduler, recovery, measurements):
    """Withdraw unfinished trials only when every completed module is unchanged."""
    from types import SimpleNamespace
    from .assembly_graph import Assembly
    from .assembly_mounts import semantic_node
    from .assembly_schedule import module_check, module_signature
    tools = scheduler.tools
    tasks = recovery.get("completion_tasks", recovery["original_schedule"]["tasks"])
    completed = {name: task for name, task in tasks.items()
        if task.get("status") == "assembled" and name in measurements}
    failed = [name for name in completed if measurements[name]["status"] != "pass"]
    if not failed:
        return
    # A tyre-free snapshot cannot disprove a measured vehicle stance defect.
    if recovery.get("source_wheel_layout", {}).get("status") == "fail":
        recovery["safe_boundary"] = dict(status="unavailable",
            reason="source_vehicle_layout_requires_structural_repair",
            source_wheel_layout=recovery["source_wheel_layout"],
            failed_modules=failed, geometry_preserved=True)
        return
    signatures = {name: task.get("measurement", {}).get("signature") for name, task in completed.items()}
    if any(not value or module_signature(tools, name) != value for name, value in signatures.items()):
        recovery["safe_boundary"] = dict(status="unavailable", reason="completed_signature_missing_or_changed")
        return
    graph = tools.assembly.graph()
    nodes = {n["id"]: semantic_node(n) for n in graph["instances"]}
    options, rejected = [], []
    for name in failed:
        for path in sorted((tools.directory / "module-candidates" / name).glob("*/candidate.json")):
            record = read(path)
            state_path = path.parent / "state.json"
            if digest(state_path) != record["snapshot_sha256"]:
                raise ValueError("Recovery boundary snapshot changed")
            state = read(state_path)
            assembly = Assembly(state["text"], state["source_sha256"])
            assembly.revision, assembly.joints = state["revision"], state["joints"]
            trial = SimpleNamespace(assembly=assembly, module_bindings=state.get("module_bindings", {}))
            candidate = assembly.graph()
            unchanged = (not state.get("packages") and not state.get("source_archive")
                and all(module_signature(trial, key) == value for key, value in signatures.items())
                and all(nodes.get(n["id"]) == semantic_node(n) for n in candidate["instances"])
                and all(graph["joints"].get(key) == value for key, value in candidate["joints"].items())
                and all(tools.module_bindings.get(key) == value for key, value in trial.module_bindings.items()))
            if not unchanged:
                rejected.append(dict(file=str(state_path), reason="would_change_completed_or_retained_geometry"))
                continue
            options.append((len(candidate["instances"]), state["revision"], str(state_path), state))
    original_assembly, original_bindings = tools.assembly, tools.module_bindings
    for _, _, path, state in sorted(options, key=lambda item: item[:3], reverse=True):
        assembly = Assembly(state["text"], state["source_sha256"])
        assembly.revision, assembly.joints = original_assembly.revision + 1, deepcopy(state["joints"])
        tools.assembly, tools.module_bindings = assembly, deepcopy(state["module_bindings"])
        try:
            checked = {name: module_check(tools, name) for name in completed}
        finally:
            tools.assembly, tools.module_bindings = original_assembly, original_bindings
        if any(result["status"] != "pass" for result in checked.values()):
            rejected.append(dict(file=path, reason="current_checker_rejected_boundary",
                statuses={name: result["status"] for name, result in checked.items()}))
            continue
        kept = {n["id"] for n in assembly.graph()["instances"]}
        tools.assembly, tools.module_bindings = assembly, deepcopy(state["module_bindings"])
        tools.save()
        measurements.update(checked)
        recovery["safe_boundary"] = dict(status="restored", file=path, sha256=digest(Path(path)),
            previous_revision=original_assembly.revision, revision=assembly.revision,
            completed_signatures=signatures, removed_unfinished_instances=sorted(nodes.keys() - kept),
            removed_unfinished_bindings=sorted(original_bindings.keys() - tools.module_bindings.keys()),
            geometry_preserved_in_source=True, rejected=rejected)
        recovery["destination_revision"] = assembly.revision
        return
    recovery["safe_boundary"] = dict(status="unavailable", reason="no_unchanged_passing_snapshot", rejected=rejected)


def recheck_seed(scheduler, recovery):
    from .assembly_candidates import structure, surroundings
    from .assembly_schedule import module_check
    from .assembly_tools import AssemblyTools
    source, tools = Path(recovery["source"]), scheduler.tools
    retired = {row["module"] for row in recovery.get("retired_modules", [])}
    tasks = recovery.get("completion_tasks", recovery["original_schedule"]["tasks"])
    for name, task in tasks.items():
        if name not in scheduler.tasks or name in retired:
            continue
        for path in sorted((source / "module-candidates" / name).glob("*/candidate.json")):
            old = read(path)
            state_path = path.parent / "state.json"
            if digest(state_path) != old["snapshot_sha256"]:
                raise ValueError("Recovery candidate snapshot changed")
            state = read(state_path)
            if state.get("packages") or state.get("source_archive"):
                raise ValueError("Recovery candidate has unsupported external dependencies")
            trial_path = tools.directory / "recovery-trials" / name / old["id"]
            write_json(trial_path / "state.json", state)
            trial = AssemblyTools(trial_path, tools.catalog_root)
            trial.checkpoint = tools.checkpoint
            trial.vehicle_contract = tools.vehicle_contract
            if structure(trial, name) != old.get("structure_id", old["id"]):
                raise ValueError("Recovery candidate structure differs from its identity")
            measurement = module_check(trial, name, compare=False)
            destination = tools.directory / "module-candidates" / name / old["id"]
            destination.mkdir(parents=True)
            shutil.copyfile(state_path, destination / "state.json")
            write_json(destination / "candidate.json", dict(old, measurement=measurement, surroundings=surroundings(trial, name),
                recovered_from=dict(file=str(path), sha256=digest(path),
                    old_measurement_sha256=identity(old["measurement"])),
                rechecked_execution_hash=scheduler.contract["execution_hash"]))
            recovery["rechecked_candidates"].append(dict(module=name, id=old["id"], status=measurement["status"]))
    measurements = {name: module_check(tools, name) for name, task in tasks.items()
        if name in scheduler.tasks and name not in retired and task.get("status") == "assembled"
        and name in tools.module_bindings}
    restore_safe_boundary(scheduler, recovery, measurements)
    for name, measurement in measurements.items():
        if measurement["status"] == "pass":
            scheduler.tasks[name].update(status="assembled", measurement=measurement,
                reason="rechecked_from_previous_executor_revision", inherited_from=dict(
                    source=recovery["source"], input_hash=recovery["source_input_hash"],
                    attempts=tasks[name].get("attempts", [])))
            recovery["carried_modules"].append(name)
        else:
            scheduler.tasks[name].update(measurement=measurement,
                reason="current_checker_rejected_previous_module")
    if any(digest(source / name) != expected for name, expected in recovery["source_files"].items()):
        raise ValueError("Source checkpoint changed during recovery")
    recovery["destination_input_hash"] = scheduler.input_hash
    recovery["destination_execution_hash"] = scheduler.contract["execution_hash"]
    write_json(tools.directory / "recovery.json", recovery)
    scheduler.save()
