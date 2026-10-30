"""Resume a vehicle as bounded module tasks; final acceptance remains vehicle-wide."""
from copy import deepcopy
import json
from pathlib import Path

from .assembly_requirements import descendants, identity, validate_binding
from .common import digest, write_json

VERSION = "vehicle-assembly-schedule.v2"


def task_plan(contract):
    names = list(contract["modules"])
    available = ["chassis", "steering", "suspension", *[n for n in names if n.startswith("wheel_")], "cabin",
        *[n for n in names if n.startswith("door_")], "front", "front_lights",
        *(["headlamp_mechanism"] if "headlamp_mechanism" in names else []), "hood", "rear", "rear_lights", "rear_cover", "signature"]
    order = [name for name in available if name in names]
    if set(order) != set(names) or len(order) != len(names):
        raise ValueError("Module scheduling must cover every required module exactly once")
    dependencies = {}
    for name in order:
        if name == "chassis":
            deps = []
        elif name.startswith("wheel_"):
            deps = ["chassis", "suspension", *(["steering"] if name.startswith("wheel_front_") else [])]
        elif name in {"steering", "suspension"}:
            deps = ["chassis"]
            if name == "suspension":
                deps.append("steering")
        elif name == "cabin":
            deps = ["chassis", "steering", "suspension"]
        elif name.startswith("door_") or name in {"front", "rear"}:
            deps = ["chassis", "cabin"]
        elif name in {"front_lights", "hood", "headlamp_mechanism"}:
            deps = ["front"]
        elif name in {"rear_lights", "rear_cover"}:
            deps = ["rear"]
        else:
            deps = order[:-1]
        dependencies[name] = dict(id=name, dependencies=[dep for dep in deps if dep in names], requirement=contract["modules"][name],
            status="pending", attempts=[], acceptance="provisional_mechanical_only")
    return dependencies


def module_signature(tools, name):
    from .assembly_mounts import owned_members, semantic_node
    binding = tools.module_bindings.get(name)
    if not binding:
        return None
    graph = tools.assembly.graph()
    try:
        members = owned_members(graph, tools.module_bindings, name)
    except ValueError:
        return None
    return identity(dict(binding=binding, instances=[semantic_node(n) for n in graph["instances"] if n["id"] in members],
        joints={key: value for key, value in graph["joints"].items() if key in members}))


def module_check(tools, name, *, compare=True):
    """Check one module against the actual surrounding assembly, without certifying appearance."""
    from .assembly_catalog import validate
    from .assembly_materials import palette, rgba
    from .assembly_motion import evaluate_motion, joint_range_issues
    from .assembly_mounts import mechanism_response, wheel_mount_issues
    graph = tools.assembly.graph()
    binding = tools.module_bindings.get(name, {})
    try:
        validate_binding(graph, tools.vehicle_contract, name, binding.get("roots", []), binding.get("roles", {}),
            bindings=tools.module_bindings)
        members = descendants(graph, binding["roots"])
    except (ValueError, KeyError) as error:
        return dict(status="fail", failures=[str(error)], module=name)
    nodes = {n["id"]: n for n in graph["instances"]}
    parts = {i for i in members if nodes[i]["kind"] == "part"}

    def scoped(posed):
        measured = validate(posed, tools.catalog, collision_limit=1000000)
        unknown = [row for row in measured["unknown"] if row.get("instance") in parts]
        collisions = [row for row in measured["collisions"] if row["a"] in parts or row["b"] in parts]
        invalid = [i for i in measured["invalid_transforms"] if i in parts]
        components = [set(group) for group in measured["components"]]
        connected = any(parts <= component for component in components)
        edges = [edge for edge in measured["connections"] if (edge["a"] in parts) != (edge["b"] in parts)]
        connected = connected and (name == "chassis" or bool(edges))
        return dict(collisions=collisions, unknown=unknown, invalid_transforms=invalid,
            gates=dict(connections="unverified" if unknown else "pass" if connected else "fail"),
            components=measured["components"], interface_contacts=edges,
            status="fail" if collisions or invalid or not connected else "unverified" if unknown else "pass")

    digital = scoped(graph)
    requirement = tools.vehicle_contract["modules"][name]
    purpose = requirement["joint_purpose"]
    joints = {key: joint for key, joint in graph["joints"].items() if key in members and joint.get("purpose") == purpose
        and joint["limits"][0] < joint["limits"][1]} if purpose else {}
    failures = wheel_mount_issues(graph, tools.vehicle_contract, tools.module_bindings, name)
    layout = None
    if name.startswith("wheel_") or name in {"steering", "suspension"}:
        from .assembly_layout import wheel_layout
        layout = wheel_layout(graph, tools.catalog, tools.vehicle_contract, tools.module_bindings, require_complete=False)
        failures.extend(sorted({item["code"] for item in layout["issues"] if item["status"] == "fail"}))
    response = None
    if name in {"steering", "suspension"}:
        response = mechanism_response(graph, tools.vehicle_contract, tools.module_bindings, name)
        if response["status"] == "fail":
            failures.append("wheel_response_failed")
    for joint in joints.values():
        failures.extend(joint_range_issues(joint))
    if len(joints) < requirement["minimum_joints"]:
        failures.append("missing_required_joints")
    if name.startswith("door_") and not any(
            set(binding["roles"]["panel"] + binding["roles"].get("glass", [])) <= descendants(graph, [key])
            for key in joints if nodes[key]["kind"] == "module"):
        failures.append("door_glass_not_in_shared_moving_subtree")
    colors = palette(tools.catalog_root)
    for role in ("glass", "lens"):
        for instance in binding["roles"].get(role, []):
            color = rgba(nodes[instance]["color"], colors)
            if color is None or color[3] == 255:
                failures.append("unknown_or_opaque_" + role)
    motion = evaluate_motion(dict(graph, joints=joints), scoped, samples=9, checkpoint=tools.checkpoint) if joints else None
    status = "fail" if failures or digital["status"] == "fail" or (motion and motion["status"] == "fail") else (
        "unverified" if digital["status"] != "pass" or (motion and motion["status"] != "pass")
        or (layout and layout["status"] != "pass") else "pass")
    candidates = None
    if compare:
        from .assembly_candidates import comparison
        candidates = comparison(tools, name)
        if status == "pass" and candidates["status"] != "pass":
            status = "unverified"
    return dict(module=name, status=status, signature=module_signature(tools, name),
        graph_sha256=identity(graph), bindings_sha256=identity(tools.module_bindings), digital=digital, motion=motion,
        failures=failures, semantic_roles="unverified", appearance="unverified", physical="unverified",
        candidates=candidates,
        mechanism_response=response,
        wheel_layout=layout,
        native_evidence=tools.verification_inputs()["evidence_files"])


class ModuleScheduler:
    def __init__(self, tools, backend, mission, resources, *, max_steps=160, checkpoint=None):
        self.tools, self.backend, self.mission, self.resources = tools, backend, mission, resources
        self.max_steps, self.checkpoint = max_steps, checkpoint or (lambda: None)
        tools.checkpoint = self.checkpoint
        tools.vehicle_contract = mission["vehicle_contract"]
        self.path = tools.directory / "module-tasks.json"
        from .assembly_agent import execution_hash, skill_bundle, DECISION_TOKENS, CONTEXT_VERSION, planner_context_contract
        from .assembly_protocol import protocol_hash
        self.contract = dict(mission=mission, execution_hash=execution_hash(), protocol_hash=protocol_hash(),
            context_version=CONTEXT_VERSION, decision_tokens=DECISION_TOKENS,
            planner_context=planner_context_contract(backend),
            skill_hash=skill_bundle()["sha256"], snapshot=tools.corpus.snapshot() if tools.corpus else None,
            exclusions=tools.exclusions)
        self.input_hash = identity(self.contract)
        self.tasks = task_plan(mission["vehicle_contract"])
        if self.path.is_file():
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            if previous["input_hash"] != self.input_hash or previous.get("planner") != backend.name:
                raise ValueError("Module task inputs, executor or planner changed; preserve the old run and create a revision")
            self.tasks = previous["tasks"]

    def save(self, active=None):
        state = dict(schema=VERSION, input_hash=self.input_hash, contract=self.contract, planner=self.backend.name, active=active,
            tasks=self.tasks, complete=sum(t["status"] == "assembled" for t in self.tasks.values()),
            required=len(self.tasks), acceptance="provisional_mechanical_only")
        write_json(self.path, state)
        return state

    def invalidate(self, names, reason):
        """Keep prior attempts while invalidating a changed module and its DAG descendants."""
        affected = set(names)
        if not affected <= self.tasks.keys():
            raise ValueError("Unknown module task")
        while True:
            descendants = {name for name, task in self.tasks.items() if affected.intersection(task["dependencies"])}
            expanded = affected | descendants
            if expanded == affected:
                break
            affected = expanded
        for name in affected:
            task = self.tasks[name]
            if task.get("measurement"):
                task.setdefault("invalidated_evidence", []).append(dict(reason=reason, measurement=task.pop("measurement")))
            task.update(status="pending", reason=reason)
        self.save()
        return affected

    def recheck(self):
        failed = []
        for name, task in self.tasks.items():
            if task["status"] == "assembled":
                task["measurement"] = module_check(self.tools, name)
                if task["measurement"]["status"] != "pass":
                    failed.append(name)
        if failed:
            self.invalidate(failed, "final_surroundings_check_failed")
        return self.save()

    def run(self, *, retry=False, auto_repair=True):
        from .assembly_agent import AssemblyAgent, PlannerUnavailable
        from .assembly_evidence import files_current
        self.save()
        for name, task in self.tasks.items():
            self.checkpoint()
            if task["status"] == "assembled":
                proof = task.get("measurement", {})
                if (proof.get("signature") == module_signature(self.tools, name)
                        and files_current(proof.get("native_evidence"))):
                    # New neighbors can collide or detach an unchanged module.
                    current = module_check(self.tools, name)
                    if current["status"] == "pass":
                        task["measurement"] = current
                        continue
                self.invalidate([name], "module_or_surroundings_changed")
            if any(self.tasks[dep]["status"] != "assembled" for dep in task["dependencies"]):
                task.update(status="blocked", reason="dependency_not_assembled")
                break
            interrupted = bool(task["attempts"] and task["attempts"][-1]["status"] in {"running", "interrupted"})
            if not interrupted and (len(task["attempts"]) >= 3 or (task["attempts"] and not retry)):
                task.update(status="blocked", reason="repair_budget_exhausted" if len(task["attempts"]) >= 3 else "explicit_retry_required")
                break
            attempt = len(task["attempts"]) if interrupted else len(task["attempts"]) + 1
            directory = self.tools.directory / "module-runs" / name / f"attempt-{attempt}"
            task.update(status="running")
            if not interrupted:
                task["attempts"].append(dict(directory=str(directory.relative_to(self.tools.directory)), status="running"))
            self.save(name)
            scoped = deepcopy(self.mission)
            scoped["module_task"] = {key: task[key] for key in ("id", "dependencies", "requirement")}
            scoped["module_task"].update(goal="Build and bind this real module; inspect three structural candidates. "
                "Preserve existing modules; connect to their interfaces. Unknown evidence blocks completion.",
                prerequisite_bindings={dep: self.tools.module_bindings.get(dep) for dep in task["dependencies"]})
            if task.get("measurement"):
                scoped["module_task"]["previous_diagnostics"] = task["measurement"]
            graph = self.tools.assembly.graph()
            completed = {key: self.tools.module_bindings[key] for key, item in self.tasks.items()
                if key != name and item["status"] == "assembled" and key in self.tools.module_bindings}
            scoped["module_task"].update(completed_bindings=deepcopy(completed),
                ownership_rule="Completed bindings are read-only and their role instances cannot be reused. "
                "Create independent instances for this task; install only new module subtrees at host installation_targets. "
                "Preserve completed parts, transforms and joints; repair the active unfinished subtree as diagnosed. "
                "Steering/suspension may share carriers.")
            from .assembly_mounts import installation_targets, protection
            mounts = installation_targets(graph, self.tools.vehicle_contract, completed, name)
            scoped["module_task"]["installation_targets"] = mounts
            self.tools.protected_state = protection(graph, completed, mounts=mounts,
                editable_roots=self.tools.module_bindings.get(name, {}).get("roots", []))
            agent = AssemblyAgent(self.tools, self.backend, scoped, directory=directory,
                max_steps=self.max_steps, checkpoint=self.checkpoint, finalize=False,
                finish_check=lambda: module_check(self.tools, name))
            self.tools.context_resources.update(self.resources)
            prior_directory = getattr(self.backend, "directory", None)
            if prior_directory is not None:
                self.backend.directory = directory / "planner"
                self.backend.directory.mkdir(parents=True, exist_ok=True)
            try:
                result = agent.run(retry=interrupted)
            except BaseException as exc:
                task.update(status="interrupted", reason="planner_unavailable" if isinstance(exc, PlannerUnavailable)
                    else "module_execution_interrupted")
                task["attempts"][-1]["status"] = "interrupted"
                self.save()
                raise
            finally:
                self.tools.protected_state = None
                if prior_directory is not None:
                    self.backend.directory = prior_directory
            measurement = module_check(self.tools, name)
            write_json(directory / "module-check.json", measurement)
            task["attempts"][-1].update(status=result["status"], input_hash=result["input_hash"],
                report_sha256=digest(directory / "module-check.json"))
            task.update(status="assembled" if result["status"] == "module_checked" and measurement["status"] == "pass" else "blocked",
                measurement=measurement, reason=result["stop_reason"])
            self.save()
            if task["status"] != "assembled":
                if auto_repair and len(task["attempts"]) < 3:
                    return self.run(retry=True, auto_repair=True)
                break
        return self.recheck()
