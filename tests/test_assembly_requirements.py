from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from car2lego3d.assembly_agent import AssemblyAgent
from car2lego3d.assembly_requirements import evaluate, identity, validate_binding, vehicle_contract
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import write_json


def fixture(tmp_path):
    def node(name, parent, kind="part", color=4):
        return dict(id=name, parent=parent, kind=kind, part=name + ".dat", color=color,
            world=np.eye(4).tolist(), local=np.eye(4).tolist())
    graph = dict(revision=3, instances=[node("door", "root", "module"), node("panel", "door"),
        node("glass", "door", color=40), node("hinge", "door"), node("body", "root")],
        joints={"door": dict(purpose="door", limits=[0, 60])})
    contract = dict(schema="vehicle-modules.v1", modules=dict(door_left=dict(
        roles=["panel", "glass", "hinge"], joint_purpose="door", minimum_joints=1)))
    bindings = dict(door_left=dict(roots=["door"], roles=dict(panel=["panel"], glass=["glass"], hinge=["hinge"])))
    check = dict(revision=3, status="pass", unknown=[], connections=[dict(a="hinge", b="body")])
    motion = dict(revision=3, status="pass", joints=[dict(instance="door", poses=[dict(status="pass")] * 9)])
    check.update(graph_sha256=identity(graph), evidence_files=[])
    motion.update(graph_sha256=identity(graph), evidence_files=[])
    (tmp_path / "LDConfig.ldr").write_text("0 !COLOUR Clear CODE 40 VALUE #FFFFFF ALPHA 128\n0 !COLOUR Red CODE 4 VALUE #FF0000\n")
    review = dict(schema="vehicle-module-review.v1", graph_sha256=identity(graph),
        bindings_sha256=identity(bindings), contract_sha256=identity(contract), reviewer="independent-test-review",
        evidence_verified=True, modules=dict(door_left=dict(status="pass")))
    return graph, SimpleNamespace(root=tmp_path), contract, bindings, check, motion, review


def test_names_and_role_labels_never_complete_vehicle_without_independent_evidence(tmp_path):
    args = fixture(tmp_path)
    report = evaluate(*args[:-1])
    assert report["status"] == "unverified" and not report["completion_ready"]
    assert report["modules"][0]["unverified"] == ["independent_role_review_required"]
    passed = evaluate(*args)
    assert passed["completion_ready"] and passed["appearance"] == "unverified"


def test_baseline_door_without_glass_still_requires_panel_in_the_moving_subtree(tmp_path):
    args = fixture(tmp_path)
    graph, catalog, contract, bindings, check, motion, review = args
    graph["instances"] = [n for n in graph["instances"] if n["id"] != "glass"]
    contract["modules"]["door_left"]["roles"].remove("glass")
    bindings["door_left"]["roles"].pop("glass")
    check["graph_sha256"] = motion["graph_sha256"] = identity(graph)
    review.update(graph_sha256=identity(graph), bindings_sha256=identity(bindings), contract_sha256=identity(contract))
    assert evaluate(*args)["completion_ready"]
    graph["instances"][1]["parent"] = "root"
    assert not evaluate(*args)["completion_ready"]


def test_baseline_scope_review_cannot_be_mistaken_for_verified_delivery(tmp_path):
    args = fixture(tmp_path)
    args[2]["scope_review_required"] = ["cover_articulation"]
    args[-1]["contract_sha256"] = identity(args[2])
    report = evaluate(*args)
    assert report["status"] == "unverified" and not report["completion_ready"]
    assert report["scope_review_required"] == ["cover_articulation"]


@pytest.mark.parametrize("problem", ["missing_binding", "glass_outside", "opaque_glass", "no_joint", "wrong_purpose",
    "no_contact", "stale_check", "stale_motion", "insufficient_poses", "colliding_pose", "missing_geometry", "stale_review"])
def test_actual_module_evidence_is_required(tmp_path, problem):
    args = fixture(tmp_path)
    graph, catalog, contract, bindings, check, motion, review = args
    if problem == "missing_binding":
        bindings.clear()
    if problem == "glass_outside":
        graph["instances"][2]["parent"] = "root"
    if problem == "opaque_glass":
        graph["instances"][2]["color"] = 4
    if problem == "no_joint":
        graph["joints"].clear()
    if problem == "wrong_purpose":
        graph["joints"]["door"]["purpose"] = "hood"
    if problem == "no_contact":
        check["connections"] = []
    if problem == "stale_check":
        check["revision"] -= 1
    if problem == "stale_motion":
        motion["revision"] -= 1
    if problem == "insufficient_poses":
        motion["joints"][0]["poses"] = [dict(status="pass")] * 8
    if problem == "colliding_pose":
        motion["joints"][0]["poses"] = [dict(status="fail")] * 9
    if problem == "missing_geometry":
        check["unknown"] = [dict(instance="glass", issue="missing")]
    if problem == "stale_review":
        review["graph_sha256"] = "outdated"
    assert not evaluate(*args)["completion_ready"]


def test_binding_rejects_duplicate_role_parts_empty_modules_and_foreign_ids(tmp_path):
    graph, _, contract, bindings, *_ = fixture(tmp_path)
    binding = bindings["door_left"]
    validate_binding(graph, contract, "door_left", **binding)
    for roots, roles in [(["root"], binding["roles"]), (["door"], dict(panel=["panel"], glass=["panel"], hinge=["hinge"])),
            (["door"], dict(panel=["body"], glass=["glass"], hinge=["hinge"])), (["door"], {})]:
        with pytest.raises(ValueError):
            validate_binding(graph, contract, "door_left", roots, roles)


def test_binding_is_revision_checked_persistent_and_rolls_back_on_failure(tmp_path, monkeypatch):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    tools.vehicle_contract = dict(modules=dict(chassis=dict(roles=["structure"])))
    response = tools.call("edit", dict(revision=0, operations=[
        dict(op="module", name="chassis.ldr", alias="c", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@c", part="3001.dat", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@c", part="3001.dat", matrix=np.eye(4).tolist())]))
    assert response["ok"]
    nodes = tools.assembly.graph()["instances"]
    args = dict(revision=1, module="chassis", roots=[nodes[0]["id"]], roles=dict(structure=[nodes[1]["id"]]))
    assert tools.call("bind_module", args)["ok"]
    assert tools.assembly.revision == 2
    assert AssemblyTools(tmp_path, tmp_path / "native").module_bindings == tools.module_bindings
    before = deepcopy(tools.module_bindings)
    repeated = tools.call("bind_module", {**args, "revision": 2})
    assert repeated["ok"] and repeated["result"]["changed"] is False
    assert tools.assembly.revision == 2 and tools.module_bindings == before
    assert not tools.call("bind_module", args)["ok"]
    assert tools.module_bindings == before
    monkeypatch.setattr(tools, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    assert not tools.call("bind_module", {**args, "revision": 2,
        "roles": dict(structure=[nodes[2]["id"]])})["ok"]
    assert tools.assembly.revision == 2 and tools.module_bindings == before


def test_duplicate_module_binding_is_rejected_without_changing_checkpoint(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    tools.vehicle_contract = dict(modules={name: dict(roles=["hub"])
        for name in ("wheel_front_left", "wheel_front_right")})
    result = tools.call("edit", dict(revision=0, operations=[
        dict(op="module", name="wheel.ldr", alias="wheel", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@wheel", part="3001.dat", alias="hub", matrix=np.eye(4).tolist())]))
    ids = result["result"]["aliases"]
    args = dict(roots=[ids["wheel"]], roles=dict(hub=[ids["hub"]]))
    assert tools.call("bind_module", dict(args, revision=1, module="wheel_front_left"))["ok"]
    before = (tmp_path / "state.json").read_bytes()
    rejected = tools.call("bind_module", dict(args, revision=2, module="wheel_front_right"))
    assert not rejected["ok"] and "wheel_front_left" in rejected["error"]
    assert ids["hub"] in rejected["error"] and "independent submodel" in rejected["error"]
    assert (tmp_path / "state.json").read_bytes() == before
    assert AssemblyTools(tmp_path, tmp_path / "native").module_bindings == tools.module_bindings


def test_final_completeness_rejects_both_duplicate_bindings_regardless_of_order(tmp_path):
    graph, catalog, contract, bindings, check, motion, review = fixture(tmp_path)
    contract["modules"]["door_right"] = deepcopy(contract["modules"]["door_left"])
    bindings["door_right"] = deepcopy(bindings["door_left"])
    for ordered in (bindings, dict(reversed(list(bindings.items())))):
        report = evaluate(graph, catalog, contract, ordered, check, motion)
        assert not report["completion_ready"]
        assert all(row["status"] == "fail" and "already assigned" in row["failures"][0]
            for row in report["modules"])


def test_shared_module_root_is_rejected_even_when_role_parts_differ(tmp_path):
    graph, _, contract, bindings, *_ = fixture(tmp_path)
    contract["modules"]["door_right"] = dict(roles=["panel"])
    with pytest.raises(ValueError, match="already assigned"):
        validate_binding(graph, contract, "door_right", ["door"], dict(panel=["hinge"]),
            bindings={"door_left": dict(roots=["door"], roles=dict(panel=["panel"]))})


def test_steering_and_suspension_can_still_share_carrier_but_wheels_cannot(tmp_path):
    graph, _, _, _, *_ = fixture(tmp_path)
    contract = dict(modules={name: dict(roles=["carrier"])
        for name in ("steering", "suspension", "wheel_front_left")})
    binding = dict(roots=["door"], roles=dict(carrier=["hinge"]))
    assert validate_binding(graph, contract, "suspension", **binding, bindings={"steering": binding}) == binding
    with pytest.raises(ValueError, match="steering"):
        validate_binding(graph, contract, "wheel_front_left", **binding, bindings={"steering": binding})


def test_exported_small_draft_cannot_finish_a_vehicle(tmp_path, monkeypatch):
    from test_assembly_agent import ScriptedPlanner, fake_outputs, seed
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    seed(tools)
    fake_outputs(monkeypatch, tools)
    backend = ScriptedPlanner([("export", {}), ("finish", {})])
    agent = AssemblyAgent(tools, backend, dict(vehicle_contract=vehicle_contract(dict(model="Countach"), "scene")), max_steps=2)
    state = agent.run()
    assert state["stop_reason"] == "tool_budget_exhausted"
    assert not state["module_progress"]["completion_ready"]
    assert state["module_progress"]["complete"] == 0
    assert state["module_progress"]["required"] == len(vehicle_contract(dict(model="Countach"), "scene")["modules"])
    assert state["status"] == "draft"
