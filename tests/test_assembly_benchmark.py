from collections import Counter
from copy import deepcopy
from types import SimpleNamespace

import pytest

from car2lego3d import assembly_benchmark as bench
from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_training_data import part_signatures
from car2lego3d.common import digest, write_json


def source(family, module):
    return dict(source=dict(family=family, families=[family], module_hashes=[module]))


@pytest.mark.parametrize("same_family", [True, False])
def test_heldout_rejects_family_or_module_leakage(same_family):
    rows = dict(train=[source("a", "x")], validation=[],
                test=[source("a" if same_family else "b", "y" if same_family else "x")])
    with pytest.raises(ValueError, match="leakage"):
        bench.split_isolation(rows)


def test_measure_requires_exact_multiplicity_and_explicit_finish(tmp_path):
    assembly = Assembly("1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n")
    tools = SimpleNamespace(directory=tmp_path, assembly=assembly)
    expected = part_signatures(assembly.graph())
    state = dict(checks=dict(result=dict(status="pass")), stop_reason="planner_finished", elapsed_seconds=1)
    assert not bench.measure(expected, tools, state)["execution_pass"]
    from zipfile import ZipFile
    with ZipFile(tmp_path / "model.io", "w") as archive:
        archive.writestr("model.ldr", assembly.text())
    assert bench.measure(expected, tools, state)["execution_pass"]
    assert not bench.measure(expected + expected, tools, state)["execution_pass"]
    state["stop_reason"] = "tool_budget_exhausted"
    assert not bench.measure(expected, tools, state)["execution_pass"]


def test_native_identity_includes_recursive_geometry(tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "p").mkdir()
    parent = tmp_path / "parts/parent.dat"
    child = tmp_path / "p/child.dat"
    parent.write_text("1 16 0 0 0 1 0 0 0 1 0 0 0 1 child.dat\n")
    child.write_text("3 16 0 0 0 1 0 0 0 1 0\n")
    expected = Counter({bench.wire_json(["parent.dat", 4, []]): 1})
    before = bench.native_identity(tmp_path, expected)
    child.write_text("3 16 0 0 0 2 0 0 0 1 0\n")
    after = bench.native_identity(tmp_path, expected)
    assert before["parts"] == after["parts"]
    assert before["geometry_dependencies"] != after["geometry_dependencies"]


def test_comparison_rejects_reported_metrics_and_missing_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "validate_suite", lambda *args: None)
    monkeypatch.setattr(bench, "validate", lambda *args: dict(status="unverified"))
    assembly = Assembly("0 FILE main.ldr\n0 NOFILE\n")
    suite = dict(tasks=[dict(id="task", expected={})], budgets={})
    state = dict(contract={}, planner="codex", checks={}, elapsed_seconds=1, stop_reason="tool_budget_exhausted")
    contract = dict(agent={}, suite_sha256=bench.fingerprint(suite), task="task", budgets={})
    saved = dict(text=assembly.text(), source_sha256=assembly.source_hash, revision=0, joints={})
    for name, content in (("agent.json", state), ("benchmark-input.json", contract), ("state.json", saved)):
        write_json(tmp_path / name, content)
    (tmp_path / "agent-decisions.jsonl").write_text("")
    metrics = bench.measure({}, SimpleNamespace(directory=tmp_path, assembly=assembly), state)
    result = dict(task="task", planner="codex", suite_sha256=bench.fingerprint(suite),
        input_hash=bench.fingerprint(contract), output=str(tmp_path), metrics=metrics,
        artifacts={p.name:digest(p) for p in tmp_path.iterdir()}, identity_unchanged=True,
        identity_before={}, identity_after={})
    report = bench.compare_results(suite, [result], None, tmp_path)
    assert report["winner"] is None and not report["default_changed"]
    changed = deepcopy(result)
    changed["metrics"]["execution_pass"] = True
    with pytest.raises(ValueError, match="metrics"):
        bench.compare_results(suite, [changed], None, tmp_path)
    del changed["artifacts"]["agent.json"]
    with pytest.raises(ValueError, match="Missing"):
        bench.compare_results(suite, [changed], None, tmp_path)
