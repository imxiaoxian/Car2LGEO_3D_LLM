import ast
import math
import operator

import pytest

from car2lego3d.common import ROOT


@pytest.fixture
def validate_mesh_data():
    source = ast.parse((ROOT / "scripts/blender_mesh_guard.py").read_text(encoding="utf-8"))
    helper = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                  and node.name == "validate_mesh_data")
    namespace = {"math": math, "operator": operator}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "blender_mesh_guard.py", "exec"), namespace)
    return namespace["validate_mesh_data"]


def test_valid_procedural_geometry_is_preserved(validate_mesh_data):
    vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    assert validate_mesh_data("panel", iter(vertices), [(0, 1)], [(0, 1, 2)]) == (
        vertices, [(0, 1)], [(0, 1, 2)])


@pytest.mark.parametrize("vertices,edges,faces,message", [
    ([(0, 0)], [], [], "vertex 0 requires three finite"),
    ([(0, 0, float('nan'))], [], [], "vertex 0 requires three finite"),
    ([(0, 0, 0)], [(0, 1, 2)], [], "edge 0 has invalid corner count"),
    ([(0, 0, 0)], [], [(0, 1)], "face 0 has invalid corner count"),
    ([(0, 0, 0)] * 3, [], [(0, 1, 3)], "face 0 indices.*outside 3 vertices"),
    ([(0, 0, 0)] * 3, [], [(0, 1, -1)], "face 0 indices.*outside 3 vertices"),
    ([(0, 0, 0)] * 3, [], [(0, 1, 1)], "face 0 repeats a vertex index"),
    ([(0, 0, 0)] * 3, [(0, 1.0)], [], "edge 0 requires integer vertex indices"),
])
def test_invalid_topology_reports_mesh_and_element(validate_mesh_data, vertices, edges, faces, message):
    with pytest.raises(ValueError, match="Mesh faulty_panel: " + message):
        validate_mesh_data("faulty_panel", vertices, edges, faces)
