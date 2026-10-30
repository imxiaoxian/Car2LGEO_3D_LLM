"""Keep executed joint state and host task failures visible between decisions."""
from copy import deepcopy

import numpy as np
import pytest

from car2lego3d.assembly_agent import AssemblyAgent, CodexPlanner
from car2lego3d.assembly_inspection import planning_evidence
from car2lego3d.assembly_prompt_codec import unpack_payload
from car2lego3d.assembly_tools import AssemblyTools


@pytest.fixture(params=["object", "codex", "local_base", "local_finetuned"])
def scoped_agent(request, tmp_path):
    tools = AssemblyTools(tmp_path / "assembly", tmp_path / "native")
    if request.param == "object":
        backend = CodexPlanner(tmp_path / "planner", executable="fixture-cli")
    else:
        backend = type("Backend", (), dict(name=request.param, vehicle_context=dict(
            contact_sheet=dict(file="fixture.png", sha256="fixture"))))()
    return AssemblyAgent(tools, backend, {}, finalize=False)


def test_current_joints_survive_instance_truncation_and_later_reads(scoped_agent):
    agent, matrix = scoped_agent, np.eye(4).tolist()
    tools = agent.tools
    created = tools.call("edit", dict(revision=0, operations=[
        dict(op="module", name="carrier.ldr", matrix=matrix)]))
    assert created["ok"]
    carrier = created["result"]["instances"][0]["id"]
    joint = dict(op="joint", instance=carrier, axis=[0, -1, 0], origin=[2, 4, 6],
                 type="revolute", purpose="steering", limits=[-30, 30])
    edited = tools.call("edit", dict(revision=1, operations=[
        dict(op="add", parent=carrier, part="3001.dat", color=4, matrix=matrix),
        *[dict(op="add", part="3001.dat", color=4, matrix=matrix) for _ in range(101)],
        joint]))
    assert edited["ok"]
    read = tools.call("context", dict(resource="graph:/revision"))
    payload = unpack_payload(agent.context([
        dict(step=0, decision=dict(tool="context"), observation=read)], 20))
    summary = payload["graph_summary"]
    assert summary["omitted_instances"] > 0
    assert carrier not in {node["id"] for node in summary["instances"]}
    assert summary["joints"] == tools.assembly.graph()["joints"] == {carrier: joint}
    assert payload["state"]["revision"] == 2

    changed = dict(joint, limits=[-45, 45])
    assert tools.call("edit", dict(revision=2, operations=[changed]))["ok"]
    fresh = unpack_payload(agent.context([], 20))
    assert fresh["graph_summary"]["joints"] == {carrier: changed}
    assert fresh["state"]["revision"] == 3
    assert summary["joints"][carrier]["limits"] == [-30, 30]


def test_module_task_summary_survives_intervening_reads_without_claiming_acceptance(scoped_agent):
    agent = scoped_agent
    task = dict(status="fail", issues=["physical_tie_missing", "motion_missing_or_stale"],
        vehicle_completion_ready=False, selection=dict(parts={"large": "x" * 10000}))
    observations = agent.tools.context_resources["observations"]
    observations["7"] = dict(tool="module_progress", ok=True, revision_after=0,
        result=dict(status="unverified", completion_ready=False, module_task=task))
    read = agent.tools.call("context", dict(resource="guide:/start"))
    payload = unpack_payload(agent.context([
        dict(step=8, decision=dict(tool="context"), observation=read)], 20))
    retained = payload["known_evidence"]["diagnostics"]["module_task"]
    assert retained["status"] == "fail" and retained["issues"] == task["issues"]
    assert retained["revision_matches"] and retained["revision"] == 0
    assert retained["vehicle_completion_ready"] is False
    assert retained["evidence_current"] == "recheck_native_dependencies_before_acceptance"
    assert retained["full_resource"] == "observations"
    assert retained["pointer"] == "/7/result/module_task" and retained["summary_only"]
    assert "selection" not in retained


def test_task_summary_retains_omission_counts_staleness_and_original_observations():
    task = dict(module="front", status="unverified", issues=[str(i) for i in range(20)],
        failures=[str(i) for i in range(15)], candidates=dict(status="unverified", required=3,
            passing=2, selected=None, reason="three_distinct_verified_structures_required",
            candidates=[dict(id="large", data="x" * 10000)]))
    observations = {"4": dict(tool="module_progress", ok=True, revision_after=7,
        result=dict(module_task=task)), "5": dict(tool="module_progress", ok=False,
        revision_after=7, result=dict(module_task=dict(status="pass")))}
    original = deepcopy(observations)
    summary = planning_evidence(observations, revision=8)["diagnostics"]["module_task"]
    assert summary["status"] == "unverified" and not summary["revision_matches"]
    assert summary["issues"] == task["issues"][:12] and summary["issues_omitted"] == 8
    assert summary["failures"] == task["failures"][:12] and summary["failures_omitted"] == 3
    assert summary["candidates"] == {key: value for key, value in task["candidates"].items()
        if key != "candidates"}
    assert observations == original
    assert not planning_evidence(observations)["diagnostics"]["module_task"]["revision_matches"]


def test_linkage_failures_remain_visible_separately_from_ordinary_motion():
    contact = [["root/left/pin", "1"], ["root/tie/beam", "5"]]
    poses = [dict(positions={"root/left": i, "root/right": 0}, status="fail",
        converged=True, residual_max=20, passive_rank=2, lost_contacts=[contact] * 15,
        check=dict(status="pass", large="x" * 10000)) for i in range(15)]
    report = dict(revision=12, status="fail", issue="driver_coupling_not_established",
        driver="root/left", passive=["root/right", "root/tie"], rolling=["root/wheel"],
        neutral_status="pass", residual_evaluations=36, poses=poses, combined=[])
    observations = {"48": dict(tool="linkage_motion", ok=True, result=report),
        "49": dict(tool="motion", ok=True,
                   result=dict(revision=12, status="fail", joints=[])),
        "50": dict(tool="linkage_motion", ok=False, result=dict(status="pass"))}
    original = deepcopy(observations)
    diagnostics = planning_evidence(observations, revision=12)["diagnostics"]
    summary = diagnostics["linkage_motion"]
    assert summary["status"] == "fail" and summary["current"]
    assert summary["issue"] == "driver_coupling_not_established"
    assert summary["driver"] == "root/left" and summary["passive"] == report["passive"]
    assert summary["failing_poses_omitted"] == 3 and len(summary["failing_poses"]) == 12
    failure = summary["failing_poses"][0]
    assert failure["positions"] == poses[0]["positions"]
    assert failure["lost_contacts"] == [contact] * 12 and failure["lost_contacts_omitted"] == 3
    assert failure["check_status"] == "pass" and "check" not in failure
    assert summary["pointer"] == "/48/result" and summary["summary_only"]
    assert summary["evidence_current"] == "recheck_native_dependencies_before_acceptance"
    assert diagnostics["motion"]["status"] == "fail"
    assert not planning_evidence(observations, revision=13)["diagnostics"]["linkage_motion"]["current"]
    assert observations == original


def test_combined_linkage_failure_summary_keeps_wheel_and_failure_counts():
    combined = [dict(rolling=f"root/wheel{i}", status="fail", poses=[
        dict(status="pass", positions={}),
        dict(status="fail", positions={"root/left": 30, f"root/wheel{i}": 180},
             lost_contacts=[], check=dict(status="fail", collisions=[dict(a="a", b="b")]))
    ]) for i in range(4)]
    report = dict(revision=1, status="fail", driver="root/left", passive=["root/right"],
        rolling=[row["rolling"] for row in combined], neutral_status="pass", poses=[], combined=combined)
    summary = planning_evidence({"3": dict(tool="linkage_motion", ok=True, result=report)},
                              revision=1)["diagnostics"]["linkage_motion"]
    assert len(summary["combined"]) == 4
    assert summary["combined"][2]["rolling"] == "root/wheel2"
    assert summary["combined"][2]["pose_count"] == 2
    assert summary["combined"][2]["failing_poses"][0]["positions"]["root/wheel2"] == 180
    assert summary["combined"][2]["failing_poses"][0]["collision_pairs"] == [dict(a="a", b="b")]
