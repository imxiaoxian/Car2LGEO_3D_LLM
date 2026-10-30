from copy import deepcopy

import numpy as np
import pytest

from car2lego3d.assembly_motion import evaluate_motion, posed_graph
from car2lego3d.job_runtime import AssemblyCancelled


def graph():
    def node(identity, parent, x):
        local = np.eye(4)
        local[0, 3] = x
        return dict(id=identity, parent=parent, local=local.tolist(), world=local.tolist())
    return dict(revision=7, instances=[node("root/carrier", "root", 10),
        node("root/carrier/hub", "root/carrier", 4)], joints={
            "root/carrier": dict(type="prismatic", axis=[0, 1, 0], origin=[0, 0, 0],
                limits=[0, 8], purpose="suspension"),
            "root/carrier/hub": dict(axis=[0, 0, 1], origin=[0, 0, 0],
                limits=[0, 90], purpose="steering")})


def clear(_):
    return dict(collisions=[], unknown=[], invalid_transforms=[], gates=dict(connections="pass"))


def test_parent_and_child_movements_compose_in_the_moving_parent_frame():
    source = graph()
    before = deepcopy(source)
    posed = posed_graph(source, {"root/carrier": 3, "root/carrier/hub": 90})
    assert np.allclose(np.asarray(posed["instances"][1]["world"])[:3, 3], [10, 7, 0])
    assert source == before


def test_combined_suspension_and_steering_collision_is_detected():
    def collision(pose):
        report = clear(pose)
        matrix = np.asarray(pose["instances"][1]["world"])
        if matrix[1, 3] > 11:
            report["collisions"] = [{"a": "hub", "b": "body"}]
        return report
    result = evaluate_motion(graph(), collision)
    assert all(p["status"] == "pass" for row in result["joints"] for p in row["poses"])
    assert result["status"] == result["combined_joint_states"] == "fail"
    assert len(result["combined"][0]["poses"]) >= 81


def test_unknown_geometry_or_unnamed_joint_does_not_pass():
    unknown = evaluate_motion(graph(), lambda _: dict(collisions=[], unknown=["no collider"], invalid_transforms=[]))
    assert unknown["status"] == "unverified"
    source = graph()
    del source["joints"]["root/carrier"]["purpose"]
    assert evaluate_motion(source, clear)["status"] == "unverified"
    source["joints"] = {}
    assert evaluate_motion(source, clear)["status"] == "unverified"


def test_clear_sampled_motion_keeps_scope_and_physical_limits_explicit():
    result = evaluate_motion(graph(), clear)
    assert result["status"] == result["combined_joint_states"] == "pass"
    assert result["physical"] == result["unsampled_multi_joint_combinations"] == "unverified"


def test_collision_free_but_detached_joint_is_not_passing_motion():
    def detached(pose):
        report = clear(pose)
        if np.asarray(pose["instances"][0]["world"])[1, 3] > 4:
            report["gates"]["connections"] = "fail"
        return report
    result = evaluate_motion(graph(), detached)
    assert result["status"] == "fail"
    assert any(p["connectivity"] == "fail" for row in result["joints"] for p in row["poses"])


def test_missing_connection_measurement_remains_unverified():
    result = evaluate_motion(graph(), lambda _: dict(collisions=[], unknown=[], invalid_transforms=[]))
    assert result["status"] == "unverified"


def test_radian_sized_rolling_range_cannot_pass_clear_sampled_motion():
    source = graph()
    source["joints"] = {"root/carrier": dict(type="revolute", axis=[0, 0, 1],
        origin=[10, 0, 0], limits=[-np.pi, np.pi], purpose="rolling")}
    result = evaluate_motion(source, clear)
    assert all(p["status"] == "pass" for p in result["joints"][0]["poses"])
    assert result["status"] == "fail"
    assert result["joints"][0]["range_issues"] == ["rolling_requires_360_degrees"]
    assert result["joints"][0]["unit"] == "degrees"
    source["joints"]["root/carrier"]["limits"] = [-180, 180]
    complete = evaluate_motion(source, clear)
    assert complete["status"] == "pass" and not complete["joints"][0]["range_issues"]
    assert len(complete["joints"][0]["poses"]) == 9


def test_prismatic_joint_cannot_represent_wheel_rolling():
    source = graph()
    source["joints"]["root/carrier"].update(purpose="rolling", limits=[0, 360])
    result = evaluate_motion(source, clear)
    assert result["status"] == "fail"
    assert result["joints"][0]["range_issues"] == ["rolling_requires_revolute_joint"]


@pytest.mark.parametrize("stop_at", [1, 5, 19, 30, 100])
def test_checkpoint_interrupts_individual_and_combined_poses(stop_at):
    validated, checkpoints = [], []

    def checkpoint():
        checkpoints.append(True)
        if len(checkpoints) == stop_at:
            raise AssemblyCancelled("stop")

    def validate(pose):
        validated.append(pose)
        return clear(pose)

    source = graph()
    before = deepcopy(source)
    with pytest.raises(AssemblyCancelled):
        evaluate_motion(source, validate, checkpoint=checkpoint)
    assert len(validated) == stop_at - 1
    assert source == before


def test_checkpoint_covers_collision_refinement_without_changing_measurements():
    checkpoints = []

    def collision(pose):
        return dict(clear(pose), collisions=[dict(a="hub", b="body")])

    expected = evaluate_motion(graph(), collision)
    actual = evaluate_motion(graph(), collision, checkpoint=lambda: checkpoints.append(True))
    assert actual == expected
    poses = sum(len(row["poses"]) for row in [*actual["joints"], *actual["combined"]])
    assert len(checkpoints) == poses + 1


def test_final_checkpoint_prevents_cancelled_last_pose_returning_a_report():
    validated = []

    def checkpoint():
        if len(validated) == 9:
            raise AssemblyCancelled("stop")

    def validate(pose):
        validated.append(pose)
        return clear(pose)

    source = graph()
    source["joints"] = {"root/carrier": source["joints"]["root/carrier"]}
    with pytest.raises(AssemblyCancelled):
        evaluate_motion(source, validate, checkpoint=checkpoint)
    assert len(validated) == 9


def steerable_wheel():
    source = graph()
    # Joint ancestry comes from parent links, not occurrence-name prefixes.
    source["instances"][1]["id"] = "wheel"
    source["joints"] = {
        "root/carrier": dict(type="revolute", axis=[0, 1, 0], origin=[0, 0, 0],
                             limits=[-30, 30], purpose="steering"),
        "wheel": dict(type="revolute", axis=[0, 0, 1], origin=[0, 0, 0],
                      limits=[0, 360], purpose="rolling"),
    }
    return source


@pytest.mark.parametrize("fault", ["collision", "detachment", "missing_geometry"])
def test_nested_rolling_is_checked_while_steered(fault):
    source = steerable_wheel()
    before = deepcopy(source)

    def validate(pose):
        report = clear(pose)
        world = np.asarray(pose["instances"][1]["world"])
        if abs(world[2, 1]) > .2:
            if fault == "collision":
                report["collisions"] = [dict(a="wheel", b="frame")]
            elif fault == "detachment":
                report["gates"]["connections"] = "fail"
            else:
                report["unknown"] = ["missing collider"]
        return report

    result = evaluate_motion(source, validate)
    assert all(p["status"] == "pass" for row in result["joints"] for p in row["poses"])
    assert result["status"] == result["combined_joint_states"] == (
        "unverified" if fault == "missing_geometry" else "fail")
    assert result["combined"][0]["instances"] == ["root/carrier", "wheel"]
    assert len(result["combined"][0]["poses"]) == 81
    assert source == before


def test_nested_suspension_rolling_and_steering_pairs_preserve_scope():
    source = steerable_wheel()
    source["instances"].append(dict(id="suspension", parent="root/carrier",
                                   local=np.eye(4).tolist(), world=np.eye(4).tolist()))
    source["instances"][1]["parent"] = "suspension"
    source["joints"]["suspension"] = dict(type="prismatic", axis=[0, 1, 0],
                                         limits=[0, 8], purpose="suspension")
    result = evaluate_motion(source, clear)
    assert {tuple(row["instances"]) for row in result["combined"]} == {
        ("root/carrier", "suspension"), ("root/carrier", "wheel"), ("suspension", "wheel")}
    assert result["status"] == "pass"
    assert result["unsampled_multi_joint_combinations"] == "unverified"
    assert all(len(row["poses"]) == 81 for row in result["combined"])


def test_unrelated_wheel_does_not_add_nested_rolling_pairs():
    source = steerable_wheel()
    source["instances"][1]["parent"] = "root"
    result = evaluate_motion(source, clear)
    assert result["status"] == "pass"
    assert result["combined_joint_states"] == "not_applicable"
    assert not result["combined"]


def test_checkpoint_interrupts_nested_rolling_combinations():
    count = 0

    def checkpoint():
        nonlocal count
        count += 1
        if count == 20:
            raise AssemblyCancelled("stop combined wheel sweep")

    with pytest.raises(AssemblyCancelled):
        evaluate_motion(steerable_wheel(), clear, checkpoint=checkpoint)
    assert count == 20
