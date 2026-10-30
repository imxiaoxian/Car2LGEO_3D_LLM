"""Isolate door diagnostic rules; synthetic fixtures are not native acceptance."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from car2lego3d.assembly_motion import evaluate_motion
from car2lego3d.common import write_json
from scripts import probe_countach_door as probe


@pytest.fixture
def trial(tmp_path, monkeypatch):
    def node(key, parent="root", part=None, color=0, position=(0, 0, 0)):
        matrix = np.eye(4)
        matrix[:3, 3] = position
        return dict(id=key, parent=parent, kind="part" if part else "module",
                    part=part or key + ".ldr", color=color, local=matrix.tolist(), world=matrix.tolist())
    nodes = [node("mount"), node("bearing", "mount", "bearing.dat"), node("door"),
             node("panel", "door", "panel.dat", 15, (40, 20, 0)),
             node("glass", "door", "glass.dat", 40, (40, -20, 0)),
             node("hinge", "door", "hinge.dat")]
    graph = dict(revision=3, instances=nodes, joints=dict(door=dict(
        type="revolute", purpose="door", axis=[0, 0, -1], origin=[0, 0, 0], limits=[0, 90])))
    check = dict(status="pass", collisions=[], unknown=[], invalid_transforms=[], gates={
        key: "pass" for key in ("parts", "transforms", "connections", "collisions", "digital_assembly")})
    motion = evaluate_motion(graph, lambda _: deepcopy(check))
    write_json(tmp_path / "checks.json", check)
    write_json(tmp_path / "motion.json", motion)
    library = SimpleNamespace(triangles=lambda _: (np.array([[[0, 0, 0], [20, 0, 0], [0, 10, 0]]]), []))
    tools = SimpleNamespace(directory=tmp_path, catalog_root=tmp_path, catalog=SimpleNamespace(library=library),
        assembly=SimpleNamespace(graph=lambda: graph), module_bindings=dict(door_left=dict(
            roots=["door"], roles=dict(panel=["panel"], glass=["glass"], hinge=["hinge"]))))
    monkeypatch.setattr(probe, "module_check", lambda *_a, **_k: dict(status="pass"))
    monkeypatch.setattr(probe, "selection_evidence", lambda *_a: dict(status="pass"))
    monkeypatch.setattr(probe, "check_current", lambda *_a: True)
    monkeypatch.setattr(probe, "palette", lambda *_a: {15: [255, 255, 255, 255], 40: [255, 255, 255, 128]})
    return tools, {node["id"]: deepcopy(node) for node in nodes[:2]}, graph, motion


def test_panel_glass_native_response_and_fixed_mount(trial):
    result = probe.evidence(*trial[:2])
    assert result["status"] == "pass", result
    assert result["tested_poses"] == 9
    assert all(row["endpoint_mean_upward_displacement_ldu"] > 1 for row in result["measurements"].values())
    assert result["vehicle_completion_ready"] is False
    assert result["structural_comparison"] == result["physical_retention"] == "unverified"


@pytest.mark.parametrize("fault", ["duplicate", "missing", "failed"])
def test_nine_records_do_not_replace_nine_distinct_passing_positions(trial, fault):
    tools, protected, _, motion = trial
    if fault == "duplicate":
        motion["joints"][0]["poses"] = [motion["joints"][0]["poses"][0]] * 9
    elif fault == "missing":
        motion["joints"][0]["poses"].pop()
    else:
        motion["joints"][0]["poses"][4]["status"] = "fail"
    write_json(tools.directory / "motion.json", motion)
    issue = ("motion_missing_failed_or_stale" if fault == "failed"
             else "requires_nine_distinct_passing_poses_covering_joint_limits")
    assert issue in probe.evidence(tools, protected)["issues"]


@pytest.mark.parametrize("fault,issue", [
    ("axis", "scissor_door_requires_native_transverse_revolute_axis"),
    ("range", "door_must_open_ninety_degrees_from_neutral"),
    ("down", "panel_native_geometry_must_rise_with_door:panel"),
    ("white", "countach_exterior_panel_must_be_white"),
    ("ownership", "selected_parts_must_share_door_submodel_with_fixed_mount_outside"),
    ("support", "fixed_mount_changed")])
def test_door_labels_cannot_replace_actual_geometry_and_ownership(trial, fault, issue):
    tools, protected, graph, _ = trial
    if fault == "axis":
        graph["joints"]["door"]["axis"] = [0, 1, 0]
    elif fault == "range":
        graph["joints"]["door"]["limits"] = [0, 30]
    elif fault == "down":
        graph["joints"]["door"]["axis"] = [0, 0, 1]
    elif fault == "white":
        graph["instances"][3]["color"] = 4
    elif fault == "ownership":
        graph["instances"][4]["parent"] = "root"
    else:
        graph["instances"][1]["color"] = 4
    assert issue in probe.evidence(tools, protected)["issues"]


@pytest.mark.parametrize("alpha", [0, 255])
def test_glass_must_be_visible_and_transparent(trial, monkeypatch, alpha):
    monkeypatch.setattr(probe, "palette", lambda *_: {40: [255, 255, 255, alpha]})
    assert "glass_requires_visible_native_transparency" in probe.evidence(*trial[:2])["issues"]


def test_failed_native_evidence_cannot_be_promoted(trial, monkeypatch):
    monkeypatch.setattr(probe, "check_current", lambda *_: False)
    monkeypatch.setattr(probe, "module_check", lambda *_a, **_k: dict(status="unverified"))
    assert {"check_missing_failed_or_stale", "motion_missing_failed_or_stale",
            "door_module_not_mechanically_passed"} <= set(probe.evidence(*trial[:2])["issues"])


@pytest.mark.parametrize("early_selection", [True, False])
def test_seeded_part_type_still_requires_selection_before_its_new_moving_instance(trial, monkeypatch, early_selection):
    from scripts.probe_autonomous_wheel import selection_evidence
    tools, protected, graph, _ = trial
    graph["instances"][1]["part"] = protected["bearing"]["part"] = "panel.dat"
    trace = [dict(ok=True, tool="build", arguments=dict(operations=[dict(op="add", part="panel.dat")]))]
    if not early_selection:
        trace.append(dict(ok=True, tool="attach", arguments=dict(part="panel.dat")))
    for part in ("panel.dat", "glass.dat", "hinge.dat"):
        trace.extend([
            dict(ok=True, tool="search_parts", arguments=dict(query="Native structural element"),
                 result=dict(parts=[dict(part=part)])),
            dict(ok=True, tool="query_part", result=dict(status="pass", part=part)),
            dict(ok=True, tool="attach", arguments=dict(part=part))])
    monkeypatch.setattr(probe, "records", lambda _: trace)
    monkeypatch.setattr(probe, "selection_evidence", selection_evidence)
    result = probe.evidence(tools, protected, selection_start=1)
    assert (result["status"] == "pass") is early_selection
    assert result["selection_trace_start"] == 1
    assert probe.evidence(tools, protected)["selection"]["status"] == "fail"


def test_missing_whole_vehicle_wheels_do_not_rewrite_native_door_results(trial):
    import json
    tools, protected, _, motion = trial
    check = json.loads((tools.directory / "checks.json").read_text(encoding="utf-8"))
    check["status"] = "unverified"
    check["gates"]["wheel_layout"] = "pending"
    write_json(tools.directory / "checks.json", check)
    motion.update(status="fail", mechanism_response=dict(steering=dict(
        status="fail", wheels=[dict(status="fail", reason="wheel_not_installed")])))
    write_json(tools.directory / "motion.json", motion)
    result = probe.evidence(tools, protected)
    assert result["status"] == "pass", result
    assert result["host_check_status"] == "unverified"
    assert result["host_motion_status"] == "fail"
    assert json.loads((tools.directory / "motion.json").read_text(encoding="utf-8"))["status"] == "fail"


@pytest.mark.parametrize("status", ["pass", "fail", "unverified", "missing"])
def test_combined_motion_checks_nested_poses(trial, status):
    tools, protected, _, motion = trial
    motion["combined"] = [dict(instances=["door", "other"],
        poses=[] if status == "missing" else [dict(status=status)])]
    write_json(tools.directory / "motion.json", motion)
    assert (probe.evidence(tools, protected)["status"] == "pass") is (status == "pass")


@pytest.mark.parametrize("fault", ["native_check", "independent_check", "motion", "range", "missing_pose",
                                  "unknown_purpose", "wheel_response"])
def test_pending_vehicle_requirements_never_hide_native_failures(trial, fault):
    import json
    tools, protected, _, motion = trial
    check = json.loads((tools.directory / "checks.json").read_text(encoding="utf-8"))
    check["status"] = "unverified"
    check["gates"]["wheel_layout"] = "pending"
    motion.update(status="fail", mechanism_response=dict(steering=dict(
        status="fail", wheels=[dict(status="fail", reason="wheel_not_installed")])))
    if fault == "native_check":
        check["gates"]["collisions"] = "fail"
    elif fault == "independent_check":
        check["gates"]["bricklayer"] = "unverified"
    elif fault == "motion":
        motion["joints"][0]["poses"][4]["status"] = "fail"
    elif fault == "range":
        motion["joints"][0]["range_issues"] = ["invalid_range"]
    elif fault == "missing_pose":
        motion["joints"][0]["poses"] = []
    elif fault == "unknown_purpose":
        motion["unknown_joint_purposes"] = ["door"]
    else:
        motion["mechanism_response"]["steering"]["wheels"][0]["reason"] = "no_axis_response"
    write_json(tools.directory / "checks.json", check)
    write_json(tools.directory / "motion.json", motion)
    assert probe.evidence(tools, protected)["status"] == "fail"
