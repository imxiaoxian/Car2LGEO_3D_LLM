import numpy as np

from car2lego3d.semantic import SCHEMA
from car2lego3d.staged_geometry import phase_geometry


def node(identity, role, matrix, parent=None, local=None, **extra):
    return dict(id=identity, role=role, parent=parent, world_transform=matrix.tolist(),
                local_transform=(matrix if local is None else local).tolist(), **extra)


def test_phase_interfaces_transform_all_boundary_points_and_joint_parent_frame():
    frame = np.array([[0., -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 30], [0, 0, 0, 1]])
    child = np.eye(4)
    child[0, 3] = 2
    points = [[0., 0, 0], [1., 0, 0], [0., 1, 0]]
    scene = dict(schema=SCHEMA, units="metres", nodes=[
        node("cabin", "cabin", frame),
        node("cowl", "cowl", frame @ child, "cabin", child,
            geometry=dict(vertices_m=points, triangles=[[0, 1, 2]], material_indices=[0]),
            boundaries={"join_windscreen": dict(frame="object", source="original-control-mesh", points_m=points)},
            ports=[dict(id="seat", kind="mount", provenance="design-inference", position_m=[0, 0, 0], axis=[1, 0, 0])],
            joint=dict(type="revolute", frame="parent", origin_m=[1, 0, 0], axis=[1, 0, 0], limits=[0, 1]))])
    context = phase_geometry(scene, "front")
    cowl = context["objects"][1]
    assert cowl["world_bounds_m"] == [[9, 22, 30], [10, 23, 30]]
    assert context["objects"][0]["world_bounds_m"] == cowl["world_bounds_m"]
    assert cowl["joint_world"]["origin_m"] == [10, 21, 30]
    assert cowl["joint_world"]["axis"] == [0, 1, 0]
    interface = context["interfaces"][0]
    assert interface["boundaries"]["join_windscreen"]["points_m"] == [[10, 22, 30], [10, 23, 30], [9, 22, 30]]
    assert interface["ports"][0]["position_m"] == [10, 22, 30]
    assert interface["ports"][0]["axis"] == [0, 1, 0]
    assert scene["nodes"][1]["boundaries"]["join_windscreen"]["points_m"] == points


def test_other_modules_remain_discoverable_without_replacing_their_geometry():
    points = [[float(i), 0, 0] for i in range(600)]
    scene = dict(schema=SCHEMA, units="metres", nodes=[node("deck", "engine_deck", np.eye(4),
        geometry=dict(vertices_m=points[:3], triangles=[[0, 1, 2]], material_indices=[0]),
        boundaries={"join_rear_window": dict(frame="object", points_m=points)})])
    front = phase_geometry(scene, "front")
    assert front["interfaces"] == []
    assert front["objects"][0]["boundary_groups"] == {"join_rear_window": 600}
    assert len(phase_geometry(scene, "rear")["interfaces"][0]["boundaries"]["join_rear_window"]["points_m"]) == 600
    assert len(phase_geometry(scene, "details")["interfaces"]) == 1
    assert scene["nodes"][0]["geometry"]["vertices_m"] == points[:3]


def test_initial_phase_has_no_invented_geometry():
    assert phase_geometry(None, "running-gear")["objects"] == []


def test_world_prismatic_travel_respects_parent_scale_and_preserves_original_joint():
    frame = np.diag([2., 3, 4, 1])
    joint = dict(type="prismatic", frame="parent", origin_m=[0, 0, 0], axis=[0, 0, 1], limits=[-.03, .06])
    scene = dict(schema=SCHEMA, units="metres", nodes=[node("axle", "axle_module", frame),
        node("hub", "hub", frame, "axle", np.eye(4), joint=joint)])
    hub = phase_geometry(scene, "body")["objects"][1]
    assert hub["joint_world"]["limits"] == [-.12, .24]
    assert hub["joint_parent"] == joint
    assert joint["limits"] == [-.03, .06]
