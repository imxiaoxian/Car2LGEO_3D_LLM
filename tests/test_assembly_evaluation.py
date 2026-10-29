"""Promotion gates use measured quality, never export success alone."""
from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_evaluation import CORE_CASES, collect_case, compare
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import write_json


def measurements():
    return [dict(case=case, planner=planner, input_hash=case + "-shared-contract",
                 gates={gate: "pass" for gate in ("digital", "motion", "archive", "completeness", "appearance", "studio")},
                 appearance_score=score, tool_success_rate=1, hard_failures=0, repairs=0, seconds=10)
            for case in sorted(CORE_CASES)
            for planner, score in (("codex", 90), ("local_base", 50), ("local_finetuned", 80))]


def test_competing_planners_selected_by_quality_and_failure_gates():
    rows = measurements()
    assert compare(rows)["winner"] == "codex"
    for row in rows:
        if row["planner"] == "local_finetuned":
            row["appearance_score"] = 95
    assert compare(rows)["winner"] == "local_finetuned"
    rows[2]["gates"]["digital"] = "fail"
    rows[2]["hard_failures"] = 1
    assert compare(rows)["winner"] == "codex"


def test_historical_roster_cannot_substitute_for_current_four_vehicle_evaluation():
    historical = measurements()
    replacements = {"nissan-r34": "lotus-for-me", "bugatti-chiron-pur-sport": "bmw-m4"}
    for row in historical:
        row["case"] = replacements.get(row["case"], row["case"])
    current = compare(historical)
    assert current["winner"] is None
    assert current["required_cases"] == ["bugatti-chiron-pur-sport", "countach", "f40", "nissan-r34"]
    assert "Missing complete planner comparison: bugatti-chiron-pur-sport" in current["reasons"]
    assert "Missing complete planner comparison: nissan-r34" in current["reasons"]
    historical_report = compare(historical, {"countach", "f40", "bmw-m4", "lotus-for-me"})
    assert historical_report["winner"] == "codex"


@pytest.mark.parametrize("change", ["missing", "inputs", "unverified", "tie", "slower-only"])
def test_incomplete_or_equal_quality_cannot_promote(change):
    rows = measurements()
    if change == "missing":
        rows.pop()
    elif change == "inputs":
        rows[0]["input_hash"] = "different"
    elif change == "unverified":
        rows[0]["gates"]["motion"] = "unverified"
    else:
        for row in rows:
            if row["planner"] == "local_finetuned":
                row["appearance_score"] = 90
                row["seconds"] = 100 if change == "slower-only" else 10
    report = compare(rows)
    assert report["status"] == "pending" and report["winner"] is None


@pytest.mark.parametrize("field,value", [("planner", "invented"), ("case", "unknown"),
    ("input_hash", ""), ("hard_failures", .5), ("hard_failures", 1),
    ("seconds", float("nan")), ("appearance_score", 101), ("repairs", True)])
def test_invalid_comparison_records_are_rejected(field, value):
    rows = measurements()
    rows[0][field] = value
    with pytest.raises(ValueError):
        compare(rows)


def test_empty_cases_and_duplicate_results_rejected():
    with pytest.raises(ValueError):
        compare(measurements(), set())
    rows = measurements()
    with pytest.raises(ValueError, match="Duplicate"):
        compare(rows + [deepcopy(rows[0])])


@pytest.fixture
def exported(tmp_path):
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", r"D:\lego\Studio 2.0\ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    tools = AssemblyTools(tmp_path, native)
    assert tools.call("edit", dict(revision=0, operations=[dict(op="add", part="3001.dat",
        color=4, matrix=np.eye(4).tolist())]))["ok"]
    check, motion = tools.call("check", {}), tools.call("motion", {"samples": 9})
    exported = tools.call("export", {})
    assert exported["ok"]
    write_json(tmp_path / "agent.json", dict(planner="codex", input_hash="fixture-contract", status="draft",
        checks=check, motion=motion, export=exported, elapsed_seconds=1))
    (tmp_path / "agent-decisions.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def test_export_only_keeps_motion_and_appearance_unverified(exported):
    row = collect_case(exported, "countach")
    assert row["gates"]["archive"] == "pass"
    assert row["gates"]["digital"] == "pass"
    assert row["gates"]["motion"] == "unverified"
    assert row["gates"]["appearance"] == "unverified"
    assert row["gates"]["completeness"] == "unverified"


def test_three_part_or_legacy_records_without_completeness_cannot_promote():
    rows = measurements()
    for row in rows:
        row["gates"].pop("completeness")
    assert compare(rows)["winner"] is None


@pytest.mark.parametrize("filename,field,value", [("checks.json", "revision", 0),
    ("agent.json", "status", "running"), ("assembly-graph.json", "revision", 3)])
def test_stale_or_unfinished_measurements_rejected(exported, filename, field, value):
    path = exported / filename
    value_object = json.loads(path.read_text(encoding="utf-8"))
    value_object[field] = value
    write_json(path, value_object)
    with pytest.raises(ValueError):
        collect_case(exported, "countach")
