import importlib.util
from pathlib import Path

import numpy as np

from car2lego3d.assembly_graph import Assembly
from car2lego3d.native_ports import Port


spec = importlib.util.spec_from_file_location("audit_baseline_articulation",
    Path(__file__).resolve().parents[1] / "scripts/audit_baseline_articulation.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_shared_nested_modules_keep_occurrence_specific_boundaries_and_unknowns():
    assembly = Assembly("""0 FILE main.ldr
1 4 0 0 0 1 0 0 0 1 0 0 0 1 door.ldr
1 1 100 0 0 1 0 0 0 1 0 0 0 1 door.ldr
1 0 0 0 0 1 0 0 0 1 0 0 0 1 mount.dat
0 FILE door.ldr
1 16 0 0 0 1 0 0 0 1 0 0 0 1 inner.ldr
0 FILE inner.ldr
1 16 0 0 0 1 0 0 0 1 0 0 0 1 panel.dat
1 16 0 10 0 1 0 0 0 1 0 0 0 1 frame.dat
""")
    graph = assembly.graph()
    parts = [n for n in graph["instances"] if n["kind"] == "part"]
    left, frame, right, _, mount = [n["id"] for n in parts]
    contacts = [dict(a=left, b=mount, port_a="0", port_b="0", kind="fixed:fixed"),
        dict(a=left, b=frame, port_a="1", port_b="0", kind="fixed:fixed"),
        dict(a=left, b=mount, port_a="2", port_b="1", kind="fixed:fixed")]
    fixed = lambda number: Port(number, "fixed", 0, (0, 0, 0), (0, 1, 0))
    ports = [(identity, fixed(number), np.zeros(3), np.array([0, 1, 0]), np.array([0, 0, 1]))
        for identity, number in ((left, "0"), (left, "1"), (left, "2"), (frame, "0"), (mount, "0"), (mount, "1"))]
    unknown = [dict(instance=right, issue="missing_connectivity")]
    rows = audit.module_interfaces(graph, contacts, unknown, ports, {})
    left_rows = [r for r in rows if left in r["part_instances"]]
    right_rows = [r for r in rows if right in r["part_instances"]]
    assert len(left_rows) == len(right_rows) == 2
    assert all(len(r["boundary_connections"]) == 2 for r in left_rows)
    assert all(not r["missing_connection_evidence"] for r in left_rows)
    assert all(r["missing_connection_evidence"] == unknown for r in right_rows)
    assert all(not r["boundary_connections"] for r in right_rows)
    assert all(r["articulation"] == "unverified" and r["motion"] == "not_checked" for r in rows)


def test_native_hinge_and_round_shaft_are_only_hints_keyed_axles_are_excluded():
    edge = dict(a="a", b="b", port_a="0", port_b="0", kind="axle:axle")
    def hints(kind, semantic_a, semantic_b, cross_a=False, cross_b=False):
        ports = {}
        for name, semantic, cross in (("a", semantic_a, cross_a), ("b", semantic_b, cross_b)):
            port = Port("0", kind, 0, (0, 0, 0), (0, 1, 0), semantic=semantic, cross_type=cross)
            ports[(name, "0")] = (name, port, np.zeros(3), np.array([0, 1, 0]), np.array([0, 0, 1]))
        return audit.axis_hint(edge, ports)
    assert hints("axle", "axle", "axle_socket", True, True) is None
    round_hint = hints("axle", "axle", "round_hole", True, False)
    assert round_hint["basis"] == "matched_shaft_with_unkeyed_member"
    assert round_hint["status"] == "unverified" and round_hint["limits_degrees"] is None
    hinge_hint = hints("hinge", "hinge", "hinge")
    assert hinge_hint["basis"] == "matched_native_hinge"
    assert hints("fixed", "fixed", "fixed") is None


def test_bounds_respect_nested_rotation_translation_and_reflection():
    node = dict(world=[[0, 0, -1, 100], [0, 1, 0, 20], [-1, 0, 0, 30], [0, 0, 0, 1]])
    low, high = audit.world_bounds(node, [[-2, -3, -4], [5, 6, 7]])
    assert np.allclose(low, [93, 17, 25])
    assert np.allclose(high, [104, 26, 32])
