from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d import assembly_linkage
from car2lego3d.assembly_linkage import evaluate_linkage
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.assembly_evidence import check_current, files_current, snapshot_files
from car2lego3d.assembly_requirements import identity
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.job_runtime import AssemblyCancelled
from car2lego3d.native_ports import Port


class Catalog:
    library = None

    def __init__(self):
        self.library = self

    def ports(self, name):
        def port(identity, position, subtype):
            return Port(identity, "hinge", subtype, position, (0, 1, 0), forward=(0, 0, 1))
        if name == "ground":
            return [port("a", (0, 0, 0), 0), port("b", (0, 0, 80), 0)]
        if name == "arm":
            return [port("a", (0, 0, 0), 1), port("b", (20, 0, 0), 0)]
        return [port("a", (0, 0, 0), 1), port("b", (0, 0, 80), 1)]

    def triangles(self, name):
        return np.ones((1, 3, 3)), None

    def boxes(self, name):
        center = {"ground": [-10, 0, 40], "arm": [10, 0, 0], "tie": [0, 0, 40], "wheel": [3, 10, 0]}[name]
        return [(np.array(center), np.eye(3), np.ones(3))]


def fixture():
    def node(identity, parent, position, part=None):
        local = np.eye(4)
        local[:3, 3] = position
        return dict(id=identity, parent=parent, local=local.tolist(), kind="part" if part else "module",
            **({"part": part} if part else {}))
    graph = dict(revision=1, instances=[node("ground", "root", [0, 0, 0], "ground"),
        node("left", "root", [0, 0, 0]), node("right", "root", [0, 0, 80]),
        node("tie", "left", [20, 0, 0]), node("la", "left", [0, 0, 0], "arm"),
        node("ra", "right", [0, 0, 0], "arm"), node("tb", "tie", [0, 0, 0], "tie")],
        joints={name: dict(axis=[0, 1, 0], origin=origin, limits=[-45, 45], purpose="steering")
            for name, origin in (("left", [0, 0, 0]), ("right", [0, 0, 80]), ("tie", [20, 0, 0]))})
    graph["joints"]["left"]["limits"] = [-30, 30]
    return posed_graph(graph, {})


def test_four_bar_solves_passive_angles_without_mutation():
    graph = fixture()
    before = deepcopy(graph)
    report = evaluate_linkage(graph, Catalog(), "left", ["right", "tie"])
    assert graph == before
    assert report["status"] == "pass"
    assert report["driver_requires_passive_response"]
    assert len(report["poses"]) == 9 and len(report["contacts"]) == 4
    for pose in report["poses"]:
        angles = pose["positions"]
        assert angles["right"] == pytest.approx(angles["left"], abs=1e-5)
        assert angles["tie"] == pytest.approx(-angles["left"], abs=1e-5)
        assert not pose["lost_contacts"] and pose["passive_rank"] == 2
    assert report["physical"] == report["force_transmission"] == report["intermediate_poses"] == "unverified"


def test_passive_limits_cannot_be_ignored():
    graph = fixture()
    graph["joints"]["right"]["limits"] = [-5, 5]
    report = evaluate_linkage(graph, Catalog(), "left", ["right", "tie"])
    assert report["status"] != "pass"
    assert any(row["lost_contacts"] for row in report["poses"])
    assert all(abs(row["positions"]["right"]) <= 5 for row in report["poses"])


def test_global_connectivity_does_not_hide_lost_original_contact(monkeypatch):
    native_validate = assembly_linkage.validate
    calls = 0
    def lost(*args, **kwargs):
        nonlocal calls
        result = native_validate(*args, **kwargs)
        calls += 1
        if calls > 1:
            result["connections"] = result["connections"][:-1]
        return result
    monkeypatch.setattr(assembly_linkage, "validate", lost)
    report = evaluate_linkage(fixture(), Catalog(), "left", ["right", "tie"])
    assert report["status"] == "fail"
    assert all(pose["check"]["status"] == "pass" and pose["lost_contacts"] for pose in report["poses"])


def test_budget_exhaustion_is_unverified():
    report = evaluate_linkage(fixture(), Catalog(), "left", ["right", "tie"], max_evaluations=1)
    assert report["status"] == "unverified" and report["issue"] == "residual_budget_exhausted"
    assert report["residual_evaluations"] == 1


def test_missing_evidence_never_passes():
    class Missing(Catalog):
        def boxes(self, name):
            raise ValueError("no native collider")
    report = evaluate_linkage(fixture(), Missing(), "left", ["right", "tie"])
    assert report["status"] == "unverified" and not report["poses"]


def test_independent_passive_joint_cannot_claim_linkage():
    graph = fixture()
    graph["instances"] = [n for n in graph["instances"] if n["id"] != "tb"]
    report = evaluate_linkage(graph, Catalog(), "left", ["right", "tie"])
    assert report["status"] == "unverified"
    assert all(row["passive_rank"] < 2 for row in report["poses"])


def test_cancellation_propagates():
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 7:
            raise AssemblyCancelled("cancel linkage")
    with pytest.raises(AssemblyCancelled):
        evaluate_linkage(fixture(), Catalog(), "left", ["right", "tie"], checkpoint=cancel)


def rolling_fixture():
    graph = fixture()
    module = deepcopy(next(n for n in graph["instances"] if n["id"] == "tie"))
    module["id"] = "rolling"
    part = deepcopy(next(n for n in graph["instances"] if n["id"] == "tb"))
    part.update(id="wheel", parent="rolling", part="wheel")
    graph["instances"].extend([module, part])
    graph["joints"]["rolling"] = dict(axis=[0, 1, 0], origin=[20, 0, 0],
        limits=[0, 360], purpose="rolling")
    class WheelCatalog(Catalog):
        def ports(self, name):
            if name == "wheel":
                return [replace(super().ports("tie")[0], id="a")]
            return super().ports(name)
    return posed_graph(graph, {}), WheelCatalog()


def test_rolling_sweeps_every_solved_linkage_pose():
    graph, catalog = rolling_fixture()
    report = evaluate_linkage(graph, catalog, "left", ["right", "tie"], rolling=["rolling"])
    assert report["status"] == "pass"
    assert len(report["combined"][0]["poses"]) == 81
    assert all(p["status"] == "pass" for p in report["combined"][0]["poses"])


def test_combined_rolling_collision_cannot_hide_behind_clear_linkage(monkeypatch):
    graph, catalog = rolling_fixture()
    native_validate = assembly_linkage.validate
    def colliding(pose, *args, **kwargs):
        report = native_validate(pose, *args, **kwargs)
        nodes = {n["id"]: n for n in pose["instances"]}
        if np.asarray(nodes["la"]["world"])[0, 2] > .2 and np.asarray(nodes["wheel"]["world"])[0, 0] < -.5:
            report.update(status="fail", collisions=[dict(a="wheel", b="ground")])
        return report
    monkeypatch.setattr(assembly_linkage, "validate", colliding)
    report = evaluate_linkage(graph, catalog, "left", ["right", "tie"], rolling=["rolling"])
    assert all(p["status"] == "pass" for p in report["poses"])
    assert report["status"] == "fail"
    assert any(p["status"] == "fail" for p in report["combined"][0]["poses"])


def test_combined_pose_budget_is_bounded():
    graph, catalog = rolling_fixture()
    with pytest.raises(ValueError, match="2000 combined poses"):
        evaluate_linkage(graph, catalog, "left", ["right", "tie"], samples=181, rolling=["rolling"])


def test_native_roundoff_does_not_become_a_new_alignment_requirement():
    class Rounded(Catalog):
        def ports(self, name):
            result = super().ports(name)
            if name == "tie":
                result[0] = replace(result[0], position=(0, .0003, 0))
                result[1] = replace(result[1], position=(0, .0003, 80))
            return result
    report = evaluate_linkage(fixture(), Rounded(), "left", ["right", "tie"])
    assert report["status"] == "pass"
    assert max(row["residual_max"] for row in report["poses"]) < 1e-5


@pytest.mark.parametrize("driver,passive", [("left", ["left"]), ("missing", ["right"]), ("left", [])])
def test_invalid_joint_selection_rejected(driver, passive):
    with pytest.raises(ValueError):
        evaluate_linkage(fixture(), Catalog(), driver, passive)


@pytest.mark.parametrize("arguments", [dict(driver="a", passive=["b", "b"]),
    dict(driver="a", passive=[]), dict(driver="a", passive=["b"], rolling=[]),
    dict(driver="a", passive=["b"], samples=8), dict(driver="a", passive=["b"], max_evaluations=0)])
def test_protocol_rejects_invalid_linkage_arguments(arguments):
    from car2lego3d.assembly_protocol import validate_call
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        validate_call("linkage_motion", arguments)


@pytest.mark.parametrize("changed", [False, True])
def test_tool_binds_canonical_snapshot_before_evaluation(tmp_path, monkeypatch, changed):
    from car2lego3d import assembly_tools
    tools = AssemblyTools(tmp_path / "assembly", tmp_path / "native")
    dependency = tmp_path / "native-source.dat"
    dependency.write_bytes(b"before")
    graph = tools.assembly.graph()
    monkeypatch.setattr(assembly_tools, "validate", lambda *args, **kwargs: {})
    monkeypatch.setattr(tools, "verification_inputs", lambda: dict(graph_sha256=identity(graph),
        evidence_files=snapshot_files([dependency])))
    seen = []
    def evaluate(*args, **kwargs):
        seen.append((args, kwargs))
        if changed:
            dependency.write_bytes(b"after")
        return dict(status="pass", revision=graph["revision"])
    monkeypatch.setattr(assembly_linkage, "evaluate_linkage", evaluate)
    result = tools.call("linkage_motion", dict(driver="left", passive=["right"], rolling=["wheel"]))
    assert result["ok"]
    report = result["result"]
    assert seen[0][1]["rolling"] == ["wheel"]
    assert tools.assembly.graph() == graph
    assert report["status"] == ("unverified" if changed else "pass")
    assert files_current(report["evidence_files"]) is not changed
    assert check_current(graph, report) is not changed
    assert {"assembly_linkage.py", "assembly_motion.py", "assembly_catalog.py", "native_ports.py"} <= {
        Path(item["file"]).name for item in report["evidence_files"]}
    assert json.loads((tools.directory / "linkage-motion.json").read_text()) == report
    assert not (tools.directory / "motion.json").exists()
    if changed:
        assert report["issue"] == "evidence_changed_during_evaluation"


def test_native_four_bar_tool_preserves_state_and_current_evidence(tmp_path):
    from scripts.probe_linkage import operations
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Studio native library unavailable")
    tools = AssemblyTools(tmp_path, native)
    built = tools.call("build", dict(revision=0, operations=operations()))
    assert built["ok"], built
    aliases = built["result"]["aliases"]
    graph, state = deepcopy(tools.assembly.graph()), (tmp_path / "state.json").read_bytes()
    result = tools.call("linkage_motion", dict(driver=aliases["left"],
        passive=[aliases["right"], aliases["tie"]]))
    assert result["ok"] and result["result"]["status"] == "pass", result
    report = result["result"]
    assert check_current(graph, report)
    assert len(report["contacts"]) == 8 and len(report["poses"]) == 9
    assert state == (tmp_path / "state.json").read_bytes() and tools.assembly.graph() == graph
    assert AssemblyTools(tmp_path, native).assembly.graph() == graph
