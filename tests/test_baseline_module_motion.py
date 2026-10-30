import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from car2lego3d.assembly_motion import posed_graph


scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("probe_baseline_module_motion", scripts / "probe_baseline_module_motion.py")
motion = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(motion)
finally:
    sys.path.remove(str(scripts))


def node(identity, parent, matrix, *, kind="module", part="box.dat", local=None):
    return dict(id=identity, parent=parent, kind=kind, part=part,
        world=np.asarray(matrix).tolist(), local=np.asarray(matrix if local is None else local).tolist())


def test_world_axis_converts_through_translated_rotated_parent():
    parent = np.eye(4)
    parent[:3, :3] = Rotation.from_euler("xyz", [20, 30, 40], degrees=True).as_matrix()
    parent[:3, 3] = [100, 200, -300]
    child = np.eye(4)
    child[:3, 3] = [10, 20, 30]
    graph = dict(instances=[node("root/p", "root", parent),
        node("root/p/m", "root/p", parent @ child, local=child)], joints={})
    origin = (parent @ np.array([1, 2, 3, 1]))[:3]
    axis = parent[:3, :3] @ [0, 0, 1]
    graph["joints"]["root/p/m"] = motion.parent_local_joint(graph, "root/p/m", origin, axis, [-60, 60])
    posed = posed_graph(graph, {"root/p/m": 30})
    rotation = Rotation.from_rotvec(axis * np.deg2rad(30)).as_matrix()
    expected = np.eye(4)
    expected[:3, :3] = rotation
    expected[:3, 3] = origin - rotation @ origin
    assert np.allclose(posed["instances"][1]["world"], expected @ parent @ child)


def test_all_port_contacts_between_same_parts_are_retained():
    edges = [dict(a="moving", b="fixed", port_a=str(n), port_b=str(n)) for n in range(2)]
    selected = motion.boundary_contacts([*edges, dict(a="moving", b="inside", port_a="0", port_b="0")],
        {"moving", "inside"})
    assert len(selected) == 2
    assert {row["port_a"] for row in selected.values()} == {"0", "1"}


class BoxCatalog:
    def boxes(self, part):
        if part == "missing.dat":
            raise ValueError("Missing collider")
        return [(np.zeros(3), np.eye(3), np.ones(3))]


def translation(x, y=0, z=0):
    result = np.eye(4)
    result[:3, 3] = [x, y, z]
    return result


def test_cross_boundary_colliders_keep_all_pairs_and_missing_evidence():
    nodes = [node("m/a", "m", translation(0), kind="part"),
        node("fixed-a", "root", translation(.5), kind="part"),
        node("fixed-b", "root", translation(-.5), kind="part"),
        node("unknown", "root", translation(100), kind="part", part="missing.dat")]
    checker = motion.CrossBoundaryCollisions(nodes, {"m/a"}, BoxCatalog())
    assert len(checker.inspect(nodes)) == 2
    assert checker.unknown == [dict(instance="unknown", part="missing.dat", issue="Missing collider")]
    nodes[0]["world"] = translation(1000).tolist()
    assert checker.inspect(nodes) == []


def test_broadphase_matches_exhaustive_native_sat_for_rotated_boxes():
    rng = np.random.default_rng(42)
    nodes = []
    for index in range(30):
        matrix = translation(*rng.uniform(-3, 3, 3))
        matrix[:3, :3] = Rotation.random(random_state=rng).as_matrix()
        nodes.append(node(str(index), "root", matrix, kind="part"))
    moving = {str(index) for index in range(10)}
    catalog = BoxCatalog()
    expected = set()
    for a in nodes[:10]:
        for b in nodes[10:]:
            if motion.colliders_intersect(motion.world_boxes(a, catalog.boxes(a["part"]))[0],
                    motion.world_boxes(b, catalog.boxes(b["part"]))[0]):
                expected.add(tuple(sorted((a["id"], b["id"]))))
    assert set(motion.CrossBoundaryCollisions(nodes, moving, catalog).inspect(nodes)) == expected


def probe_fixture():
    graph = dict(instances=[node("root/m", "root", np.eye(4)),
        node("root/m/part", "root/m", translation(2), kind="part"),
        node("fixed", "root", translation(0, 2), kind="part")], joints={})
    edge = dict(a="root/m/part", b="fixed", port_a="0", port_b="0")
    module = dict(instance="root/m", part_instances=["root/m/part"],
        boundary_connections=[edge], candidate_axes=[dict(connection=edge,
            connector_origin_ldu=[0, 0, 0], axis=[0, 0, 1])], missing_connection_evidence=[])
    return graph, module, edge


def test_lost_contacts_and_new_collisions_are_distinct_from_baseline_overlap(monkeypatch):
    graph, module, edge = probe_fixture()
    def matching(nodes, catalog):
        moving = next(row for row in nodes if row["id"] == "root/m/part")
        return ([edge] if np.asarray(moving["world"])[1, 3] <= 0 else []), []
    monkeypatch.setattr(motion, "connections", matching)
    result = motion.probe(graph, module, BoxCatalog(), refinement=0)
    assert result["summary"]["sampled_poses"] == 9
    assert result["summary"]["poses_losing_boundary_contacts"] == 4
    assert result["summary"]["poses_with_new_native_overlaps"] > 0
    assert not result["initial_native_overlap_pairs"]
    assert result["motion_acceptance"] == "unverified"
    assert graph["joints"] == {}


def test_preexisting_overlap_is_preserved_and_not_accepted(monkeypatch):
    graph, module, edge = probe_fixture()
    graph["instances"][-1]["local"] = translation(1).tolist()
    graph["instances"][-1]["world"] = translation(1).tolist()
    monkeypatch.setattr(motion, "connections", lambda nodes, catalog: ([edge], []))
    result = motion.probe(graph, module, BoxCatalog(), refinement=0)
    zero = next(row for row in result["poses"] if row["value_degrees"] == 0)
    assert result["initial_native_overlap_pairs"]
    assert zero["native_overlap_pairs"] and not zero["newly_overlapping_pairs"]
    assert zero["hypothesis_status"] == "unverified"


def test_invalid_or_conflicting_mounts_and_changed_membership_are_rejected():
    graph, module, _ = probe_fixture()
    with pytest.raises(ValueError, match="finite limits"):
        motion.probe(graph, module, BoxCatalog(), limits=[0, 90])
    module["part_instances"] = ["wrong"]
    with pytest.raises(ValueError, match="membership"):
        motion.probe(graph, module, BoxCatalog())


def test_original_boundary_must_be_reproducible(monkeypatch):
    graph, module, _ = probe_fixture()
    monkeypatch.setattr(motion, "connections", lambda nodes, catalog: ([], []))
    with pytest.raises(ValueError, match="zero degrees"):
        motion.probe(graph, module, BoxCatalog())
