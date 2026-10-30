"""Axial release diagnosis must cover thin obstructions and fail closed."""
from copy import deepcopy
import os
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from car2lego3d.assembly_axial import axial_clearance, axial_motion, sweep_obb
from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.native_ports import Port
from scripts.probe_connector_module import demonstration_operations


def box(center, half, axes=None):
    return np.asarray(center, float), np.eye(3) if axes is None else axes, np.asarray(half, float)


def test_continuous_sweep_detects_obstruction_between_discrete_samples():
    moving = box([0, 0, 0], [.2, 2, 2])
    fixed = box([5.05, 0, 0], [.2, 2, 2])
    interval = sweep_obb(moving, fixed, np.array([1., 0, 0]), 10)
    np.testing.assert_allclose(interval, [5, 5.1])
    assert not any(interval[0] < sample < interval[1] for sample in np.linspace(0, 10, 9))
    assert sweep_obb(moving, fixed, np.array([-1., 0, 0]), 10) is None


def test_sweep_is_invariant_under_arbitrary_rigid_frame_change():
    rotation = Rotation.from_euler("xyz", [23, -48, 19], degrees=True).as_matrix()
    offset = np.array([25, -123, 16])
    ma, fb = box([0, 0, 0], [1, 2, 3]), box([9, 1, 0], [2, 3, 4])
    transform = lambda item: (rotation @ item[0] + offset, rotation @ item[1], item[2])
    np.testing.assert_allclose(sweep_obb(ma, fb, np.array([1., 0, 0]), 20),
        sweep_obb(transform(ma), transform(fb), rotation @ [1., 0, 0], 20))
    assert sweep_obb(ma, box([9, 20, 0], [2, 3, 4]), np.array([1., 0, 0]), 20) is None


@pytest.fixture
def scene(tmp_path):
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    tools = AssemblyTools(tmp_path, native)
    result = tools.call("build", dict(revision=0, operations=demonstration_operations(rolling=True)))
    assert result["ok"], result
    ids = result["result"]["aliases"]
    return tools, dict(revision=1, moving=ids["wheel"], source=ids["axle"], source_port="0",
        target=ids["bearing"], target_port="2")


def test_native_diagnostic_is_readonly_and_never_certifies_retention(scene):
    tools, args = scene
    before = deepcopy(tools.assembly.graph()), (tools.directory / "state.json").read_bytes()
    result = tools.call("axial_clearance", args)
    assert result["ok"], result
    report = result["result"]
    assert report["evidence_files"] and report["evidence_unchanged"]
    assert report["physical_retention"] == report["load_capacity"] == "unverified"
    assert report["coverage"]["evaluated_pairs"] == report["coverage"]["candidate_pairs"]
    assert all(row["full_disengagement_covered"] for row in report["directions"].values())
    assert (tools.assembly.graph(), (tools.directory / "state.json").read_bytes()) == before


@pytest.mark.parametrize("fault", ["stale", "source_outside", "target_inside", "unknown", "port", "detached", "limit"])
def test_bad_calls_leave_state_unchanged(scene, fault):
    tools, args = scene
    if fault == "stale":
        args["revision"] = 0
    elif fault == "source_outside":
        args["moving"] = args["target"]
    elif fault == "target_inside":
        args["target"] = args["source"]
    elif fault == "unknown":
        args["moving"] = "invented"
    elif fault == "port":
        args["source_port"] = "invented"
    elif fault == "detached":
        args["source_port"] = "1"
    else:
        args["max_pairs"] = 0
    before = deepcopy(tools.assembly.graph()), (tools.directory / "state.json").read_bytes()
    assert not tools.call("axial_clearance", args)["ok"]
    assert (tools.assembly.graph(), (tools.directory / "state.json").read_bytes()) == before


def test_missing_collider_and_budget_never_become_clear(scene, monkeypatch):
    tools, args = scene
    limited = tools.call("axial_clearance", {**args, "max_pairs": 1})["result"]
    assert "collider_pair_budget_exhausted" in limited["issues"]
    assert all(row["status"] == "unverified" for row in limited["directions"].values())
    original = tools.catalog.boxes
    def missing(part):
        if part == "3700.dat":
            raise ValueError("Missing native collider")
        return original(part)
    monkeypatch.setattr(tools.catalog, "boxes", missing)
    report = tools.call("axial_clearance", args)["result"]
    assert report["unknown"]
    assert all(row["status"] == "unverified" for row in report["directions"].values())


def test_control_checkpoint_does_not_mutate_assembly(scene):
    tools, args = scene
    def cancel():
        raise RuntimeError("test cancellation")
    tools.checkpoint = cancel
    before = deepcopy(tools.assembly.graph())
    result = tools.call("axial_clearance", args)
    assert not result["ok"] and "test cancellation" in result["error"]
    assert tools.assembly.graph() == before


def test_truncated_travel_is_unverified_without_an_observed_blocker(scene):
    tools, args = scene
    report = tools.call("axial_clearance", {**args, "max_travel": .001})["result"]
    for row in report["directions"].values():
        assert not row["full_disengagement_covered"]
        if row["first_blocker"] is None:
            assert row["status"] == "unverified"


def test_evidence_change_cannot_certify_path(scene, monkeypatch):
    from car2lego3d import assembly_axial
    tools, args = scene
    monkeypatch.setattr(assembly_axial, "files_current", lambda _: False)
    report = tools.call("axial_clearance", args)["result"]
    assert "evidence_changed_during_diagnostic" in report["issues"]
    assert all(row["status"] == "unverified" for row in report["directions"].values())


def test_nonrigid_transform_rejected(scene):
    tools, args = scene
    graph = tools.assembly.graph()
    graph["instances"][0]["world"][0][0] = 2
    with pytest.raises(ValueError, match="rigid"):
        axial_clearance(graph, tools.catalog, **args)


def test_sampled_axial_tool_is_readonly_current_and_pose_specific(scene):
    tools, args = scene
    before = deepcopy(tools.assembly.graph()), (tools.directory / "state.json").read_bytes()
    result = tools.call("axial_motion", {**args, "joint": args["moving"]})
    assert result["ok"], result
    report = result["result"]
    assert check_current(before[0], report)
    assert report["evidence_unchanged"]
    assert report["physical_retention"] == report["load_capacity"] == "unverified"
    assert [row["value"] for row in report["poses"]] == list(np.linspace(0, 360, 9))
    assert len({row["graph_sha256"] for row in report["poses"]}) > 1
    assert (tools.assembly.graph(), (tools.directory / "state.json").read_bytes()) == before


@pytest.mark.parametrize("fault", ["samples", "joint", "cut", "revision"])
def test_sampled_axial_rejects_bad_scope(scene, fault):
    tools, args = scene
    kwargs = dict(**args, joint=args["moving"])
    kwargs.update({"samples": 8} if fault == "samples" else {"joint": "root/missing"} if fault == "joint"
        else {"moving": args["target"]} if fault == "cut" else {"revision": 0})
    before = deepcopy(tools.assembly.graph())
    assert not tools.call("axial_motion", kwargs)["ok"]
    assert tools.assembly.graph() == before


def test_sampled_axial_cannot_hide_a_single_pose_failure(scene, monkeypatch):
    from car2lego3d import assembly_axial
    tools, args = scene
    original = assembly_axial.axial_clearance
    calls = []
    def inspect(*a, **kw):
        report = original(*a, **kw)
        calls.append(report)
        if len(calls) == 3:
            report["issues"].append("test_incomplete_pose")
        return report
    monkeypatch.setattr(assembly_axial, "axial_clearance", inspect)
    report = axial_motion(tools.assembly.graph(), tools.catalog, **args, joint=args["moving"])
    assert len(report["poses"]) == 9
    assert report["classification"] == "unverified"
    assert "test_incomplete_pose" in report["poses"][1]["issues"]


def test_sampled_axial_reports_detached_contact_without_skipping_pose(scene, monkeypatch):
    from car2lego3d import assembly_axial
    tools, args = scene
    original = assembly_axial.axial_clearance
    count = 0
    def inspect(*a, **kw):
        nonlocal count
        count += 1
        if count == 4:
            raise ValueError("Selected ports have no verified native shaft contact")
        return original(*a, **kw)
    monkeypatch.setattr(assembly_axial, "axial_clearance", inspect)
    report = axial_motion(tools.assembly.graph(), tools.catalog, **args, joint=args["moving"])
    assert report["classification"] == "unverified"
    assert report["poses"][2]["issues"] == ["contact_unverified_at_pose"]
    assert len(report["poses"]) == 9


def test_sampled_axial_evidence_change_invalidates_aggregate(scene, monkeypatch):
    from car2lego3d import assembly_axial
    tools, args = scene
    monkeypatch.setattr(assembly_axial, "files_current", lambda _: False)
    report = axial_motion(tools.assembly.graph(), tools.catalog, **args, joint=args["moving"])
    assert not report["evidence_unchanged"]
    assert "evidence_changed_during_diagnostic" in report["issues"]
    assert report["classification"] == "unverified"


def test_complete_path_detects_thin_stop_and_rotated_parent(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.write_bytes(b"synthetic collider/port fixture")
    class Catalog:
        def evidence(self, *args):
            return evidence

        def ports(self, part):
            return (Port("0", "axle", 0, (0, 0, 0), (1, 0, 0), length=10,
                semantic="axle" if part == "shaft" else "round_hole"),)

        def boxes(self, part):
            return {"shaft": (box([-5, 0, 0], [5, 1, 1]),),
                "bearing": (box([-5, 5, 0], [5, 1, 1]),),
                "stop": (box([5, 0, 0], [.2, 2, 2]),)}[part]

    frame = np.eye(4)
    frame[:3, :3] = Rotation.from_euler("xyz", [29, 17, -48], degrees=True).as_matrix()
    frame[:3, 3] = [37, 12, -41]
    graph = dict(revision=0, joints={}, instances=[dict(id=name, part=part, kind="part", parent="root",
        local=frame.tolist(), world=frame.tolist()) for name, part in
        (("root/moving", "shaft"), ("root/bearing", "bearing"), ("root/stop", "stop"))])
    report = axial_clearance(graph, Catalog(), revision=0, moving="root/moving", source="root/moving",
        source_port="0", target="root/bearing", target_port="0")
    assert not report["issues"]
    assert report["directions"]["positive"]["status"] == "blocked"
    blocker = report["directions"]["positive"]["first_blocker"]
    assert blocker["fixed"] == "root/stop"
    assert blocker["entry_distance_ldu"] == pytest.approx(5.15)
    assert report["directions"]["negative"]["status"] == "clear"
    np.testing.assert_allclose(report["axis_world"], frame[:3, 0])
    # Initial penetration is a broken starting state, not proof of a stop.
    Catalog.boxes = lambda self, part: (box([0, 0, 0], [2, 2, 2]),)
    invalid = axial_clearance(graph, Catalog(), revision=0, moving="root/moving", source="root/moving",
        source_port="0", target="root/bearing", target_port="0")
    assert "initial_cross_subtree_collision" in invalid["issues"]
    assert all(row["status"] == "unverified" for row in invalid["directions"].values())
