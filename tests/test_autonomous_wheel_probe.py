from copy import deepcopy
import os
from pathlib import Path

import pytest

from car2lego3d.assembly_tools import AssemblyTools
from scripts.probe_autonomous_wheel import selection_evidence, wheel_evidence
from scripts.probe_connector_module import demonstration_operations


def search(query="wheel rim", *, ok=True):
    return dict(tool="search_parts", arguments=dict(query=query), ok=ok,
        result=dict(parts=[dict(part="12345.dat", description="Wheel Rim")]))


def query():
    return dict(tool="query_part", arguments=dict(part="12345.dat"), ok=True,
        result=dict(part="12345.dat", status="pass"))


def add():
    return dict(tool="build", ok=True, arguments=dict(operations=[dict(op="add", part="12345.dat")]))


def test_selection_requires_prior_description_search_then_verified_query():
    evidence = selection_evidence([search(), query(), add()], {"12345.dat"})
    assert evidence["status"] == "pass"
    assert evidence["parts"]["12345.dat"]["search"] == dict(step=0, query="wheel rim")


@pytest.mark.parametrize("trace", [
    [add(), search(), query()], [query(), search(), add()],
    [search("12345"), query(), add()], [search("12345.dat wheel"), query(), add()],
    [search(ok=False), query(), add()], [query(), add()],
])
def test_late_id_only_failed_or_absent_search_cannot_prove_selection(trace):
    assert selection_evidence(trace, {"12345.dat"})["status"] == "fail"


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    tools = AssemblyTools(tmp_path_factory.mktemp("selected-wheel"), native)
    operations = demonstration_operations(rolling=True)
    seeded = tools.call("build", dict(revision=0, operations=operations[:1]))
    assert seeded["ok"], seeded
    support = next(node for node in tools.assembly.graph()["instances"] if node["kind"] == "part")
    descriptions = {}
    for operation in operations[1:]:
        if "part" in operation:
            name = operation["part"]
            with tools.catalog.library.resolve(name).open(encoding="utf-8-sig") as stream:
                descriptions[name] = stream.readline().strip().removeprefix("0 ")
        if operation.get("target") == "@bearing":
            operation["target"] = support["id"]
    # Bound only the fixture's search inventory; native geometry and contacts are real.
    tools.catalog.descriptions = lambda: descriptions
    for role, name in zip(("wheel rim", "tyre", "axle"), descriptions):
        assert tools.call("search_parts", dict(query=role))["ok"]
        assert tools.call("query_part", dict(part=name))["result"]["status"] == "pass"
    built = tools.call("build", dict(revision=1, operations=operations[1:]))
    assert built["ok"], built
    assert tools.call("check", {})["result"]["status"] == "pass"
    assert tools.call("motion", dict(samples=9))["result"]["status"] == "pass"
    return tools, support


def test_native_roles_contacts_and_actual_geometry_motion_qualify(wheel):
    tools, support = wheel
    evidence = wheel_evidence(tools, support)
    assert evidence["status"] == "pass", evidence
    assert evidence["poses"] == 9
    assert all(len(values) == 1 for values in evidence["roles"].values())
    assert min(evidence["maximum_vertex_displacement_ldu"].values()) > 1
    assert evidence["selection"]["status"] == "pass"


def test_role_labels_cannot_replace_real_geometry_motion(wheel, monkeypatch):
    from scripts import probe_autonomous_wheel
    tools, support = wheel
    original = tools.assembly.graph()
    monkeypatch.setattr(probe_autonomous_wheel, "posed_graph", lambda graph, positions: deepcopy(original))
    assert "actual_wheel_geometry_did_not_rotate" in wheel_evidence(tools, support)["issues"]


def test_support_change_invalidates_acceptance(wheel, monkeypatch):
    tools, support = wheel
    graph = deepcopy(tools.assembly.graph())
    next(node for node in graph["instances"] if node["id"] == support["id"])["color"] = 4
    monkeypatch.setattr(tools.assembly, "graph", lambda: graph)
    evidence = wheel_evidence(tools, support)
    assert "fixed_support_changed" in evidence["issues"]
    assert "check_missing_failed_or_stale" in evidence["issues"]
