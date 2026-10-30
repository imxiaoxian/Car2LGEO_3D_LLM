"""Keep autonomous linked chassis evidence distinct from tool fixture success."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import write_json
from scripts import probe_autonomous_linkage as probe
from scripts.probe_linkage import operations


def native_root():
    root = Path(os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not root.is_dir():
        pytest.skip("Native catalog not installed")
    return root


def test_empty_scene_cannot_pass(tmp_path):
    tools = AssemblyTools(tmp_path, native_root())
    result = probe.evidence(tools)
    assert result["status"] == "fail"
    assert {"check_missing_or_stale", "linkage_missing_or_stale", "requires_four_rolling_joints",
            "requires_twenty_to_sixty_four_native_parts"} <= set(result["issues"])
    assert result["vehicle_completion_ready"] is False


@pytest.fixture(scope="module")
def chassis(tmp_path_factory):
    tools = AssemblyTools(tmp_path_factory.mktemp("autonomous-linkage"), native_root())
    built = tools.call("build", dict(revision=0, operations=operations(chassis=True)))
    assert built["ok"], built
    aliases = built["result"]["aliases"]
    assert tools.call("check", {})["result"]["status"] == "pass"
    linked = tools.call("linkage_motion", dict(driver=aliases["left"],
        passive=[aliases["right"], aliases["tie"]],
        rolling=[aliases[side + "wheel"] for side in ("left", "right", "rearleft", "rearright")]))
    assert linked["ok"] and linked["result"]["status"] == "pass", linked
    # Isolate this diagnostic's handling of a current failed ordinary-motion report.
    # This is not a measured ordinary sweep or an autonomous planner success.
    check = json.loads((tools.directory / "checks.json").read_text(encoding="utf-8"))
    write_json(tools.directory / "motion.json", {**check, "schema": "assembly-motion.v1", "status": "fail"})
    return tools


def test_fixture_does_not_establish_autonomous_selection(chassis):
    result = probe.evidence(chassis)
    assert result["status"] == "fail"
    assert "selection_not_observed_before_placement" in result["issues"]
    assert result["vehicle_completion_ready"] is False


@pytest.fixture
def selected(chassis, monkeypatch):
    monkeypatch.setattr(probe, "selection_evidence", lambda *_: dict(status="pass"))
    return chassis


def test_diagnostic_never_relabels_failed_ordinary_motion_as_vehicle_pass(selected):
    result = probe.evidence(selected)
    assert result["status"] == "pass", result
    assert result["ordinary_motion_status"] == "fail"
    assert result["vehicle_completion_ready"] is False
    assert sum(row["combined_poses"] for row in result["measurements"].values()) == 324
    for key, row in result["measurements"].items():
        assert min(row["rolling_displacement_ldu"].values()) > 1
        assert row["steering_axis_degrees"] >= 20 if key.startswith("front") else row["steering_axis_degrees"] < 1e-6


@pytest.mark.parametrize("fault,expected", [
    ("combined", "requires_each_wheel_rolling_through_all_nine_linked_poses"),
    ("passive", "linkage_must_cover_every_nonrolling_joint"),
    ("driver", "requires_nine_linked_poses_covering_minus_thirty_to_plus_thirty"),
])
def test_incomplete_linkage_evidence_fails(selected, monkeypatch, fault, expected):
    graph = deepcopy(selected.assembly.graph())
    path = selected.directory / "linkage-motion.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    changed = deepcopy(original)
    if fault == "combined":
        changed["combined"][0]["poses"].pop()
    elif fault == "passive":
        changed["passive"].pop()
    else:
        changed["poses"].pop()
    try:
        write_json(path, changed)
        assert expected in probe.evidence(selected)["issues"]
    finally:
        write_json(path, original)
    assert selected.assembly.graph() == graph


def test_stale_native_reports_and_no_actual_response_fail(selected, monkeypatch):
    graph = deepcopy(selected.assembly.graph())
    graph["instances"][0]["color"] = 4
    monkeypatch.setattr(selected.assembly, "graph", lambda: graph)
    assert {"check_missing_or_stale", "motion_missing_or_stale", "linkage_missing_or_stale"} <= set(
        probe.evidence(selected)["issues"])
    monkeypatch.undo()
    monkeypatch.setattr(probe, "selection_evidence", lambda *_: dict(status="pass"))
    monkeypatch.setattr(probe, "posed_graph", lambda *_: deepcopy(selected.assembly.graph()))
    issues = probe.evidence(selected)["issues"]
    assert {"front_wheel_actual_steering_response_missing", "actual_rim_tyre_axle_rolling_response_missing"} <= set(issues)


def test_horizontal_carrier_axis_cannot_be_called_steering(selected, monkeypatch):
    graph = deepcopy(selected.assembly.graph())
    linkage = json.loads((selected.directory / "linkage-motion.json").read_text(encoding="utf-8"))
    graph["joints"][linkage["driver"]]["axis"] = [1., 0., 0.]
    monkeypatch.setattr(selected.assembly, "graph", lambda: graph)
    # Isolate the geometry rule; production freshness rejects this edit first.
    monkeypatch.setattr(probe, "check_current", lambda *_: True)
    assert "front_steering_carrier_requires_vertical_kingpin" in probe.evidence(selected)["issues"]
