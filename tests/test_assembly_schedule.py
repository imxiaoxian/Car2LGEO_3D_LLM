from copy import deepcopy
import json

import numpy as np
import pytest

from car2lego3d.assembly_agent import AssemblyAgent, PlannerUnavailable
from car2lego3d.assembly_requirements import vehicle_contract
from car2lego3d.assembly_schedule import ModuleScheduler, task_plan
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import write_json


def test_vehicle_tasks_cover_required_modules_and_mechanical_dependencies():
    for brand, model in (("Lamborghini", "Countach"), ("Ferrari", "F40"),
            ("Nissan", "Skyline R34"), ("Bugatti", "Chiron Pur Sport")):
        contract = vehicle_contract(dict(brand=brand, model=model), "semantic-hash")
        tasks = task_plan(contract)
        assert set(tasks) == set(contract["modules"])
        seen = set()
        for name, task in tasks.items():
            assert set(task["dependencies"]) <= seen
            seen.add(name)
        assert "cabin" in tasks["door_left"]["dependencies"]
        assert "front" in tasks["front_lights"]["dependencies"]
        if "suspension" in tasks:
            assert "steering" in tasks["suspension"]["dependencies"]
            assert "suspension" in tasks["wheel_front_left"]["dependencies"]
        if "steering" in tasks:
            assert "steering" in tasks["wheel_front_left"]["dependencies"]
            assert not any(n.startswith("wheel_") for n in tasks["steering"]["dependencies"])
        if brand in {"Lamborghini", "Ferrari"}:
            assert tasks["headlamp_mechanism"]["requirement"]["joint_purpose"] == "lamp"
            assert "front" in tasks["headlamp_mechanism"]["dependencies"]


def test_unknown_requirement_cannot_silently_disappear_from_scheduler():
    contract = vehicle_contract({}, "scene")
    contract["modules"]["unknown_mechanism"] = {}
    with pytest.raises(ValueError, match="every required"):
        task_plan(contract)


class Planner:
    name = "codex"

    def __init__(self):
        self.calls = 0

    def propose(self, payload, step):
        self.calls += 1
        return dict(tool="finish" if step else "export", arguments_json="{}", reason="fixture")


def test_scheduler_and_agent_share_motion_checkpoint(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    mission = dict(vehicle_contract=vehicle_contract({}, "scene"))
    checkpoint = lambda: None
    scheduler = ModuleScheduler(tools, Planner(), mission, {}, checkpoint=checkpoint)
    assert tools.checkpoint is scheduler.checkpoint is checkpoint
    agent = AssemblyAgent(tools, Planner(), mission, checkpoint=checkpoint)
    assert tools.checkpoint is agent.checkpoint is checkpoint


def test_module_finish_is_scoped_and_keeps_vehicle_acceptance_separate(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path / "graph", tmp_path / "native")
    contract = vehicle_contract({}, "scene")
    backend = Planner()
    monkeypatch.setattr(tools, "tool_export", lambda: dict(delivery_status="draft"))
    agent = AssemblyAgent(tools, backend, dict(vehicle_contract=contract, module_task=dict(id="chassis")),
        directory=tmp_path / "task", finalize=False, finish_check=lambda: dict(status="pass"), max_steps=3)
    result = agent.run()
    assert result["status"] == "module_checked" and result["delivery_status"] == "draft"
    assert tools.vehicle_contract == contract
    assert (tmp_path / "task/agent-decisions.jsonl").is_file()
    assert not (tools.directory / "agent.json").exists()
    assert agent.context([], 1)["module_task"]["id"] == "chassis"


def test_unverified_module_cannot_finish_and_cannot_claim_complete(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path / "graph", tmp_path / "native")
    monkeypatch.setattr(tools, "tool_export", lambda: {})
    agent = AssemblyAgent(tools, Planner(), dict(module_task=dict(id="chassis")), max_steps=3,
        directory=tmp_path / "task", finalize=False, finish_check=lambda: dict(status="unverified"))
    result = agent.run()
    assert result["status"] == "draft"
    assert result["module_task"]["status"] == "unverified"


def test_scheduler_stops_at_failed_module_and_preserves_three_attempt_limit(tmp_path, monkeypatch):
    from car2lego3d import assembly_schedule
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    contract = vehicle_contract({}, "scene")
    mission = dict(vehicle_contract=contract)
    monkeypatch.setattr(tools, "tool_export", lambda: {})
    monkeypatch.setattr(assembly_schedule, "module_check", lambda tools, name: dict(status="fail", module=name))
    for attempt in range(3):
        result = ModuleScheduler(tools, Planner(), mission, {}, max_steps=2).run(retry=attempt > 0, auto_repair=False)
        assert len(result["tasks"]["chassis"]["attempts"]) == attempt + 1
        assert not result["tasks"]["wheel_front_left"]["attempts"]
    blocked = ModuleScheduler(tools, Planner(), mission, {}, max_steps=2).run(retry=True)
    assert blocked["tasks"]["chassis"]["reason"] == "repair_budget_exhausted"
    assert len(blocked["tasks"]["chassis"]["attempts"]) == 3
    with pytest.raises(ValueError, match="changed"):
        ModuleScheduler(tools, Planner(), dict(mission, request={"color": 4}), {})


def test_module_automatically_repairs_at_most_three_times_before_stopping(tmp_path, monkeypatch):
    from car2lego3d import assembly_schedule
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    monkeypatch.setattr(tools, "tool_export", lambda: {})
    monkeypatch.setattr(assembly_schedule, "module_check", lambda tools, name: dict(status="fail", module=name))
    result = ModuleScheduler(tools, Planner(), dict(vehicle_contract=vehicle_contract({}, "scene")), {}, max_steps=2).run()
    assert len(result["tasks"]["chassis"]["attempts"]) == 3
    assert result["complete"] == 0
    assert not result["tasks"]["wheel_front_left"]["attempts"]


def test_provider_outage_resumes_same_attempt_without_replaying_successful_edits(tmp_path, monkeypatch):
    from car2lego3d import assembly_schedule

    class InterruptedPlanner:
        name = "codex"

        def __init__(self):
            self.calls = []
            self.available = False

        def propose(self, payload, step):
            self.calls.append(step)
            if step == 0:
                return dict(tool="edit", arguments_json=json.dumps(dict(revision=0, operations=[
                    dict(op="add", part="3001.dat", color=4, matrix=np.eye(4).tolist())])), reason="add once")
            if not self.available:
                raise PlannerUnavailable("provider unavailable")
            return dict(tool="export" if step == 2 else "finish", arguments_json="{}", reason="resume")

    plan = task_plan(vehicle_contract({}, "scene"))
    monkeypatch.setattr(assembly_schedule, "task_plan", lambda contract: dict(chassis=deepcopy(plan["chassis"])))
    monkeypatch.setattr(assembly_schedule, "module_check", lambda *args: dict(status="pass"))
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    monkeypatch.setattr(tools, "tool_export", lambda: dict(delivery_status="draft"))
    backend = InterruptedPlanner()
    mission = dict(vehicle_contract=vehicle_contract({}, "scene"))
    scheduler = ModuleScheduler(tools, backend, mission, {}, max_steps=5)
    scheduler.tasks["chassis"]["measurement"] = dict(status="fail", prior_diagnostic="keep stable on resume")
    with pytest.raises(PlannerUnavailable, match="provider unavailable"):
        scheduler.run()
    task = scheduler.tasks["chassis"]
    assert task["status"] == "interrupted" and task["reason"] == "planner_unavailable"
    assert len(task["attempts"]) == 1 and task["attempts"][0]["status"] == "interrupted"
    folder = tmp_path / task["attempts"][0]["directory"]
    interrupted = json.loads((folder / "agent.json").read_text())
    assert interrupted["status"] == "interrupted" and interrupted["stop_reason"] == "planner_unavailable"
    assert interrupted["steps"] == 2 and interrupted["consecutive_failures"] == 0
    trace = [json.loads(row) for row in (folder / "agent-decisions.jsonl").read_text().splitlines()]
    assert trace[-1]["observation"]["failure_kind"] == "planner_unavailable"
    assert backend.calls == [0, 1] and tools.assembly.revision == 1
    assert task["measurement"]["prior_diagnostic"] == "keep stable on resume"
    backend.available = True
    result = ModuleScheduler(tools, backend, mission, {}, max_steps=5).run(retry=True)
    assert result["complete"] == result["required"] == 1
    assert len(result["tasks"]["chassis"]["attempts"]) == 1
    assert backend.calls == [0, 1, 2, 3] and tools.assembly.revision == 1
    assert len(tools.assembly.graph()["instances"]) == 1


def test_planners_share_input_contract_but_cannot_resume_each_others_work(tmp_path):
    mission = dict(vehicle_contract=vehicle_contract({}, "scene"))
    first = ModuleScheduler(AssemblyTools(tmp_path / "first", tmp_path / "native"), Planner(), mission, {})
    first.save()
    other = Planner()
    other.name = "local_finetuned"
    second = ModuleScheduler(AssemblyTools(tmp_path / "second", tmp_path / "native"), other, mission, {})
    assert first.input_hash == second.input_hash
    with pytest.raises(ValueError, match="planner"):
        ModuleScheduler(first.tools, other, mission, {})


def test_different_visual_context_cannot_claim_same_comparison_input(tmp_path):
    from car2lego3d.assembly_agent import CodexPlanner, LocalPlanner, planner_context_contract
    mission = dict(vehicle_contract=vehicle_contract({}, "scene"))
    codex = CodexPlanner(tmp_path / "planner", executable="fixture-codex")
    codex.vehicle_context = dict(contact_sheet=dict(file="views.jpg", sha256="pixels"))
    local = LocalPlanner("local_finetuned", url="http://127.0.0.1:9999")
    first = ModuleScheduler(AssemblyTools(tmp_path / "first", tmp_path / "native"), codex, mission, {})
    second = ModuleScheduler(AssemblyTools(tmp_path / "second", tmp_path / "native"), local, mission, {})
    assert first.input_hash != second.input_hash
    local.vehicle_context = codex.vehicle_context
    assert planner_context_contract(codex)["vehicle_context_sha256"] == planner_context_contract(local)["vehicle_context_sha256"]
    assert planner_context_contract(codex) != planner_context_contract(local)


def test_invalidating_module_preserves_history_and_only_invalidates_dependents(tmp_path):
    scheduler = ModuleScheduler(AssemblyTools(tmp_path, tmp_path / "native"), Planner(),
        dict(vehicle_contract=vehicle_contract({}, "scene")), {})
    for name, task in scheduler.tasks.items():
        task.update(status="assembled", measurement=dict(module=name, status="pass"))
        task["attempts"].append(dict(status="module_checked"))
    affected = scheduler.invalidate(["front"], "changed_interface")
    assert affected == {"front", "front_lights", "hood", "signature"}
    assert scheduler.tasks["cabin"]["status"] == scheduler.tasks["rear"]["status"] == "assembled"
    for name in affected:
        task = scheduler.tasks[name]
        assert task["status"] == "pending" and len(task["attempts"]) == 1
        assert task["invalidated_evidence"][0]["measurement"]["module"] == name


def test_final_neighbor_failure_invalidates_earlier_modules_and_downstream(tmp_path, monkeypatch):
    from car2lego3d import assembly_schedule
    scheduler = ModuleScheduler(AssemblyTools(tmp_path, tmp_path / "native"), Planner(),
        dict(vehicle_contract=vehicle_contract({}, "scene")), {})
    for task in scheduler.tasks.values():
        task["status"] = "assembled"
    monkeypatch.setattr(assembly_schedule, "module_check", lambda tools, name:
        dict(status="fail" if name == "cabin" else "pass", module=name, graph_sha256="final-graph"))
    result = scheduler.recheck()
    assert result["complete"] < result["required"]
    assert result["tasks"]["cabin"]["status"] == result["tasks"]["door_left"]["status"] == "pending"
    assert result["tasks"]["chassis"]["measurement"]["graph_sha256"] == "final-graph"


def test_module_task_exposes_all_completed_bindings_not_only_dependencies(tmp_path, monkeypatch):
    from car2lego3d import assembly_evidence, assembly_schedule
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    aliases = tools.call("edit", dict(revision=0, operations=[
        dict(op="module", name="frame.ldr", alias="frame", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@frame", part="3001.dat", alias="brick", matrix=np.eye(4).tolist())]))["result"]["aliases"]
    contract = vehicle_contract({}, "scene")
    for name, role in (("chassis", "structure"), ("wheel_front_left", "hub")):
        tools.module_bindings[name] = dict(roots=[aliases["frame"]], roles={role: [aliases["brick"]]})
    scheduler = ModuleScheduler(tools, Planner(), dict(vehicle_contract=contract), {})
    for name in ("chassis", "steering", "suspension", "wheel_front_left"):
        scheduler.tasks[name].update(status="assembled", measurement=dict(signature="fixture", native_evidence=[]))
    monkeypatch.setattr(assembly_schedule, "module_signature", lambda *args: "fixture")
    monkeypatch.setattr(assembly_schedule, "module_check", lambda *args: dict(status="pass"))
    monkeypatch.setattr(assembly_evidence, "files_current", lambda *args: True)

    def run(agent, **kwargs):
        task = agent.mission["module_task"]
        assert task["id"] == "wheel_front_right"
        assert task["dependencies"] == ["chassis", "suspension", "steering"]
        assert task["completed_bindings"]["wheel_front_left"] == tools.module_bindings["wheel_front_left"]
        assert "independent instances" in task["ownership_rule"]
        raise RuntimeError("inspected task context")

    monkeypatch.setattr(AssemblyAgent, "run", run)
    with pytest.raises(RuntimeError, match="inspected task context"):
        scheduler.run()
