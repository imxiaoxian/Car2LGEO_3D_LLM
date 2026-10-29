import importlib.util
from pathlib import Path

import numpy as np
import pytest
import trimesh


spec = importlib.util.spec_from_file_location("render_baseline_articulation",
    Path(__file__).resolve().parents[1] / "scripts/render_baseline_articulation.py")
presentation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(presentation)


def source():
    world = np.eye(4)
    world[:3, 3] = [200, -40, 80]
    graph = dict(instances=[dict(id="root/door/panel", kind="part", world=world.tolist())])
    scene = trimesh.Scene()
    matrix = np.diag([.0004, -.0004, -.0004, 1]) @ world
    scene.add_geometry(trimesh.creation.box(), node_name="root/door/panel:16", transform=matrix)
    return scene, graph


def test_glb_review_keeps_all_part_placements_and_multiple_colours():
    scene, graph = source()
    scene.add_geometry(trimesh.creation.box(), node_name="root/door/panel:4",
        transform=scene.graph["root/door/panel:16"][0])
    assert presentation.verify_scene(scene, graph) == 1


def test_glb_review_rejects_missing_parts_or_changed_placements():
    scene, graph = source()
    graph["instances"][0]["world"][0][3] += 20
    with pytest.raises(ValueError, match="placement differs"):
        presentation.verify_scene(scene, graph)
    scene, graph = source()
    graph["instances"].append(dict(id="root/missing", kind="part", world=np.eye(4).tolist()))
    with pytest.raises(ValueError, match="every audited"):
        presentation.verify_scene(scene, graph)


def test_glb_review_rejects_unknown_instances():
    scene, graph = source()
    scene.add_geometry(trimesh.creation.box(), node_name="root/unexpected:16")
    with pytest.raises(ValueError, match="Unexpected GLB"):
        presentation.verify_scene(scene, graph)
