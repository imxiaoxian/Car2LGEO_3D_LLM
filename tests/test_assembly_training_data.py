import json
import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_agent import AssemblyAgent, wire_json
from car2lego3d.assembly_corpus import Corpus
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.assembly_training_data import build, grouped_splits, replay_run
from car2lego3d.common import digest, write_json
from test_assembly_corpus import fixture_corpus


@pytest.fixture
def recorded(tmp_path):
    native = Path(os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    corpus, source = fixture_corpus(tmp_path)
    identity = corpus.db.execute("SELECT id FROM modules WHERE model_id='0'").fetchone()[0]
    assert corpus.review_source(identity, source, native, reviewer="fixture", notes="Not real training data")["status"] == "approved"
    frozen = corpus.freeze()
    corpus.db.close()
    corpus = Corpus(tmp_path / "corpus", snapshot=frozen["snapshot"])
    tools = AssemblyTools(tmp_path / "run", native, corpus)

    class FixturePlanner:
        name = "codex"

        def __init__(self):
            self.decisions = iter([
                ("graft", dict(revision=0, module_id=identity, matrix=np.eye(4).tolist())),
                ("edit", dict(revision=1, operations=[dict(op="color", instance="PLACEHOLDER", color=4)])),
                ("export", {}), ("finish", {})])

        def propose(self, payload, step):
            tool, arguments = next(self.decisions)
            if tool == "edit":
                arguments["operations"][0]["instance"] = next(n["id"] for n in tools.assembly.graph()["instances"] if n["kind"] == "part")
            return dict(tool=tool, arguments_json=wire_json(arguments), reason="Constructed fixture only")

    state = AssemblyAgent(tools, FixturePlanner(), dict(request=dict(goal="Fixture source replay")), max_steps=4).run()
    assert state["stop_reason"] == "planner_finished" and state["digital_status"] == "pass"
    item = dict(directory=str(tools.directory), source_modules=[identity])
    yield item, corpus, native
    corpus.db.close()


def rewrite_trace(item, change):
    directory = Path(item["directory"])
    path = directory / "agent-decisions.jsonl"
    rows = [json.loads(r) for r in path.read_text(encoding="utf-8").splitlines()]
    change(directory, rows)
    path.write_text("".join(wire_json(r) + "\n" for r in rows), encoding="utf-8")


def test_replay_success_and_duplicate_recovery_has_no_extra_weight(recorded, tmp_path):
    item, corpus, native = recorded
    report = build([item, item], corpus, native, tmp_path / "dataset")
    assert not report["rejected"]
    assert sum(report["samples"].values()) == 2
    assert report["duplicate_samples"] == 2
    for path in (tmp_path / "dataset").glob("*.jsonl"):
        for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
            assert digest(path.parent / row["evidence"]["path"]) == row["evidence"]["sha256"]


@pytest.mark.parametrize("corruption", ["input", "observation", "late_context"])
def test_replay_rejects_corruption_without_publishing_partial_samples(recorded, tmp_path, corruption):
    item, corpus, native = recorded

    def change(directory, rows):
        if corruption == "input":
            (directory / rows[0]["input_file"]).write_bytes(b"changed")
        elif corruption == "observation":
            rows[0]["observation"]["revision_after"] = 99
        else:
            row = rows[1]
            path = directory / row["input_file"]
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["state"]["instances"] = 999
            write_json(path, payload)
            row.update(input_sha256=digest(path), input_text=wire_json(payload))

    rewrite_trace(item, change)
    destination = tmp_path / "evidence-output"
    with pytest.raises(ValueError):
        replay_run(item, corpus, native, destination)
    assert not destination.exists()


def test_replay_rejects_dependencies_that_changed_after_initial_capture(recorded, tmp_path):
    item, corpus, native = recorded
    directory = Path(item["directory"])
    initial_path = directory / "initial-assembly.json"
    initial = json.loads(initial_path.read_text(encoding="utf-8"))
    initial["dependencies"] = {"parts/3001.dat": "wrong"}
    write_json(initial_path, initial)
    state_path = directory / "agent.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["contract"]["initial_assembly_sha256"] = digest(initial_path)
    from hashlib import sha256
    state["input_hash"] = sha256(json.dumps(state["contract"], sort_keys=True).encode()).hexdigest()
    write_json(state_path, state)
    with pytest.raises(ValueError, match="dependency hashes"):
        replay_run(item, corpus, native, tmp_path / "output")


def test_family_and_nested_hash_components_stay_in_one_split():
    rows = [dict(source=dict(family="A", families=["A"], module_hashes=["one", "nested"])),
            dict(source=dict(family="B", families=["B"], module_hashes=["nested"])),
            dict(source=dict(family="B", families=["B"], module_hashes=["three"]))]
    splits, groups = grouped_splits(rows)
    assert len(groups) == 1 and groups[0]["samples"] == 3
    assert sum(bool(v) for v in splits.values()) == 1
