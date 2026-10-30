import importlib.util
import json
from pathlib import Path

import pytest

from car2lego3d.assembly_agent import wire_json
from car2lego3d.assembly_corpus import Corpus
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


spec = importlib.util.spec_from_file_location("packing_evaluation",
    Path(__file__).parents[1] / "scripts/evaluate_assembly_prompt_packing.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def checkpoint(tmp_path):
    agent = tmp_path / "source"
    attempt = agent / "module-runs/chassis/attempt-1"
    tools = AssemblyTools(agent, tmp_path / "native")
    tools.save()
    initial = json.loads((agent / "state.json").read_text())
    corpus = Corpus(tmp_path / "corpus")
    snapshot = corpus.freeze()["snapshot"]
    corpus.db.close()
    contract = dict(mission=dict(module_task=dict(id="chassis")), exclusions=tools.exclusions)
    write_json(attempt / "agent.json", dict(contract=contract))
    write_json(attempt / "initial-assembly.json", initial)
    (attempt / "agent-decisions.jsonl").write_text("", encoding="utf-8")
    write_json(agent / "launch.json", dict(arguments=dict(corpus_root=str(tmp_path / "corpus"),
        native_root=str(tmp_path / "native"), snapshot=snapshot)))
    write_json(attempt / "inputs/0000.json", dict(state=dict(revision=0),
        graph_summary=dict(instances=tools.assembly.graph()["instances"], bom=tools.assembly.bom())))
    return agent, attempt


def test_isolated_decisions_use_identical_state_without_mutating_source(tmp_path, monkeypatch):
    agent, attempt = checkpoint(tmp_path)
    before = {str(p): digest(p) for p in agent.rglob("*") if p.is_file()}
    requests = []

    def propose(self, payload, step):
        requests.append(payload)
        folder = self.directory / f"{step:04d}"
        folder.mkdir(parents=True)
        (folder / "events.jsonl").write_text(wire_json(dict(type="turn.completed",
            usage=dict(input_tokens=100, output_tokens=20))), encoding="utf-8")
        return dict(tool="inspect", arguments_json="{}", reason="Read the frozen assembly")

    monkeypatch.setattr(probe.CodexPlanner, "propose", propose)
    monkeypatch.setenv("CODEX_CLI_PATH", "fixture-codex")
    report = probe.evaluate(agent, attempt, 0, tmp_path / "evaluation")
    assert len(requests) == 2 and "planner_encoding" not in requests[0]
    assert "planner_encoding" in requests[1]
    assert report["same_action"] and report["same_resulting_graph"]
    assert report["source_unchanged"] and report["exact_roundtrip"]
    assert not report["production_enabled"] and not report["vehicle_acceptance"]
    assert report["variants"][0]["checkpoint_sha256"] == report["variants"][1]["checkpoint_sha256"]
    assert all(digest(Path(path)) == value for path, value in before.items())


def test_two_failed_requests_cannot_count_as_matching_outputs(tmp_path, monkeypatch):
    agent, attempt = checkpoint(tmp_path)

    def fail(*args):
        raise TimeoutError("Fixture deadline")

    monkeypatch.setattr(probe.CodexPlanner, "propose", fail)
    monkeypatch.setenv("CODEX_CLI_PATH", "fixture-codex")
    report = probe.evaluate(agent, attempt, 0, tmp_path / "evaluation")
    assert report["model_requests"] == 2
    assert not report["same_action"] and not report["same_resulting_graph"]
    assert all(row["error"]["type"] == "TimeoutError" for row in report["variants"])


def test_replay_rejects_divergent_mutation_and_external_packages(tmp_path):
    agent, attempt = checkpoint(tmp_path)
    initial = probe.read(attempt / "initial-assembly.json")
    kwargs = dict(directory=tmp_path / "replay", initial=initial, mission={}, native=tmp_path / "native",
        corpus=None, exclusions=dict(models=[], families=[], hashes=[]))
    with pytest.raises(ValueError, match="diverged"):
        probe.replay(records=[dict(step=0, decision=dict(tool="edit", arguments_json=wire_json(
            dict(revision=999, operations=[])), reason="Invalid revision"),
            observation=dict(ok=True, revision_after=1))], **kwargs)
    initial["packages"] = ["external"]
    with pytest.raises(ValueError, match="native-only"):
        probe.replay(records=[], **kwargs)


@pytest.mark.parametrize("destination", ["source", "source/nested", "ancestor", "existing"])
def test_probe_rejects_output_overlap_and_existing_paths_before_requests(tmp_path, destination):
    agent, attempt = checkpoint(tmp_path)
    output = tmp_path if destination == "ancestor" else tmp_path / destination
    if destination == "existing":
        output.mkdir()
    with pytest.raises(ValueError, match="new output"):
        probe.evaluate(agent, attempt, 0, output)


def test_probe_rejects_attempt_outside_source_before_reading_files(tmp_path):
    with pytest.raises(ValueError, match="inside the source"):
        probe.evaluate(tmp_path / "source", tmp_path / "other", 0, tmp_path / "evaluation")
