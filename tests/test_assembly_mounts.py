from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from car2lego3d.assembly_candidates import capture, structure, surroundings
from car2lego3d.assembly_mounts import (installation_targets, mechanism_response, protection,
    semantic_node, wheel_mount_issues)
from car2lego3d.assembly_requirements import evaluate, identity, vehicle_contract
from car2lego3d.assembly_schedule import module_signature
from car2lego3d.assembly_tools import AssemblyTools


def setup(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    tools.vehicle_contract = vehicle_contract(dict(brand="Lamborghini", model="Countach"), "scene")
    result = tools.call("edit", dict(revision=0, operations=[
        dict(op="module", name="steering.ldr", matrix=np.eye(4).tolist(), alias="steering"),
        dict(op="add", parent="@steering", part="3743.dat", matrix=np.eye(4).tolist(), alias="rack"),
        dict(op="module", parent="@steering", name="carrier.ldr", matrix=np.eye(4).tolist(), alias="carrier"),
        dict(op="add", parent="@carrier", part="6571.dat", matrix=np.eye(4).tolist(), alias="bearing"),
        dict(op="joint", instance="@carrier", axis=[0, 1, 0], limits=[-20, 20], purpose="steering")]))
    assert result["ok"], result
    ids = result["result"]["aliases"]
    tools.module_bindings["steering"] = dict(roots=[ids["steering"]], roles=dict(rack=[ids["rack"]], carrier=[ids["bearing"]]))
    tools.protected_state = protection(tools.assembly.graph(), tools.module_bindings, mounts=[ids["carrier"]])
    return tools, ids


def install(tools, parent, *, fixed_hub=False):
    result = tools.call("edit", dict(revision=tools.assembly.revision, operations=[
        dict(op="module", parent=parent, name="wheel.ldr", matrix=np.eye(4).tolist(), alias="wheel"),
        dict(op="module", parent="@wheel", name="rolling.ldr", matrix=np.eye(4).tolist(), alias="rolling"),
        *[dict(op="add", parent="@wheel" if fixed_hub and role == "hub" else "@rolling",
            part=part, matrix=np.eye(4).tolist(), alias=role)
            for role, part in (("tyre", "41897.dat"), ("rim", "41896.dat"), ("hub", "6587.dat"))],
        dict(op="joint", instance="@rolling", axis=[0, 0, 1], limits=[0, 360], purpose="rolling")]))
    assert result["ok"], result
    ids = result["result"]["aliases"]
    tools.module_bindings["wheel_front_left"] = dict(roots=[ids["wheel"]],
        roles={role: [ids[role]] for role in ("tyre", "rim", "hub")})
    return ids


def test_wheel_installation_preserves_carrier_geometry_and_candidate_identity(tmp_path):
    tools, ids = setup(tmp_path)
    original = {n["id"]: semantic_node(n) for n in tools.assembly.graph()["instances"]}
    signature = module_signature(tools, "steering")
    fingerprint, outside = structure(tools, "steering"), surroundings(tools, "steering")
    assert installation_targets(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings,
        "wheel_front_left") == [ids["carrier"]]
    wheel = install(tools, ids["carrier"])
    assert all(semantic_node(n) == original[n["id"]] for n in tools.assembly.graph()["instances"] if n["id"] in original)
    assert module_signature(tools, "steering") == signature
    assert structure(tools, "steering") == fingerprint
    assert surroundings(tools, "steering") != outside
    assert not wheel_mount_issues(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings, "wheel_front_left")
    result = mechanism_response(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings, "steering")
    assert result["status"] == "pending"
    assert result["wheels"][0]["status"] == "pass" and result["wheels"][0]["samples"] == 9
    assert result["wheels"][1]["reason"] == "wheel_not_installed"
    assert mechanism_response(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings,
        "steering", require_complete=True)["status"] == "fail"
    assert wheel["tyre"] in {n["id"] for n in tools.assembly.graph()["instances"]}


@pytest.mark.parametrize("change", ["loose_part", "wrong_parent", "transform", "color", "remove", "joint", "new_joint"])
def test_mount_permissions_do_not_allow_changes_to_completed_geometry(tmp_path, change):
    tools, ids = setup(tmp_path)
    before = tools.assembly.text(), tools.assembly.revision, deepcopy(tools.assembly.joints)
    if change == "loose_part":
        op = dict(op="add", parent=ids["carrier"], part="3001.dat", matrix=np.eye(4).tolist())
    elif change == "wrong_parent":
        op = dict(op="module", parent=ids["steering"], name="wrong.ldr", matrix=np.eye(4).tolist())
    elif change in {"joint", "new_joint"}:
        op = dict(op="joint", instance=ids["carrier" if change == "joint" else "steering"], axis=[1, 0, 0], limits=[0, 50], purpose="steering")
    else:
        op = dict(op=change, instance=ids["bearing"])
        if change == "color":
            op["color"] = 4
        elif change == "transform":
            op["matrix"] = np.eye(4).tolist()
            op["matrix"][0][3] = 1
    result = tools.call("edit", dict(revision=tools.assembly.revision, operations=[op]))
    assert not result["ok"] and "Completed module" in result["error"]
    assert (tools.assembly.text(), tools.assembly.revision, tools.assembly.joints) == before


def test_steering_unrelated_carrier_or_axle_spin_cannot_pass_wheel_response(tmp_path):
    tools, ids = setup(tmp_path)
    wheel = install(tools, "root")
    graph = tools.assembly.graph()
    assert wheel_mount_issues(graph, tools.vehicle_contract, tools.module_bindings, "wheel_front_left") == ["wheel_not_mounted_under_steering"]
    assert mechanism_response(graph, tools.vehicle_contract, tools.module_bindings, "steering")["status"] == "fail"
    tools.protected_state = None
    tools.assembly.transact(tools.assembly.revision, [dict(op="remove", instance=wheel["wheel"])])
    del tools.module_bindings["wheel_front_left"]
    # Fresh names are required because MPD retains unused definitions.
    for name in ("wheel.ldr", "rolling.ldr"):
        del tools.assembly.sections[name]
    install(tools, ids["carrier"])
    tools.assembly.joints[ids["carrier"]]["axis"] = [0, 0, 1]
    report = mechanism_response(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings, "steering")
    assert report["status"] == "fail"
    assert report["wheels"][0]["reason"] == "wheel_does_not_respond_to_steering"


def test_missing_wheel_carrier_role_is_not_an_installation_interface(tmp_path):
    tools, ids = setup(tmp_path)
    tools.module_bindings["steering"]["roles"]["carrier"] = [ids["rack"]]
    assert installation_targets(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings,
        "wheel_front_left") == []


def test_stationary_hub_can_steer_without_being_inside_the_rolling_subtree(tmp_path):
    tools, ids = setup(tmp_path)
    wheel = install(tools, ids["carrier"], fixed_hub=True)
    report = mechanism_response(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings, "steering")
    assert report["wheels"][0]["status"] == "pass"
    before = tools.assembly.revision
    changed = tools.call("edit", dict(revision=before, operations=[dict(op="color", instance=wheel["rim"], color=4)]))
    assert changed["ok"] and tools.assembly.revision == before + 1


def test_restoring_carrier_candidate_cannot_erase_installed_child_module(tmp_path, monkeypatch):
    from car2lego3d import assembly_schedule
    tools, ids = setup(tmp_path)
    monkeypatch.setattr(assembly_schedule, "module_check", lambda *args, **kwargs: dict(status="pass",
        signature=module_signature(tools, "steering"), native_evidence=[]))
    candidate = capture(tools, tools.assembly.revision, "steering", "carrier before wheel installation")
    wheel = install(tools, ids["carrier"])
    before = tools.assembly.text(), tools.assembly.revision, deepcopy(tools.module_bindings)
    rejected = tools.call("restore_candidate", dict(revision=tools.assembly.revision,
        module="steering", candidate=candidate["id"]))
    assert not rejected["ok"] and "Surrounding assembly changed" in rejected["error"]
    assert (tools.assembly.text(), tools.assembly.revision, tools.module_bindings) == before
    assert wheel["wheel"] in {n["id"] for n in tools.assembly.graph()["instances"]}


def test_resumed_wheel_is_editable_but_completed_carrier_stays_protected(tmp_path):
    tools, ids = setup(tmp_path)
    wheel = install(tools, ids["carrier"])
    completed = {"steering": tools.module_bindings["steering"]}
    tools.protected_state = protection(tools.assembly.graph(), completed,
        mounts=[ids["carrier"]], editable_roots=[wheel["wheel"]])
    result = tools.call("edit", dict(revision=tools.assembly.revision,
        operations=[dict(op="color", instance=wheel["rim"], color=4)]))
    assert result["ok"], result
    rejected = tools.call("edit", dict(revision=tools.assembly.revision,
        operations=[dict(op="color", instance=ids["bearing"], color=4)]))
    assert not rejected["ok"] and "Completed module changed" in rejected["error"]
    with pytest.raises(ValueError, match="completed module"):
        protection(tools.assembly.graph(), completed, editable_roots=[ids["carrier"]])


def test_nested_suspension_carrier_moves_the_installed_wheel(tmp_path):
    from car2lego3d.assembly_motion import evaluate_motion
    tools, ids = setup(tmp_path)
    tools.vehicle_contract["modules"]["suspension"] = dict(roles=["carrier"])
    result = tools.call("edit", dict(revision=tools.assembly.revision, operations=[
        dict(op="module", parent=ids["carrier"], name="suspension.ldr", alias="s", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@s", part="6571.dat", alias="bearing", matrix=np.eye(4).tolist()),
        dict(op="joint", instance="@s", axis=[0, 1, 0], limits=[-5, 5], type="prismatic", purpose="suspension")]))
    assert result["ok"], result
    suspension = result["result"]["aliases"]
    tools.module_bindings["suspension"] = dict(roots=[suspension["s"]], roles=dict(carrier=[suspension["bearing"]]))
    tools.protected_state = protection(tools.assembly.graph(), tools.module_bindings, mounts=[suspension["s"]])
    assert installation_targets(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings,
        "wheel_front_left") == [suspension["s"]]
    install(tools, suspension["s"], fixed_hub=True)
    graph = tools.assembly.graph()
    assert not wheel_mount_issues(graph, tools.vehicle_contract, tools.module_bindings, "wheel_front_left")
    for purpose in ("steering", "suspension"):
        assert mechanism_response(graph, tools.vehicle_contract, tools.module_bindings, purpose)["wheels"][0]["status"] == "pass"
    report = evaluate_motion(graph, lambda _: dict(collisions=[], unknown=[], invalid_transforms=[],
        gates=dict(connections="pass")))
    assert report["combined_joint_states"] == "pass" and len(report["combined"][0]["poses"]) == 81
    tools.assembly.joints[suspension["s"]].update(type="revolute", axis=[0, 0, 1])
    failed = mechanism_response(tools.assembly.graph(), tools.vehicle_contract, tools.module_bindings, "suspension")
    assert failed["wheels"][0]["reason"] == "wheel_does_not_respond_to_suspension"


def test_final_evaluator_rejects_geometrically_clear_but_unrelated_steering(tmp_path):
    tools, ids = setup(tmp_path)
    install(tools, "root")
    tools.vehicle_contract["modules"] = {k: v for k, v in tools.vehicle_contract["modules"].items()
        if k in {"steering", "wheel_front_left"}}
    graph = tools.assembly.graph()
    check = dict(revision=graph["revision"], graph_sha256=identity(graph), status="pass", unknown=[],
        evidence_files=[], connections=[])
    report = evaluate(graph, SimpleNamespace(root=tmp_path), tools.vehicle_contract,
        tools.module_bindings, check, {**check, "joints": []})
    failures = {row["id"]: row["failures"] for row in report["modules"]}
    assert not report["completion_ready"] and "wheel_response_failed" in failures["steering"]
    assert "wheel_not_mounted_under_steering" in failures["wheel_front_left"]


def test_motion_tool_reports_wheel_response_failure_even_with_clear_poses(tmp_path, monkeypatch):
    from car2lego3d import assembly_tools
    tools, ids = setup(tmp_path)
    install(tools, "root")
    monkeypatch.setattr(assembly_tools, "validate", lambda *args, **kwargs: dict(collisions=[], unknown=[],
        invalid_transforms=[], gates=dict(connections="pass")))
    report = tools.tool_motion()
    assert report["status"] == "fail"
    assert all(pose["status"] == "pass" for row in report["joints"] for pose in row["poses"])
    assert report["mechanism_response"]["steering"]["status"] == "fail"
