import importlib.util
import json
from pathlib import Path

import pytest

from car2lego3d.assembly_prompt_codec import pack_ports


spec = importlib.util.spec_from_file_location("inspect_assembly_run",
    Path(__file__).resolve().parents[1] / "scripts/inspect_assembly_run.py")
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def test_pending_execution_distinguishes_model_and_completed_decision(tmp_path):
    request = tmp_path / "inputs/0002.json"
    request.parent.mkdir()
    request.write_text("{}")
    state = dict(status="running", steps=2)
    assert diagnostics.pending_execution(tmp_path, state)["phase"] == "planner"

    decision = tmp_path / "planner/0002/decision.json"
    decision.parent.mkdir(parents=True)
    decision.write_text(json.dumps(dict(tool="search_parts")))
    result = diagnostics.pending_execution(tmp_path, state)
    assert result["phase"] == "tool_or_checkpoint"
    assert result["tool"] == "search_parts"
    assert result["evidence"] == str(decision)
    assert result["elapsed_seconds"] >= 0


def test_pending_execution_does_not_reuse_prior_decision(tmp_path):
    decision = tmp_path / "planner/0001/decision.json"
    decision.parent.mkdir(parents=True)
    decision.write_text(json.dumps(dict(tool="motion")))
    assert diagnostics.pending_execution(tmp_path, dict(status="running", steps=2)) == dict(
        step=2, phase="checkpoint_or_context")
    assert diagnostics.pending_execution(tmp_path, dict(status="draft", steps=1)) is None
    assert diagnostics.pending_execution(None, dict(status="running")) is None


def test_part_diagnosis_accepts_raw_and_integrity_checked_port_tables():
    ports = [dict(id=str(i), kind="axle", position=[0, 0, i]) for i in range(20)]
    packed = pack_ports(ports)
    assert isinstance(packed, dict)
    for value in (ports, packed):
        result = diagnostics.part_diagnosis("rim.dat", dict(ports=value))
        assert result["ports"] == 20
        assert result["port_kinds"] == {"axle": 20}
    packed["rows"][0][0] = "tampered"
    with pytest.raises(ValueError, match="integrity"):
        diagnostics.part_diagnosis("rim.dat", dict(ports=packed))


def test_decision_timings_preserve_usage_and_distinguish_intervals(tmp_path):
    import os

    requests = tmp_path / "inputs"
    requests.mkdir()
    request = requests / "0002.json"
    request.write_text("{}")
    following = requests / "0003.json"
    following.write_text("{}")
    folder = tmp_path / "planner/0002"
    folder.mkdir(parents=True)
    decision = folder / "decision.json"
    decision.write_text(json.dumps(dict(tool="query_part")))
    usage = dict(input_tokens=100, cached_input_tokens=80, output_tokens=10)
    (folder / "events.jsonl").write_text(json.dumps(dict(type="turn.completed", usage=usage)) + "\n{unfinished")
    for path, timestamp in ((request, 100), (decision, 130), (following, 142)):
        os.utime(path, (timestamp, timestamp))
    assert diagnostics.decision_timings(tmp_path) == [dict(step=2, tool="query_part",
        request_to_decision_seconds=30.0, decision_to_next_input_seconds=12.0, usage=usage)]
    assert diagnostics.decision_timings(None) == []


def test_motion_summary_keeps_missing_wheels_separate_from_passing_carrier_poses():
    from copy import deepcopy

    response = dict(steering=dict(status="fail", wheels=[dict(module="wheel_front_left",
        status="fail", reason="wheel_not_installed")]))
    report = dict(revision=47, status="fail", mechanism_response=response,
        combined_joint_states="not_applicable", joints=[dict(instance="root/carrier", purpose="steering",
            limits=[-25, 25], unit="degrees", poses=[dict(value=n, status="pass") for n in range(9)])])
    original = deepcopy(report)
    result = diagnostics.motion_diagnosis(report, 47)
    assert result["current"] and result["status"] == "fail"
    assert result["mechanism_response"] == response
    assert result["joints"][0]["samples"] == 9
    assert result["joints"][0]["nonpassing_poses"] == 0
    assert not diagnostics.motion_diagnosis(report, 48)["current"]
    assert report == original


def test_motion_diagnostics_bound_pose_details_without_hiding_totals_or_combined_failures():
    poses = [dict(value=i, status="unverified" if i % 2 else "fail", collisions=[{}],
        unknown=2, invalid=["root/bad"], connectivity="fail") for i in range(20)]
    report = dict(revision=2, status="fail", joints=[dict(instance="root/wheel", poses=poses,
        range_issues=["rolling_requires_360_degrees"])], combined=[dict(instances=["steering", "suspension"],
        poses=[dict(status="pass"), dict(status="fail"), dict(status="unverified")])])
    result = diagnostics.motion_diagnosis(report, 2)
    joint = result["joints"][0]
    assert joint["nonpassing_poses"] == 20 and joint["pose_failures_omitted"] == 8
    assert len(joint["pose_failures"]) == 12
    assert joint["pose_failures"][0] == dict(value=0, status="fail", collisions=1,
        unknown=2, invalid=["root/bad"], connectivity="fail")
    assert joint["range_issues"] == ["rolling_requires_360_degrees"]
    assert result["combined"][0]["statuses"] == {"pass": 1, "fail": 1, "unverified": 1}
    assert diagnostics.motion_diagnosis({}, 2) is None


def test_inspection_does_not_present_old_structural_check_as_current(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(dict(revision=8)))
    (tmp_path / "checks.json").write_text(json.dumps(dict(revision=7, status="pass")))
    result = diagnostics.inspect(tmp_path)
    assert result["structural_check"]["status"] == "pass"
    assert not result["structural_check"]["current"]
    assert result["motion_check"] is None


def test_motion_collision_pairs_aggregate_across_pose_order():
    report = dict(revision=1, joints=[dict(instance="root/wheel", poses=[
        dict(status="fail", collisions=[dict(a="wheel", b="chassis")]),
        dict(status="fail", collisions=[dict(a="chassis", b="wheel")]),
        dict(status="pass", collisions=[])])])
    joint = diagnostics.motion_diagnosis(report, 1)["joints"][0]
    assert joint["collision_pairs"] == [dict(a="chassis", b="wheel", occurrences=2)]
    assert joint["collision_pairs_omitted"] == 0
