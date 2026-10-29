import json
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_agent import (AssemblyAgent, CodexPlanner, LocalPlanner, PlannerUnavailable, CONTEXT_VERSION,
                                     context_guide, parse_decision, wire_json)
from car2lego3d.assembly_protocol import OPERATIONS, SCHEMAS, validate_call
from car2lego3d.assembly_tools import AssemblyTools


class ScriptedPlanner:
    name = "codex"

    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = 0

    def propose(self, payload, step):
        self.calls += 1
        tool, arguments = next(self.decisions)
        return dict(tool=tool, arguments_json=wire_json(arguments), reason="contract fixture")


def seed(tools):
    assert tools.call("edit", dict(revision=0, operations=[dict(op="add",
        part="3001.dat", color=4, matrix=np.eye(4).tolist())]))["ok"]


def test_execution_identity_invalidates_evaluation_when_validator_conditions_change(monkeypatch):
    from car2lego3d import assembly_agent as agent
    monkeypatch.delenv("CAR2LEGO_BRICKLAYER_AUDIT", raising=False)
    monkeypatch.setattr(agent.metadata, "version", lambda name: "0.1.0")
    original = agent.execution_hash()
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", "auto")
    assert agent.execution_hash() == original
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", "required")
    assert agent.execution_hash() != original
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", "auto")
    monkeypatch.setattr(agent.metadata, "version", lambda name: "0.2.0")
    assert agent.execution_hash() != original
    def unavailable(name):
        raise agent.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(agent.metadata, "version", unavailable)
    assert agent.execution_hash() != original


def fake_outputs(monkeypatch, tools):
    monkeypatch.setattr(tools, "tool_check", lambda: dict(status="unverified"))
    monkeypatch.setattr(tools, "tool_motion", lambda samples: dict(status="unverified"))
    monkeypatch.setattr(tools, "tool_render", lambda: dict(status="unverified"))
    monkeypatch.setattr(tools, "tool_export", lambda: dict(delivery_status="draft"))


def test_context_pages_roundtrip_pointers_and_work_note(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    value = {"a/b~c": ["玻璃" * 420, {"matrix": np.eye(4).tolist()}]}
    tools.context_resources["semantic"] = value
    offset, chunks = 0, []
    while True:
        result = tools.call("context", dict(resource="semantic", pointer="/a~1b~0c/0", offset=offset, limit=137))
        assert result["ok"]
        page = result["result"]
        chunks.append(page["text"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert "".join(chunks) == value["a/b~c"][0]
    assert not tools.call("context", dict(resource="semantic", pointer="/a~2b"))["ok"]
    assert not tools.call("context", dict(resource="semantic", limit=401))["ok"]
    assert tools.call("remember", dict(text="window moves with door"))["ok"]
    agent = AssemblyAgent(tools, ScriptedPlanner([]), dict(request=dict(model="Countach")))
    assert agent.context([], 1)["note"] == "window moves with door"
    assert tools.call("context", dict(resource="request"))["result"]["text"] == '{"model":"Countach"}'


def test_context_shorthand_normalizes_receipts_and_rejects_conflicts(tmp_path):
    from car2lego3d.assembly_context import progress
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    AssemblyAgent(tools, ScriptedPlanner([]), {})
    result = tools.call("context", dict(resource="guide:/start"))
    assert result["ok"]
    assert result["result"]["resource"] == "guide"
    assert result["result"]["pointer"] == "/start"
    assert progress({"0": result}, 0)["start_read"]
    tools.context_resources["semantic"] = {"a/b~c": ["exact"]}
    assert tools.call("context", dict(resource="semantic:/a~1b~0c/0"))["result"]["text"] == "exact"
    assert not tools.call("context", dict(resource="guide:/start", pointer="/tools/edit"))["ok"]
    for resource in ("guide:/../skill", "semantic:/a~2b", "unknown:/start", "guide:relative"):
        assert not tools.call("context", dict(resource=resource))["ok"]
    tools.context_resources["module:abc"] = {"instances": [{"id": "native"}]}
    direct = tools.call("context", dict(resource="module:abc", pointer="/instances/0/id"))
    shorthand = tools.call("context", dict(resource="module:abc:/instances/0/id"))
    assert direct["ok"] and shorthand["ok"]
    assert direct["result"] == shorthand["result"]
    assert direct["result"]["text"] == "native"
    tools.context_resources["literal:/name"] = "exact resource"
    assert tools.call("context", dict(resource="literal:/name"))["result"]["text"] == "exact resource"


def test_resume_requires_retry_and_same_inputs(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    fake_outputs(monkeypatch, tools)
    backend = ScriptedPlanner([("inspect", {})])
    first = AssemblyAgent(tools, backend, {}, max_steps=1).run()
    assert first["status"] == "draft" and first["steps"] == 1
    idle = ScriptedPlanner([])
    assert AssemblyAgent(tools, idle, {}, max_steps=2).run() == first
    assert idle.calls == 0
    retry = ScriptedPlanner([("inspect", {})])
    second = AssemblyAgent(tools, retry, {}, max_steps=2).run(retry=True)
    assert second["steps"] == 2 and second["elapsed_seconds"] >= first["elapsed_seconds"]
    with pytest.raises(ValueError, match="changed inputs"):
        AssemblyAgent(tools, idle, dict(request=dict(model="F40"))).run(retry=True)


def test_module_progress_exposes_assigned_candidate_gate(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    fake_outputs(monkeypatch, tools)
    monkeypatch.setattr(tools, "tool_module_progress", lambda: dict(modules=[], completion_ready=False))
    assigned = dict(module="wheel_front_left", status="unverified",
        candidates=dict(required=3, passing=2, reason="three_distinct_verified_structures_required"))
    agent = AssemblyAgent(tools, ScriptedPlanner([("module_progress", {})]),
        dict(module_task=dict(id="wheel_front_left")),
        max_steps=1, finalize=False, finish_check=lambda: assigned)
    agent.run()
    record = json.loads((agent.directory / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()[0])
    result = record["observation"]["result"]
    assert result["completion_ready"] is False
    assert result["module_task"] == assigned


def test_finish_requires_export_of_current_revision(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    seed(tools)
    identity = tools.assembly.graph()["instances"][0]["id"]
    fake_outputs(monkeypatch, tools)
    backend = ScriptedPlanner([("finish", {}), ("export", {}),
        ("edit", dict(revision=1, operations=[dict(op="color", instance=identity, color=1)])),
        ("finish", {}), ("export", {}), ("finish", {})])
    state = AssemblyAgent(tools, backend, {}, max_steps=8).run()
    records = [json.loads(r) for r in (tmp_path / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["observation"]["ok"] for r in records] == [False, True, True, False, True, True]
    assert state["stop_reason"] == "planner_finished"
    assert state["delivery_status"] == "draft" and state["appearance"] == "unverified"
    for record in records:
        assert record["input_text"] == wire_json(json.loads((tmp_path / record["input_file"]).read_text(encoding="utf-8")))
        assert record["decision_text"] == wire_json(record["decision"])


def test_decision_rejects_non_protocol_output():
    with pytest.raises(Exception):
        parse_decision(dict(tool="read", arguments_json="{}", reason=""))
    with pytest.raises(ValueError, match="object"):
        parse_decision(dict(tool="inspect", arguments_json="[]", reason=""))
    with pytest.raises(ValueError, match="no arguments"):
        parse_decision(dict(tool="finish", arguments_json='{"ok":true}', reason=""))


def test_guide_signatures_match_executor_and_fit_one_page(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    agent = AssemblyAgent(tools, ScriptedPlanner([]), {})
    guide = context_guide()
    assert agent.contract["context_version"] == CONTEXT_VERSION
    assert set(guide["tools"]) == {*SCHEMAS, "finish"}
    for group, schemas in (("tools", SCHEMAS), ("operations", {
            s["properties"]["op"]["const"]: s for s in OPERATIONS})):
        for name, schema in schemas.items():
            signature = guide[group][name]
            for field in schema["properties"]:
                marker = field + (":" if field in schema["required"] else "?:")
                assert marker in signature
            page = tools.call("context", dict(resource="guide", pointer=f"/{group}/{name}"))["result"]
            assert page["text"] == signature and page["next_offset"] is None
    assert len(guide["start"]) <= 400
    assert r"pattern=\.ldr$" in guide["build_operations"]["module"]
    payload = agent.context([], 20)
    assert payload["navigation"]["build_operations"] == "guide:/build_operations/<op>"


def test_live_module_probe_labels_recovered_errors_without_erasing_them():
    from scripts.probe_connector_module import recovered_errors
    trace = [
        dict(step=3, decision=dict(tool="build"), observation=dict(ok=False)),
        dict(step=4, decision=dict(tool="context"), observation=dict(ok=False)),
        dict(step=5, decision=dict(tool="build"), observation=dict(ok=True)),
        dict(step=6, decision=dict(tool="context"), observation=dict(ok=True)),
        dict(step=7, decision=dict(tool="finish"), observation=dict(ok=False)),
    ]
    assert recovered_errors(trace) == [
        dict(failed_step=3, recovered_step=5, tool="build"),
        dict(failed_step=4, recovered_step=6, tool="context"),
    ]


def test_first_context_navigates_exact_placements_without_embedding_them(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    operation = dict(op="add", part="3794a.dat", color=15, matrix=np.eye(4).tolist())
    agent = AssemblyAgent(tools, ScriptedPlanner([]), dict(semantic=dict(operations=[operation]*100)))
    payload = agent.context([], 20)
    assert payload["navigation"]["placements"]["count"] == 100
    assert "3794a.dat" not in wire_json(payload)
    result = tools.call("context", dict(resource="semantic", pointer="/operations/99"))
    assert result["ok"] and result["result"]["next_offset"] is None
    recovered = json.loads(result["result"]["text"])
    validate_call("edit", dict(revision=0, operations=[recovered]))
    assert recovered == operation


def test_completion_instruction_requires_actual_vehicle_contract(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    agent = AssemblyAgent(tools, ScriptedPlanner([]), {})
    assert "completion_ready" not in agent.context([], 20)["instruction"]
    tools.vehicle_contract = {"required_modules": ["chassis"]}
    assert "module_progress completion_ready=true" in agent.context([], 20)["instruction"]


def test_codex_options_preserve_provider_and_disable_only_child_extensions(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    text = 'model_provider="private"\n[model_providers.private]\nbase_url="http://localhost:1234"\n[mcp_servers.test]\ncommand="test"\n'
    config.write_text(text)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    options = CodexPlanner(tmp_path / "planner", executable="codex").cli_options()
    assert '--ignore-user-config' not in options
    assert 'mcp_servers.test.enabled=false' in options
    assert 'private' not in str(options)
    assert config.read_text() == text
    config.write_text('[mcp_servers."a.b"]\ncommand="test"\n')
    with pytest.raises(ValueError, match="MCP names"):
        CodexPlanner(tmp_path / "planner", executable="codex").cli_options()


def test_progress_survives_short_history_and_resume(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    fake_outputs(monkeypatch, tools)
    backend = ScriptedPlanner([("context", dict(resource="guide", pointer="/start")),
        ("context", dict(resource="guide", pointer="/tools/edit")),
        *[("remember", dict(text=f"Read observation {i}")) for i in range(9)]])
    agent = AssemblyAgent(tools, backend, {}, max_steps=11)
    agent.run()
    assert agent.context([], 1)["memory"]["start_read"]
    resumed = AssemblyAgent(AssemblyTools(tmp_path, tmp_path / "native"), ScriptedPlanner([("inspect", {})]),
                            {}, max_steps=12)
    fake_outputs(monkeypatch, resumed.tools)
    resumed.run(retry=True)
    memory = resumed.context([], 1)["memory"]
    assert memory["start_read"]
    assert memory["signatures"]["/tools/edit"].startswith("revision:")


def test_repeated_successful_reads_stop_with_diagnostics(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    fake_outputs(monkeypatch, tools)
    backend = ScriptedPlanner([("context", dict(resource="guide", pointer="/tools/edit"))] * 20)
    result = AssemblyAgent(tools, backend, {}, max_steps=20).run()
    assert result["steps"] == backend.calls == 3
    assert result["stop_reason"] == "repeated_tool_cycle"
    assert result["stall"] == dict(period=1, repetitions=3, revision=0,
        first_step=0, last_step=2, tools=["context"])
    assert result["delivery_status"] == "draft"


@pytest.mark.parametrize("status,error", [
    (422, dict(type="proposal_failed", message="Model did not return a complete JSON assembly decision")),
    (400, dict(type="invalid_request", message="Context limit exceeded: no silent truncation")),
])
def test_local_planner_keeps_actual_service_failure(monkeypatch, status, error):
    import httpx
    response = httpx.Response(status, request=httpx.Request("POST", "http://127.0.0.1:8781/v1/chat/completions"),
        json=dict(error=error))
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match=error["message"]):
        LocalPlanner("local_base", url="http://127.0.0.1:8781").propose({}, 0)


def test_local_planner_handles_non_json_service_error(monkeypatch):
    import httpx
    response = httpx.Response(502, request=httpx.Request("POST", "http://127.0.0.1:8781/v1/chat/completions"),
        text="Service unavailable")
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    with pytest.raises(PlannerUnavailable, match="HTTP 502"):
        LocalPlanner("local_base", url="http://127.0.0.1:8781").propose({}, 0)


@pytest.mark.parametrize("status", [401, 404, 408, 429, 503])
def test_local_provider_errors_interrupt_instead_of_consuming_repairs(monkeypatch, status):
    import httpx
    response = httpx.Response(status, json=dict(error=dict(type="service_error", message="Unavailable")))
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    with pytest.raises(PlannerUnavailable, match=f"HTTP {status}.*Unavailable"):
        LocalPlanner("local_base", url="http://127.0.0.1:8781").propose({}, 0)


def test_local_transport_timeout_keeps_original_cause(monkeypatch):
    import httpx

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("No response")

    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(PlannerUnavailable, match="ReadTimeout") as error:
        LocalPlanner("local_base", url="http://127.0.0.1:8781").propose({}, 0)
    assert isinstance(error.value.__cause__, httpx.ReadTimeout)


@pytest.mark.parametrize("returncode", [0, 1])
def test_codex_missing_decision_preserves_service_evidence(tmp_path, monkeypatch, returncode):
    from car2lego3d import assembly_agent

    class Process:
        def __init__(self, command, **kwargs):
            self.returncode = returncode
            kwargs["stderr"].write(b"provider failure")

        def communicate(self, *args, **kwargs):
            pass

    monkeypatch.setattr(assembly_agent.subprocess, "Popen", Process)
    backend = CodexPlanner(tmp_path, executable="fixture-codex")
    monkeypatch.setattr(backend, "cli_options", lambda: [])
    with pytest.raises(PlannerUnavailable, match="decision failed"):
        backend.propose(dict(task="preserve"), 0)
    assert (tmp_path / "0000/stderr.log").read_bytes() == b"provider failure"
    assert json.loads((tmp_path / "0000/input.json").read_text()) == dict(task="preserve")


def test_codex_deadline_kills_child_before_interrupting(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent
    events = []

    class Process:
        pid = 12345

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, *args, **kwargs):
            raise assembly_agent.subprocess.TimeoutExpired("fixture-codex", 1)

        def kill(self):
            events.append("kill")

        def wait(self):
            events.append("wait")

    monkeypatch.setattr(assembly_agent.subprocess, "Popen", Process)
    monkeypatch.setattr(assembly_agent.subprocess, "run", lambda *args, **kwargs: events.append("taskkill"))
    backend = CodexPlanner(tmp_path, executable="fixture-codex", timeout=1)
    monkeypatch.setattr(backend, "cli_options", lambda: [])
    with pytest.raises(PlannerUnavailable, match="deadline exceeded"):
        backend.propose({}, 0)
    assert events == ["taskkill" if assembly_agent.os.name == "nt" else "kill", "wait"]


def test_malformed_decisions_still_use_decision_failure_budget(tmp_path):
    class Malformed:
        name = "codex"

        def propose(self, payload, step):
            return dict(tool="inspect", arguments_json="not-json", reason="malformed")

    tools = AssemblyTools(tmp_path, tmp_path / "native")
    result = AssemblyAgent(tools, Malformed(), {}, max_steps=10, finalize=False).run()
    assert result["stop_reason"] == "repair_budget_exhausted"
    assert result["status"] == "draft" and result["steps"] == 3


def test_late_decision_cannot_mutate_or_trigger_finalization(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent
    clock = [0.0]
    monkeypatch.setattr(assembly_agent.time, "monotonic", lambda: clock[0])
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    before = tools.assembly.graph()

    class LatePlanner:
        name = "codex"

        def propose(self, payload, step):
            clock[0] = 11
            return dict(tool="edit", arguments_json=wire_json(dict(revision=0,
                operations=[dict(op="add", part="3001.dat", matrix=np.eye(4).tolist())])), reason="late")

    result = AssemblyAgent(tools, LatePlanner(), {}, max_seconds=10).run()
    assert result["stop_reason"] == "time_budget_exhausted" and result["status"] == "draft"
    assert result["steps"] == 0 and tools.assembly.graph() == before
    assert not (tmp_path / "trajectory.jsonl").exists()
    assert not (tmp_path / "model.io").exists()
    assert (tmp_path / "inputs/0000.json").is_file()


def test_agent_passes_remaining_time_without_changing_planner_timeout(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent
    clock, timeouts = [0.0], []
    monkeypatch.setattr(assembly_agent.time, "monotonic", lambda: clock[0])
    tools = AssemblyTools(tmp_path / "assembly", tmp_path / "native")
    fake_outputs(monkeypatch, tools)
    backend = CodexPlanner(tmp_path / "planner", executable="fixture-codex", timeout=1200)

    def propose(payload, step, *, timeout):
        timeouts.append(timeout)
        clock[0] += 4
        return dict(tool="export" if step == 0 else "finish", arguments_json="{}", reason="within budget")

    monkeypatch.setattr(backend, "propose", propose)
    result = AssemblyAgent(tools, backend, {}, max_seconds=10, finalize=False).run()
    assert result["stop_reason"] == "planner_finished"
    assert timeouts == [10, 6] and backend.timeout == 1200


def test_budget_checkpoint_stops_motion_and_restores_host_checkpoint(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent
    clock = [0.0]
    monkeypatch.setattr(assembly_agent.time, "monotonic", lambda: clock[0])
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    host_checkpoint = lambda: None
    agent = AssemblyAgent(tools, ScriptedPlanner([("motion", dict(samples=9))]), {},
        max_seconds=10, checkpoint=host_checkpoint)

    def motion(samples):
        clock[0] = 11
        tools.checkpoint()
        pytest.fail("Expired motion must stop at its next pose checkpoint")

    monkeypatch.setattr(tools, "tool_motion", motion)
    result = agent.run()
    assert result["stop_reason"] == "time_budget_exhausted"
    assert tools.checkpoint is host_checkpoint
    record = json.loads((tmp_path / "trajectory.jsonl").read_text().splitlines()[0])
    assert not record["ok"] and "AssemblyBudgetExceeded" in record["error"]


@pytest.mark.parametrize("configured,remaining,expected", [(1200, 7, 7), (3, 7, 3)])
def test_codex_communicate_uses_shorter_deadline(tmp_path, monkeypatch, configured, remaining, expected):
    from car2lego3d import assembly_agent
    observed = []

    class Process:
        returncode = 0

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, data, *, timeout):
            observed.append(timeout)
            folder = tmp_path / "0000"
            (folder / "decision.json").write_text(wire_json(dict(
                tool="inspect", arguments_json="{}", reason="fixture")))

    monkeypatch.setattr(assembly_agent.subprocess, "Popen", Process)
    backend = CodexPlanner(tmp_path, executable="fixture-codex", timeout=configured)
    monkeypatch.setattr(backend, "cli_options", lambda: [])
    assert backend.propose({}, 0, timeout=remaining)["tool"] == "inspect"
    assert observed == [expected]


def test_final_module_check_cannot_return_success_after_budget(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent
    clock, checks = [0.0], []
    monkeypatch.setattr(assembly_agent.time, "monotonic", lambda: clock[0])
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    fake_outputs(monkeypatch, tools)

    def finish_check():
        checks.append(True)
        if len(checks) == 2:
            clock[0] = 11
        return dict(status="pass")

    result = AssemblyAgent(tools, ScriptedPlanner([("export", {}), ("finish", {})]),
        dict(module_task=dict(id="deadline_fixture")),
        max_seconds=10, finalize=False, finish_check=finish_check).run()
    assert result["stop_reason"] == "time_budget_exhausted"
    assert result["status"] == "draft"
