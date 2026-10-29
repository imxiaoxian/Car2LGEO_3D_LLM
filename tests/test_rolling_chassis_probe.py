"""A chassis diagnostic must prove wheel motion, stance and fixed supports together."""
from copy import deepcopy
import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_tools import AssemblyTools
from scripts.probe_rolling_chassis import chassis_evidence, demonstration_operations


@pytest.fixture(scope="module")
def chassis(tmp_path_factory):
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    tools = AssemblyTools(tmp_path_factory.mktemp("rolling-chassis"), native)
    result = tools.call("build", dict(revision=0, operations=demonstration_operations()))
    assert result["ok"], result
    assert tools.call("check", {})["result"]["status"] == "pass"
    assert tools.call("motion", dict(samples=9))["result"]["status"] == "pass"
    return tools


def test_four_wheels_rotate_independently_above_a_common_ground(chassis):
    evidence = chassis_evidence(chassis)
    assert evidence["status"] == "pass", evidence
    assert len(evidence["wheels"]) == 4
    assert {row["poses"] for row in evidence["wheels"]} == {9}
    assert {row["span_degrees"] for row in evidence["wheels"]} == {360}
    layout = evidence["layout"]
    assert layout["status"] == "pass" and layout["complete"]
    np.testing.assert_allclose(sorted(row["center_ldu"] for row in layout["wheels"].values()),
        [(-60, -14, -80), (-60, -14, 80), (60, -14, -80), (60, -14, 80)], atol=1e-8)
    assert min(row["ground_y_ldu"] for row in layout["wheels"].values()) > layout["chassis"]["bottom_y_ldu"]
    restored = AssemblyTools(chassis.directory, chassis.catalog_root)
    assert chassis_evidence(restored) == evidence


def test_chassis_evidence_rejects_graph_change_and_missing_motion(chassis, monkeypatch):
    graph = deepcopy(chassis.assembly.graph())
    graph["instances"][0]["color"] = 4
    monkeypatch.setattr(chassis.assembly, "graph", lambda: graph)
    assert "check_missing_failed_or_stale" in chassis_evidence(chassis)["issues"]
    monkeypatch.undo()
    path = chassis.directory / "motion.json"
    with monkeypatch.context() as patch:
        exists = Path.is_file
        patch.setattr(Path, "is_file", lambda self: False if self == path else exists(self))
        assert "motion_missing_failed_or_stale" in chassis_evidence(chassis)["issues"]


def test_inboard_wheels_fail_native_collision_and_rollback(tmp_path, chassis):
    tools = AssemblyTools(tmp_path, chassis.catalog_root)
    operations = demonstration_operations()
    for operation in operations:
        if operation["op"] == "module" and operation["alias"] != "chassis":
            operation["matrix"][2][3] *= .5
    before = tools.assembly.graph()
    result = tools.call("build", dict(revision=0, operations=operations))
    assert not result["ok"]
    assert "collisions" in result["error"]
    assert tools.assembly.graph() == before
    assert not (tools.directory / "state.json").exists()


def test_zero_rotation_geometry_cannot_qualify(chassis, monkeypatch):
    from scripts import probe_rolling_chassis as probe
    original = chassis.assembly.graph()
    monkeypatch.setattr(probe, "posed_graph", lambda graph, positions: deepcopy(original))
    evidence = chassis_evidence(chassis)
    assert evidence["status"] == "fail"
    assert "actual_rim_tyre_and_axle_geometry_must_rotate" in evidence["issues"]


def test_autonomous_check_uses_native_roles_instead_of_fixed_inventory(chassis, monkeypatch):
    from scripts import probe_rolling_chassis as probe
    monkeypatch.setattr(probe, "PARTS", {})
    measured = chassis_evidence(chassis, autonomous=True)
    assert measured["status"] == "pass", measured
    assert len(measured["native_descriptions"]) == 5


def test_autonomous_check_rejects_false_roles(chassis, monkeypatch):
    from scripts import probe_autonomous_wheel as probe
    monkeypatch.setattr(probe, "native_role", lambda catalog, part: ("axle", "incorrect label"))
    measured = chassis_evidence(chassis, autonomous=True)
    assert measured["status"] == "fail"
    assert "rolling_subtree_requires_one_rim_tyre_and_axle_only" in measured["issues"]


def test_fixed_part_chassis_does_not_prove_autonomous_selection(chassis):
    from scripts.probe_autonomous_chassis import evidence
    measured = evidence(chassis)
    assert measured["chassis"]["status"] == "pass"
    assert measured["status"] == "fail"
    assert "selection_not_observed_before_placement" in measured["issues"]
    assert set(measured["selection"]["missing_prior_selection"]) == set(
        node["part"] for node in chassis.assembly.graph()["instances"] if node["kind"] == "part")
