"""Native mounting search must be replayable without editing its input state."""
from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from car2lego3d.assembly_catalog import compatible, shaft_match, shaft_snap_origin, validate
from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_mounts import protection
from car2lego3d.assembly_search import axial_offsets, placement_operations, rotations
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.job_runtime import AssemblyCancelled
from car2lego3d.native_ports import Port
from scripts.probe_connector_module import demonstration_operations, rolling_evidence


@pytest.fixture
def scene(tmp_path, monkeypatch):
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", "off")
    tools = AssemblyTools(tmp_path, native)
    shifted = np.eye(4)
    shifted[0, 3] = 200
    built = tools.call("edit", dict(revision=0, operations=[
        dict(op="add", part="3001.dat", alias="base", matrix=np.eye(4).tolist()),
        dict(op="module", name="moving.ldr", alias="moving", matrix=shifted.tolist()),
        dict(op="add", part="3001.dat", parent="@moving", alias="brick", matrix=np.eye(4).tolist())]))
    assert built["ok"], built
    ids = built["result"]["aliases"]
    ports = tools.catalog.ports("3001.dat")
    hole, stud = next((h, s) for h in ports for s in ports if h.kind == "hole" and s.kind == "stud"
        and compatible(h, s) and np.allclose(np.asarray(h.position)[[0, 2]], np.asarray(s.position)[[0, 2]]))
    args = dict(revision=1, moving=ids["moving"], targets=[ids["base"]], limit=1,
        source_ports=[dict(instance=ids["brick"], port=hole.id)],
        target_ports=[dict(instance=ids["base"], port=stud.id)])
    return tools, args, ids


def test_native_proposals_are_read_only_and_replay_with_one_revision(scene):
    tools, args, ids = scene
    before = deepcopy(tools.assembly.graph())
    state = (tools.directory / "state.json").read_bytes()
    result = tools.call("search_mounts", args)
    assert result["ok"], result
    report = result["result"]
    assert result["revision_after"] == result["revision_before"] == 1
    assert tools.assembly.graph() == before and (tools.directory / "state.json").read_bytes() == state
    assert check_current(before, report)
    assert report["status"] == "proposals" and report["statistics"]["evaluations"] == 1
    proposal = report["proposals"][0]
    assert proposal["installation"] == proposal["whole_graph"]["status"] == "pass"
    assert proposal["build_arguments"]["strict"] is True and proposal["motion"] == "unverified"
    assert tools.call("build", proposal["build_arguments"])["ok"]
    assert tools.assembly.revision == 2
    nodes = {node["id"]: node for node in tools.assembly.graph()["instances"]}
    np.testing.assert_allclose(np.asarray(nodes[ids["brick"]]["world"])[:3, 3], [0, -24, 0])
    assert validate(tools.assembly.graph(), tools.catalog)["status"] == "pass"
    assert not check_current(tools.assembly.graph(), report)
    assert AssemblyTools(tools.directory, tools.catalog_root).assembly.graph() == tools.assembly.graph()
    records = [json.loads(line) for line in (tools.directory / "trajectory.jsonl").read_text().splitlines()]
    assert [record["tool"] for record in records] == ["edit", "search_mounts", "build"]


def test_unrelated_disconnected_module_stays_explicitly_draft(scene):
    tools, args, _ = scene
    matrix = np.eye(4)
    matrix[0, 3] = -400
    assert tools.call("edit", dict(revision=1, operations=[dict(op="add", part="3001.dat", matrix=matrix.tolist())]))["ok"]
    args["revision"] = 2
    proposal = tools.call("search_mounts", args)["result"]["proposals"][0]
    assert proposal["installation"] == "pass"
    assert proposal["whole_graph"]["status"] == "fail" and proposal["whole_graph"]["components"] == 2
    assert proposal["build_arguments"]["strict"] is False
    assert tools.call("build", proposal["build_arguments"])["ok"]
    assert tools.call("check", {})["result"]["status"] == "fail"


@pytest.mark.parametrize("fault", ["stale", "inside", "unknown_root", "source_filter", "zero_budget", "large_budget",
                                  "axial_zero", "axial_large", "axial_fraction"])
def test_invalid_requests_do_not_change_input(scene, fault):
    tools, args, ids = scene
    if fault == "stale":
        args["revision"] = 0
    elif fault == "inside":
        args["targets"] = [ids["brick"]]
    elif fault == "unknown_root":
        args["moving"] = "invented"
    elif fault == "source_filter":
        args["source_ports"][0]["instance"] = ids["base"]
    elif fault == "zero_budget":
        args["max_evaluations"] = 0
    elif fault == "large_budget":
        args["max_port_pairs"] = 100001
    else:
        args["max_axial_samples"] = {"axial_zero": 0, "axial_large": 34, "axial_fraction": 1.5}[fault]
    before = tools.assembly.graph(), (tools.directory / "state.json").read_bytes()
    assert not tools.call("search_mounts", args)["ok"]
    assert (tools.assembly.graph(), (tools.directory / "state.json").read_bytes()) == before


@pytest.mark.parametrize("fault", ["collision", "exact_contact", "disconnected", "protected", "unknown"])
def test_rejects_native_failures_and_respects_evaluation_budget(scene, monkeypatch, fault):
    from car2lego3d import assembly_search
    tools, args, ids = scene
    args.update(max_evaluations=1, limit=2)
    if fault == "protected":
        binding = dict(roots=[ids["moving"]], roles=dict(body=[ids["brick"]]))
        tools.module_bindings = dict(completed=binding)
        tools.protected_state = protection(tools.assembly.graph(), tools.module_bindings)
    else:
        native_validate = assembly_search.validate
        count = 0

        def failed(graph, catalog):
            nonlocal count
            report = native_validate(graph, catalog)
            count += 1
            if count == 1 and fault != "unknown":
                return report
            if fault == "collision":
                report["collisions"] = [dict(a=ids["base"], b=ids["brick"])]
            elif fault == "exact_contact":
                report["connections"] = [{**row, "port_a": "wrong"} for row in report["connections"]]
            elif fault == "disconnected":
                report["components"] = [[ids["base"]], [ids["brick"]]]
            elif fault == "unknown":
                report["unknown"] = [dict(instance=ids["brick"], issue="missing native data")]
            return report

        monkeypatch.setattr(assembly_search, "validate", failed)
    before = tools.assembly.graph()
    response = tools.call("search_mounts", args)
    assert response["ok"], response
    result = response["result"]
    assert result["proposals"] == [] and tools.assembly.graph() == before
    if fault == "unknown":
        assert result["status"] == "unverified" and result["statistics"]["evaluations"] == 0
    else:
        assert result["statistics"]["evaluations"] == 1 and result["stop_reason"] == "evaluation_budget"
        reason = {"collision": "collisions", "exact_contact": "selected_contact_missing",
            "disconnected": "moving_subtree_disconnected", "protected": "protected_module_changed"}[fault]
        assert result["rejected"][reason] == 1


def test_port_pairs_are_bounded_and_native_pose_duplicates_are_omitted(scene):
    tools, args, _ = scene
    result = tools.call("search_mounts", {**args, "limit": 20, "max_port_pairs": 1})["result"]
    assert result["statistics"]["port_pairs"] == 1 and result["stop_reason"] == "port_pair_budget"
    result = tools.call("search_mounts", {**args, "limit": 20})["result"]
    matrices = [np.asarray(row["build_arguments"]["operations"][0]["matrix"]).round(6).tolist()
                for row in result["proposals"]]
    assert len(matrices) == len({json.dumps(row) for row in matrices})
    assert result["statistics"]["duplicate_poses"] > 0
    assert len(list(rotations())) == 24


def test_interrupts_and_dependency_changes_invalidate_search(scene, monkeypatch):
    tools, args, _ = scene
    before = tools.assembly.graph()
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise AssemblyCancelled("cancelled during mounting search")

    tools.checkpoint = cancelled
    with pytest.raises(AssemblyCancelled):
        tools.call("search_mounts", args)
    assert tools.assembly.graph() == before
    tools.checkpoint = None
    original = tools.verification_inputs
    calls = 0

    def changed():
        nonlocal calls
        calls += 1
        result = original()
        if calls > 1:
            result["evidence_files"][0]["sha256"] = "changed"
        return result

    monkeypatch.setattr(tools, "verification_inputs", changed)
    result = tools.call("search_mounts", args)
    assert not result["ok"] and "evidence changed" in result["error"]
    assert tools.assembly.graph() == before


def test_nested_rolling_joint_moves_with_root_and_replays_native_motion(scene):
    tools, _, _ = scene
    tools = AssemblyTools(tools.directory / "rolling", tools.catalog_root)
    parent = np.eye(4)
    parent[:3, :3] = Rotation.from_euler("xyz", [25, 60, -30], degrees=True).as_matrix()
    parent[:3, 3] = [100, 45, -220]
    operations = [dict(op="module", name="mount.ldr", alias="mount", matrix=parent.tolist())]
    for op in demonstration_operations(rolling=True):
        if op["op"] in {"module", "add"} and "parent" not in op:
            op["parent"] = "@mount"
        operations.append(op)
    built = tools.call("build", dict(revision=0, operations=operations))
    assert built["ok"], built
    ids = built["result"]["aliases"]
    original = tools.assembly.graph()
    shift = np.eye(4)
    shift[:3, :3] = Rotation.from_euler("xyz", [90, 0, 90], degrees=True).as_matrix()
    shift[:3, 3] = [180, -50, 90]
    assert tools.call("edit", dict(revision=1,
        operations=placement_operations(original, ids["wheel"], shift)))["ok"]
    before = tools.assembly.graph()
    searched = tools.call("search_mounts", dict(revision=2, moving=ids["wheel"], targets=[ids["bearing"]],
        limit=1, source_ports=[dict(instance=ids["axle"], port="0")],
        target_ports=[dict(instance=ids["bearing"], port="2")]))
    assert searched["ok"], searched
    proposal = searched["result"]["proposals"][0]
    assert tools.assembly.graph() == before
    assert len(proposal["build_arguments"]["operations"]) == 2
    assert tools.call("build", proposal["build_arguments"])["ok"]
    assert tools.call("check", {})["result"]["status"] == "pass"
    assert tools.call("motion", dict(samples=9))["result"]["status"] == "pass"
    assert rolling_evidence(tools)["status"] == "pass"
    assert tools.assembly.joints[ids["wheel"]]["origin"] != before["joints"][ids["wheel"]]["origin"]


@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize("semantic", ["axle", "axle_guard", "pin"])
@pytest.mark.parametrize("capped", [False, True])
def test_axial_samples_preserve_native_stops_keys_and_minimum_engagement(sign, semantic, capped):
    male = Port("0", "axle", 7, (0, 0, 0), (0, 0, 1), length=60,
                semantic=semantic, start_capped=capped, cross_type=True)
    socket = Port("0", "axle", 2, (0, 0, 0), (0, 0, sign), length=20,
                  semantic="pin_socket", end_capped=capped, cross_type=True)
    a = ("shaft", male, np.array([0., 0, 100]), np.array(male.direction), np.array([1., 0, 0]))
    b = ("socket", socket, np.zeros(3), np.array(socket.direction), np.array([1., 0, 0]))
    a = (a[0], male, shaft_snap_origin(a, b), *a[3:])
    offsets = list(axial_offsets(a, b, 9))
    assert offsets[0] == 0 and 1 <= len(offsets) <= 9
    if not capped:
        assert len(offsets) > 1
    assert len(set(offsets)) == len(offsets)
    for offset in offsets:
        assert shaft_match((a[0], male, a[2] + offset * b[3], *a[3:]), b)
    assert list(axial_offsets(a, b, 1)) == [0.]
    miskeyed = (*a[:4], np.array([2 ** -.5, 2 ** -.5, 0]))
    assert list(axial_offsets(miskeyed, b, 9)) == [0.]


@pytest.mark.parametrize("side", ["fl", "fr", "rl", "rr"])
def test_axial_search_repairs_inboard_wheel_with_full_chassis_layout_and_motion(tmp_path, scene, side):
    from car2lego3d.assembly_layout import wheel_layout
    from scripts.probe_rolling_chassis import chassis_evidence, demonstration_operations as chassis_operations

    tools = AssemblyTools(tmp_path / "chassis", scene[0].catalog_root)
    built = tools.call("build", dict(revision=0, operations=chassis_operations()))
    assert built["ok"], built
    ids = built["result"]["aliases"]
    tools.module_bindings = {"chassis": dict(roots=[ids["chassis"]])}
    for prefix, axle, position in (("fl", "front", "left"), ("fr", "front", "right"),
                                   ("rl", "rear", "left"), ("rr", "rear", "right")):
        tools.module_bindings[f"wheel_{axle}_{position}"] = dict(
            roots=[ids[prefix]], roles=dict(tyre=[ids[prefix + "tyre"]]))
    tools.vehicle_contract = dict(modules={key: {} for key in tools.module_bindings})
    delta = np.eye(4)
    delta[2, 3] = 40 if side.endswith("l") else -40
    assert tools.call("edit", dict(revision=1,
        operations=placement_operations(tools.assembly.graph(), ids[side], delta)))["ok"]
    before = deepcopy(tools.assembly.graph())
    state = (tools.directory / "state.json").read_bytes()
    assert validate(before, tools.catalog)["collisions"]
    args = dict(revision=2, moving=ids[side], targets=[ids[side + "bearing"]], limit=1,
        max_port_pairs=1, max_evaluations=33,
        source_ports=[dict(instance=ids[side + "axle"], port="0")],
        target_ports=[dict(instance=ids[side + "bearing"], port="2")])
    original = tools.call("search_mounts", {**args, "max_axial_samples": 1})["result"]
    assert not original["proposals"] and original["rejected"]["collisions"] == 1
    bounded = tools.call("search_mounts", {**args, "max_evaluations": 1})["result"]
    assert not bounded["proposals"] and bounded["stop_reason"] == "evaluation_budget"
    result = tools.call("search_mounts", args)
    assert result["ok"], result
    report = result["result"]
    assert tools.assembly.graph() == before and (tools.directory / "state.json").read_bytes() == state
    assert 1 < report["statistics"]["evaluations"] <= 9
    proposal = report["proposals"][0]
    assert proposal["seating"]["offset_ldu"] != 0 and proposal["wheel_layout"]["status"] == "pass"
    assert proposal["motion"] == "unverified" and len(proposal["build_arguments"]["operations"]) == 2
    assert tools.call("build", proposal["build_arguments"])["ok"]
    after = tools.assembly.graph()
    assert all(node == next(n for n in after["instances"] if n["id"] == node["id"])
               for node in before["instances"] if node["id"] != ids[side]
               and not node["id"].startswith(ids[side] + "/"))
    assert wheel_layout(after, tools.catalog, tools.vehicle_contract, tools.module_bindings)["status"] == "pass"
    assert tools.call("check", {})["result"]["status"] == "pass"
    assert tools.call("motion", dict(samples=9))["result"]["status"] == "pass"
    assert chassis_evidence(tools)["status"] == "pass"


def test_axial_repair_preserves_joint_under_rotated_translated_parent(tmp_path, scene):
    from scripts.probe_rolling_chassis import demonstration_operations as chassis_operations

    tools = AssemblyTools(tmp_path / "nested-chassis", scene[0].catalog_root)
    parent = np.eye(4)
    parent[:3, :3] = Rotation.from_euler("xyz", [25, 60, -30], degrees=True).as_matrix()
    parent[:3, 3] = [100, 45, -220]
    operations = [dict(op="module", name="mount.ldr", alias="mount", matrix=parent.tolist())]
    for operation in chassis_operations():
        if operation["op"] in {"module", "add"} and "parent" not in operation:
            operation["parent"] = "@mount"
        operations.append(operation)
    ids = tools.call("build", dict(revision=0, operations=operations))["result"]["aliases"]
    delta = np.eye(4)
    delta[:3, 3] = parent[:3, :3] @ [0, 0, 40]
    assert tools.call("edit", dict(revision=1,
        operations=placement_operations(tools.assembly.graph(), ids["fl"], delta)))["ok"]
    searched = tools.call("search_mounts", dict(revision=2, moving=ids["fl"], targets=[ids["flbearing"]],
        limit=1, max_port_pairs=1, source_ports=[dict(instance=ids["flaxle"], port="0")],
        target_ports=[dict(instance=ids["flbearing"], port="2")]))
    assert searched["ok"], searched
    proposal = searched["result"]["proposals"][0]
    assert proposal["seating"]["offset_ldu"] != 0
    assert tools.call("build", proposal["build_arguments"])["ok"]
    assert tools.call("check", {})["result"]["status"] == "pass"
    assert tools.call("motion", dict(samples=9))["result"]["status"] == "pass"
