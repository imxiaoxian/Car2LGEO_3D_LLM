"""Guard integration evidence; synthetic reports do not establish native assembly."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from car2lego3d.common import write_json
from scripts import probe_cabin_integration as probe


def native_check():
    return dict(status="pass", gates={key: "pass" for key in
        ("parts", "transforms", "connections", "collisions", "digital_assembly")})


@pytest.fixture
def mechanism():
    profile = dict(driver="driver", passive=["tie"], rolling=["wheel" + str(n) for n in range(4)],
                   contacts=[dict(a="a", b="b", port_a="0", port_b="1")])
    graph = dict(joints={key: dict(limits=[0, 360] if key.startswith("wheel") else [-30, 30])
                        for key in [profile["driver"], *profile["passive"], *profile["rolling"]]})
    def pose(a, root=None, b=None):
        return dict(positions={"driver": float(a), "tie": float(a), **({root: float(b)} if root else {})},
                    status="pass", lost_contacts=[], check=native_check())
    report = dict(status="pass", **deepcopy(profile),
        poses=[pose(a) for a in np.linspace(-30, 30, 9)],
        combined=[dict(rolling=root, poses=[pose(a, root, b) for a in np.linspace(-30, 30, 9)
                                           for b in np.linspace(0, 360, 9)]) for root in profile["rolling"]])
    return graph, profile, report


def test_complete_linkage_coverage_does_not_mutate_reports(mechanism):
    graph, profile, report = mechanism
    original = deepcopy(report)
    issues, poses = probe.linkage_issues(graph, report, profile)
    assert not issues and len(poses) == 333
    assert report == original


@pytest.mark.parametrize("fault,expected", [
    ("pose", "requires_nine_original_linked_steering_poses"),
    ("rolling_pose", "requires_324_original_rolling_combinations"),
    ("duplicate_group", "requires_four_distinct_rolling_sweeps"),
    ("contact", "original_native_contacts_missing"),
    ("lost", "native_linkage_pose_failed_or_missing"),
    ("collision", "native_linkage_pose_failed_or_missing"),
    ("stale_profile", "original_linkage_not_passed"),
])
def test_incomplete_or_failed_linkage_cannot_pass(mechanism, fault, expected):
    graph, profile, report = mechanism
    if fault == "pose":
        report["poses"].pop()
    elif fault == "rolling_pose":
        report["combined"][0]["poses"].pop()
    elif fault == "duplicate_group":
        report["combined"][0] = deepcopy(report["combined"][1])
    elif fault == "contact":
        report["contacts"] = []
    elif fault == "lost":
        report["combined"][0]["poses"][0]["lost_contacts"] = ["a"]
    elif fault == "collision":
        report["combined"][0]["poses"][0]["check"]["gates"]["collisions"] = "fail"
    else:
        report["driver"] = "different"
    assert expected in probe.linkage_issues(graph, report, profile)[0]


def node(key, kind, parent="root", position=(0, 0, 0)):
    matrix = np.eye(4)
    matrix[:3, 3] = position
    return dict(id=key, kind=kind, parent=parent, part=key + ".dat", color=0,
                world=matrix.tolist(), local=matrix.tolist())


@pytest.fixture
def cabin(tmp_path, monkeypatch, mechanism):
    graph, profile, linked = mechanism
    originals = [node("frame", "module"), *[node(key, "module") for key in graph["joints"]]]
    joints = {key: dict(**joint, axis=[0, 1, 0], origin=[0, 0, 0],
                       purpose="rolling" if key.startswith("wheel") else "steering")
              for key, joint in graph["joints"].items()}
    seed = dict(instances=originals, joints=joints)
    new = [node("left", "part", "cabin", (110, -60, -25)),
           node("right", "part", "cabin", (110, -60, 25)),
           node("dash", "part", "cabin", (70, -80, 0)),
           node("handwheel", "part", "cabin", (85, -85, -25))]
    graph = dict(instances=deepcopy(originals) + [node("cabin", "module")] + new, joints=deepcopy(joints), revision=1)
    descriptions = dict(left="Seat 2 x 2", right="Seat 2 x 2", dash="Slope 2 x 3", handwheel="Steering Wheel Small")
    for key, description in descriptions.items():
        (tmp_path / (key + ".dat")).write_text("0 " + description + "\n", encoding="utf-8")
    def triangles(part):
        extent = [10, 10, 30 if part == "dash.dat" else 10]
        return np.array([[-np.array(extent), extent, -np.array(extent)]]), None
    library = SimpleNamespace(resolve=lambda part: tmp_path / part, triangles=triangles)
    tools = SimpleNamespace(directory=tmp_path, assembly=SimpleNamespace(graph=lambda: graph),
        module_bindings=dict(cabin=dict(roots=["cabin"], roles=dict(seats=["left", "right"],
            dashboard=["dash"], steering_wheel=["handwheel"]))), catalog=SimpleNamespace(library=library))
    motion = dict(status="pass", unknown_joint_purposes=[],
        joints=[dict(instance=key, poses=[dict(value=float(value), status="pass")
                for value in np.linspace(*joint["limits"], 9)]) for key, joint in joints.items()])
    for filename, data in (("checks.json", native_check()), ("motion.json", motion), ("linkage-motion.json", linked)):
        write_json(tmp_path / filename, data)
    monkeypatch.setattr(probe, "check_current", lambda *_: True)
    monkeypatch.setattr(probe, "module_check", lambda *_args, **_kwargs: dict(status="pass"))
    monkeypatch.setattr(probe, "selection_evidence", lambda *_: dict(status="pass"))
    envelope = dict(front_x=40, rear_x=160, left_z=-90, right_z=90, center_z=0, ground_y=10)
    return tools, seed, profile, envelope


def test_local_evidence_never_certifies_whole_vehicle(cabin):
    result = probe.evidence(*cabin)
    assert result["status"] == "pass", result["issues"]
    assert result["linked_pose_count"] == 9 and result["rolling_pose_count"] == 324
    assert result["vehicle_completion_ready"] is False
    assert result["steering_wheel_linkage"] == result["semantic_roles"] == result["appearance"] == "unverified"


@pytest.mark.parametrize("fault,expected", [
    ("frame", "protected_chassis_or_joints_changed"),
    ("limits", "protected_chassis_or_joints_changed"),
    ("seat_role", "seat_role_requires_native_seat:left"),
    ("wheel_role", "steering_wheel_requires_native_steering_wheel:handwheel"),
    ("outside", "cabin_role_outside_axles_track_or_ground:left"),
    ("seats_same_side", "two_seats_must_straddle_chassis_centerline"),
    ("dashboard_behind", "dashboard_and_wheel_must_be_ahead_of_both_seats"),
    ("moving_parent", "all_new_geometry_must_belong_to_independent_fixed_cabin"),
    ("extra_part", "all_new_geometry_must_belong_to_independent_fixed_cabin"),
    ("ordinary_pose", "native_ordinary_motion_not_passed"),
])
def test_incorrect_integration_fails(cabin, fault, expected):
    tools, seed, profile, envelope = cabin
    nodes = {n["id"]: n for n in tools.assembly.graph()["instances"]}
    if fault == "frame":
        nodes["frame"]["color"] = 15
    elif fault == "limits":
        tools.assembly.graph()["joints"]["driver"]["limits"] = [-15, 15]
    elif fault in {"seat_role", "wheel_role"}:
        key = "left" if fault == "seat_role" else "handwheel"
        (tools.directory / (key + ".dat")).write_text("0 Brick 2 x 2\n", encoding="utf-8")
    elif fault in {"outside", "seats_same_side", "dashboard_behind"}:
        key, axis, value = {"outside": ("left", 0, 190), "seats_same_side": ("left", 2, 25),
                            "dashboard_behind": ("dash", 0, 140)}[fault]
        nodes[key]["world"][axis][3] = nodes[key]["local"][axis][3] = value
    elif fault == "moving_parent":
        nodes["cabin"]["parent"] = "driver"
    elif fault == "extra_part":
        tools.assembly.graph()["instances"].append(node("stray", "part"))
    else:
        path = tools.directory / "motion.json"
        report = json.loads(path.read_text())
        report["joints"][0]["poses"].pop()
        write_json(path, report)
    assert expected in probe.evidence(*cabin)["issues"]


def test_stale_reports_cannot_pass(cabin, monkeypatch):
    monkeypatch.setattr(probe, "check_current", lambda *_: False)
    assert {"check_missing_or_stale", "motion_missing_or_stale", "linkage_missing_or_stale"} <= set(
        probe.evidence(*cabin)["issues"])


def test_seeded_type_needs_new_selection_before_cabin_placement(cabin, monkeypatch):
    tools = cabin[0]
    write_json(tools.directory / "seed-introduction.json", {})
    trace = [dict(tool="build", ok=True, arguments=dict(operations=[dict(part="left.dat")]), result={}),
             dict(tool="search_parts", ok=True, arguments=dict(query="seat"), result=dict(parts=[dict(part="left.dat")])),
             dict(tool="query_part", ok=True, arguments={}, result=dict(status="pass", part="left.dat")),
             dict(tool="build", ok=True, arguments=dict(operations=[dict(part="left.dat")]), result={})]
    monkeypatch.setattr(probe, "records", lambda *_: trace)
    from scripts.probe_autonomous_wheel import selection_evidence
    monkeypatch.setattr(probe, "selection_evidence", selection_evidence)
    full = probe.evidence(*cabin)["selection"]
    scoped = probe.evidence(*cabin, selection_start=1)["selection"]
    assert full["parts"]["left.dat"]["status"] == "fail"
    assert scoped["parts"]["left.dat"]["status"] == "pass"
    assert scoped["status"] == "fail"  # The other new types still lack selection.
