import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_catalog import (NativeCatalog, connections, intersects, shaft_diagnostics,
                                       shaft_match, shaft_snap_origin, validate)
from car2lego3d.native_ports import Port


def model(part="3001.dat", y=-24, x=0):
    return Assembly(f"1 4 0 0 0 1 0 0 0 1 0 0 0 1 {part}\n1 1 {x} {y} 0 1 0 0 0 1 0 0 0 1 {part}\n").graph()


class FixtureCatalog:
    def ports(self, part):
        return tuple(Port(str(i), kind, 0, (x, y, 0), (0, 1, 0), altitude=10, area=4)
                     for i, (kind, x, y) in enumerate((("stud", 0, 0), ("stud", 20, 0),
                                                      ("hole", 0, 24), ("hole", 20, 24))))


def test_all_contacts_and_wrong_alignment():
    edges, unknown = connections(model()["instances"], FixtureCatalog())
    assert len(edges) == 2 and not unknown
    assert len({(e["port_a"], e["port_b"]) for e in edges}) == 2
    assert connections(model(x=3)["instances"], FixtureCatalog())[0] == []


def test_obb_touching_rotation_and_penetration():
    a = (np.zeros(3), np.eye(3), np.ones(3))
    assert not intersects(a, (np.array([2, 0, 0]), np.eye(3), np.ones(3)))
    assert intersects(a, (np.array([1, 0, 0]), np.eye(3), np.ones(3)))
    r = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    assert not intersects(a, (np.array([5, 0, 0]), r, np.ones(3)))


@pytest.fixture
def native():
    path = Path(os.environ.get("LDRAW_LIBRARY_PATH", r"D:\lego\Studio 2.0\ldraw"))
    if not path.exists():
        pytest.skip("Native catalog not installed")
    return NativeCatalog(path)


def test_real_native_stacking_and_missing_evidence(native):
    report = validate(model(), native)
    assert report["status"] == "pass", report
    assert len(report["connections"]) >= 8
    assert validate(model(y=0), native)["status"] == "fail"
    assert validate(model(part="missing-part.dat"), native)["status"] == "unverified"
    assert report["gates"]["physical"] == "unverified"


def test_identity_alias_uses_canonical_evidence_with_provenance_and_direct_precedence(tmp_path):
    for folder in ('parts', 'collider'):
        (tmp_path / folder).mkdir()
    alias = tmp_path / 'parts/alias.dat'
    alias.write_text('0 !LDRAW_ORG Part Alias\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 canonical.dat\n')
    (tmp_path / 'parts/canonical.dat').write_text('3 16 0 0 0 1 0 0 0 1 0\n')
    canonical = tmp_path / 'collider/canonical.col'
    canonical.write_bytes(b'canonical-test-evidence')
    catalog = NativeCatalog(tmp_path)
    assert catalog.evidence('alias.dat', 'collider', '.col') == canonical
    assert str(alias.resolve()) in catalog.library.used
    direct = tmp_path / 'collider/alias.col'
    direct.write_bytes(b'direct-test-evidence')
    assert catalog.evidence('alias.dat', 'collider', '.col') == direct
    direct.unlink()
    alias.write_text('0 !LDRAW_ORG Part Alias\n1 16 1 0 0 1 0 0 0 1 0 0 0 1 canonical.dat\n')
    with pytest.raises(ValueError, match='Missing collider'):
        catalog.evidence('alias.dat', 'collider', '.col')


def test_alias_evidence_cycle_cannot_supply_missing_data(tmp_path):
    (tmp_path / 'parts').mkdir()
    for a, b in (('a', 'b'), ('b', 'a')):
        (tmp_path / f'parts/{a}.dat').write_text(f'0 !LDRAW_ORG Part Alias\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 {b}.dat\n')
    with pytest.raises(ValueError, match='Cyclic native evidence alias'):
        NativeCatalog(tmp_path).evidence('a.dat', 'collider', '.col')


def test_real_glass_alias_keeps_native_geometry_and_uses_exact_canonical_collider(native):
    record = native.part('60603.dat')
    assert record['status'] == 'pass', record
    assert Path(record['evidence_sources']['collider']).name == '86210.col'
    alias, _ = native.library.triangles('60603.dat')
    canonical, _ = native.library.triangles('86210.dat')
    np.testing.assert_array_equal(alias, canonical)


def test_existing_part_with_missing_surface_dependency_cannot_pass_geometry_gate():
    class Library:
        def resolve(self, _):
            return Path("exists.dat")

        def triangles(self, _):
            raise FileNotFoundError("Missing LDraw geometry: nested-surface.dat")

    class Catalog(FixtureCatalog):
        library = Library()

        def boxes(self, _):
            return [(np.zeros(3), np.eye(3), np.ones(3))]

    report = validate(model(), Catalog())
    assert report["gates"]["parts"] == "unverified"
    assert report["gates"]["collisions"] == "unverified"
    assert report["coverage"]["geometry"] == 0
    assert all("nested-surface.dat" in item["issue"] for item in report["unknown"])


def test_disconnected_shaft_reports_actual_gap_and_preserves_failure():
    class Catalog:
        def ports(self, part):
            return (Port("0", "axle", 0, (0, 0, 0), (0, 0, 1), length=20,
                         semantic="axle" if part == "shaft.dat" else "axle_socket", cross_type=True),)

    graph = Assembly("1 0 0 0 0 1 0 0 0 1 0 0 0 1 shaft.dat\n"
                     "1 0 0 0 60 1 0 0 0 1 0 0 0 1 rim.dat\n").graph()
    ids = [node["id"] for node in graph["instances"]]
    report = shaft_diagnostics(graph["instances"], Catalog(), [[ids[0]], [ids[1]]])
    gap = report["candidates"][0]
    assert gap["axial_gap_ldu"] == 40
    assert gap["engagement_ldu"] == 0 and gap["lateral_error_ldu"] == 0
    assert gap["axis_angle_degrees"] == 0 and not gap["verified_contact"]
    assert report["status"] == "diagnostic_only" and not report["exhaustive"]
    assert connections(graph["instances"], Catalog())[0] == []
    assert shaft_diagnostics(graph["instances"], Catalog(), [ids])["candidates"] == []


def test_shaft_diagnostics_does_not_suggest_incompatible_or_far_contacts():
    class Catalog:
        def ports(self, part):
            return (Port("0", "axle", 0, (0, 0, 0), (0, 0, 1), length=20,
                         semantic="axle_socket"),)

    graph = model(part="rim.dat", y=0, x=100)
    parts = graph["instances"]
    assert shaft_diagnostics(parts, Catalog(), [[p["id"]] for p in parts])["candidates"] == []


@pytest.mark.parametrize("sign", [1, -1])
def test_shaft_seating_respects_blind_socket_stop_and_preserves_facing(sign):
    male = Port("0", "axle", 7, (0, 0, 0), (0, 0, sign), length=40,
                semantic="axle", start_capped=True, cross_type=True)
    socket = Port("0", "axle", 6, (0, 0, 0), (0, 0, 1), length=20,
                  semantic="axle_socket", end_capped=True, cross_type=True)
    a = ("shaft", male, np.array([4., 0, 70]), np.array(male.direction), np.array([1., 0, 0]))
    b = ("socket", socket, np.zeros(3), np.array(socket.direction), np.array([1., 0, 0]))
    if sign == 1:
        assert not shaft_match((a[0], male, b[2], a[3], a[4]), b)
    origin = shaft_snap_origin(a, b)
    assert shaft_match((a[0], male, origin, a[3], a[4]), b)
    np.testing.assert_allclose(origin, [0, 0, 20 if sign == 1 else -20])


def test_shaft_seating_cannot_override_caps_or_keyed_orientation():
    male = Port("0", "axle", 7, (0, 0, 0), (0, 0, 1), length=40,
                semantic="axle", start_capped=True, end_capped=True, cross_type=True)
    socket = Port("0", "axle", 6, (0, 0, 0), (0, 0, 1), length=20,
                  semantic="axle_socket", start_capped=True, end_capped=True, cross_type=True)
    a = ("shaft", male, np.zeros(3), np.array(male.direction), np.array([1., 0, 0]))
    b = ("socket", socket, np.zeros(3), np.array(socket.direction), np.array([1., 0, 0]))
    with pytest.raises(ValueError, match="No legal shaft seating"):
        shaft_snap_origin(a, b)
    from dataclasses import replace
    a = (a[0], replace(male, start_capped=False, end_capped=False), *a[2:])
    b = (b[0], replace(socket, start_capped=False, end_capped=False), *b[2:4], np.array([1., 1, 0]) / np.sqrt(2))
    with pytest.raises(ValueError, match="keyed orientation"):
        shaft_snap_origin(a, b)


def test_seating_suggestion_moves_connected_rim_tyre_and_keeps_nested_hub_fixed():
    class Catalog:
        def ports(self, part):
            if part == "tyre.dat":
                return ()
            return (Port("0", "axle", 0, (0, 0, 0), (0, 0, 1), length=20,
                semantic="axle" if part == "shaft.dat" else "axle_socket", cross_type=True),)

    source = Assembly("0 FILE main.ldr\n1 0 100 0 0 0 -1 0 1 0 0 0 0 1 group.ldr\n"
        "0 FILE group.ldr\n1 0 0 0 0 1 0 0 0 1 0 0 0 1 shaft.dat\n"
        "1 0 0 0 60 1 0 0 0 1 0 0 0 1 rim.dat\n"
        "1 0 0 0 60 1 0 0 0 1 0 0 0 1 tyre.dat\n")
    nodes = [n for n in source.graph()["instances"] if n["kind"] == "part"]
    hub, rim, tyre = [n["id"] for n in nodes]
    report = shaft_diagnostics(source.graph()["instances"], Catalog(), [[hub], [rim, tyre]])
    option = next(row for row in report["candidates"][0]["seating_options"] if row["source"] == rim)
    assert {op["instance"] for op in option["operations"]} == {rim, tyre}
    hub_before = next(n for n in nodes if n["id"] == hub)["world"]
    source.transact(0, option["operations"])
    moved = {n["id"]: n for n in source.graph()["instances"]}
    np.testing.assert_allclose(moved[rim]["world"], moved[tyre]["world"])
    np.testing.assert_allclose(moved[hub]["world"], hub_before)
    assert len(connections([n for n in moved.values() if n["kind"] == "part"], Catalog())[0]) == 1
    assert option["status"] == "proposal_requires_collision_and_motion_checks"
