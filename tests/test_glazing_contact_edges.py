import ast

import pytest

from car2lego3d.common import ROOT


@pytest.fixture
def glazing_contact_edges():
    source = ast.parse((ROOT / "scripts/blender_export_render.py").read_text(encoding="utf-8"))
    helper = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                  and node.name == "glazing_contact_edges")
    namespace = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "blender_export_render.py", "exec"), namespace)
    return namespace["glazing_contact_edges"]


@pytest.fixture
def raked_glass():
    left = [(t * .8, -1 + t * .3, .8 + t * .5) for t in (0, .1, .9, 1)]
    right = [(x, -y, z) for x, y, z in left]
    lower = [(0, y, .8) for y in (-1, -.5, 0, .5, 1)]
    upper = [(.8, y, 1.3) for y in (-.7, -.35, 0, .35, .7)]
    boundary = [*zip(left, left[1:]), *zip(right, right[1:]),
                *zip(lower, lower[1:]), *zip(upper, upper[1:])]
    return left + right + lower + upper, boundary, lower, upper


@pytest.mark.parametrize("side", ["lower", "upper"])
def test_raked_pillar_edges_do_not_become_cowl_or_header_contact(glazing_contact_edges, raked_glass, side):
    vertices, boundary, lower, upper = raked_glass
    selected, coverage = glazing_contact_edges(vertices, boundary, side)
    rim = lower if side == "lower" else upper
    assert selected == list(zip(rim, rim[1:]))
    assert .95 <= coverage <= 1


def test_missing_middle_span_remains_detectable(glazing_contact_edges, raked_glass):
    vertices, boundary, lower, _ = raked_glass
    boundary.remove((lower[1], lower[2]))
    _, coverage = glazing_contact_edges(vertices, boundary, "lower")
    assert coverage == pytest.approx(.75)


def test_overlapping_edge_intervals_do_not_hide_missing_support(glazing_contact_edges, raked_glass):
    vertices, _, lower, _ = raked_glass
    edges = [(lower[0], lower[2]), (lower[0], lower[1])]
    _, coverage = glazing_contact_edges(vertices, edges, "lower")
    assert coverage == pytest.approx(.5)


def test_complete_open_surface_boundary_excludes_shared_internal_edges():
    source = ast.parse((ROOT / "scripts/blender_export_render.py").read_text(encoding="utf-8"))
    helper = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "open_boundary_edges")
    namespace = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "blender_export_render.py", "exec"), namespace)
    edges = namespace["open_boundary_edges"]([[0, 1, 2], [0, 2, 3]])
    assert set(edges) == {(0, 1), (1, 2), (2, 3), (0, 3)}
