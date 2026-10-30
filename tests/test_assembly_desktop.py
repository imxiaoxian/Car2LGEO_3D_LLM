import numpy as np

from car2lego3d.assembly_desktop import preview_hierarchy


def test_preview_preserves_nested_motion_and_converts_parent_joint_frame():
    parent = np.eye(4)
    parent[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    parent[:3, 3] = [100, 200, 300]
    graph = dict(instances=[
        dict(id="root/cabin", parent="root", kind="module", world=parent.tolist()),
        dict(id="root/cabin/door", parent="root/cabin", kind="module"),
        dict(id="root/cabin/door/glass", parent="root/cabin/door", kind="part")],
        joints={"root/cabin/door": dict(type="revolute", axis=[1, 0, 0], origin=[10, 0, 0], limits=[0, 70])})
    modules, joints = preview_hierarchy(graph)
    assert modules[-1]["parent"] == "root/cabin"
    assert joints[0]["child"] == "root/cabin/door"
    assert joints[0]["axis"] == [0, 1, 0]
    assert joints[0]["pivot"] == [100, 210, 300]
    assert joints[0]["status"] == "unverified"
