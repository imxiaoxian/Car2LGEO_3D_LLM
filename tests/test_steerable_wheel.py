"""A steering trial must demonstrate geometry response and combined motion."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from car2lego3d.assembly_motion import evaluate_motion, posed_graph
from car2lego3d.common import write_json
from scripts import probe_steerable_wheel as probe


@pytest.fixture
def trial(tmp_path, monkeypatch):
    nodes = []

    def node(identity, parent="root", part=None):
        nodes.append(dict(id=identity, parent=parent, kind="part" if part else "module",
                          part=part or identity + ".ldr", color=71,
                          local=np.eye(4).tolist(), world=np.eye(4).tolist()))

    node("support", part="support.dat")
    node("steering")
    node("kingpin", "steering", "axle.dat")
    node("bearing", "steering", "bearing.dat")
    node("rolling", "steering")
    for role in ("axle", "rim", "tyre"):
        node(role, "rolling", role + ".dat")
    graph = dict(revision=2, instances=nodes, joints={
        "steering": dict(type="revolute", axis=[0, 1, 0], origin=[0, 0, 0],
                         limits=[-30, 30], purpose="steering"),
        "rolling": dict(type="revolute", axis=[0, 0, 1], origin=[0, 0, 0],
                        limits=[0, 360], purpose="rolling")})
    report = dict(status="pass", collisions=[], unknown=[], invalid_transforms=[],
                  gates=dict(connections="pass"), connections=[dict(a=a, b=b) for a, b in
                  (("support", "kingpin"), ("rim", "tyre"), ("axle", "rim"))])
    motion = evaluate_motion(graph, lambda _: deepcopy(report))
    write_json(tmp_path / "checks.json", report)
    write_json(tmp_path / "motion.json", motion)
    # These tests isolate host acceptance rules; native geometry is covered by live trials.
    library = SimpleNamespace(triangles=lambda _: (np.array([[[0, 0, 0], [20, 0, 0], [0, 20, 0]]]), []))
    tools = SimpleNamespace(directory=tmp_path, assembly=SimpleNamespace(graph=lambda: graph),
                            catalog=SimpleNamespace(library=library))
    monkeypatch.setattr(probe, "native_role", lambda _, name: (
        (name.removesuffix(".dat") if name in {"axle.dat", "rim.dat", "tyre.dat"} else None), name))
    monkeypatch.setattr(probe, "selection_evidence", lambda *_: dict(status="pass"))
    monkeypatch.setattr(probe, "check_current", lambda *_: True)
    return tools, deepcopy(nodes[0]), graph, motion


def test_nested_response_and_combined_grid_are_required(trial):
    tools, support, _, _ = trial
    result = probe.evidence(tools, support)
    assert result["status"] == "pass", result
    assert result["combined_poses"] == 81
    assert min(result["maximum_tyre_vertex_displacement_ldu"].values()) > 1
    assert result["physical_retention"] == result["load_capacity"] == result["assembly_order"] == "unverified"


@pytest.mark.parametrize("fault", ["missing", "failed", "incomplete", "wrong_joints"])
def test_combined_motion_cannot_be_replaced_by_individual_sweeps(trial, fault):
    tools, support, _, motion = trial
    if fault == "missing":
        motion["combined"] = []
    elif fault == "failed":
        motion["combined"][0]["poses"][0]["status"] = "fail"
    elif fault == "incomplete":
        motion["combined"][0]["poses"].pop()
    else:
        motion["combined"][0]["instances"] = ["steering", "another_wheel"]
    write_json(tools.directory / "motion.json", motion)
    assert "requires_passing_combined_steering_and_rolling_grid" in probe.evidence(tools, support)["issues"]


def test_sibling_wheel_does_not_count_as_steered(trial):
    tools, support, graph, _ = trial
    next(node for node in graph["instances"] if node["id"] == "rolling")["parent"] = "root"
    assert "rolling_wheel_must_descend_from_steering_carrier" in probe.evidence(tools, support)["issues"]


def test_fixed_support_and_current_native_reports_are_required(trial, monkeypatch):
    tools, support, graph, _ = trial
    graph["instances"][0]["color"] = 4
    monkeypatch.setattr(probe, "check_current", lambda *_: False)
    issues = probe.evidence(tools, support)["issues"]
    assert {"fixed_support_changed", "check_missing_failed_or_stale", "motion_missing_failed_or_stale"} <= set(issues)


def test_part_selection_and_real_tyre_response_are_not_labels(trial, monkeypatch):
    tools, support, graph, _ = trial
    neutral = posed_graph(graph, {})
    monkeypatch.setattr(probe, "posed_graph", lambda *_: deepcopy(neutral))
    assert {"actual_tyre_geometry_did_not_respond_to_steering",
            "actual_tyre_geometry_did_not_respond_to_rolling"} <= set(probe.evidence(tools, support)["issues"])
    monkeypatch.setattr(probe, "selection_evidence", lambda *_: dict(status="fail"))
    assert "selection_not_observed_before_placement" in probe.evidence(tools, support)["issues"]
