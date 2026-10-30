"""Reusable subassemblies keep internal motion without copying external mounts."""
from copy import deepcopy
import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_graph import Assembly, IOArchive
from car2lego3d.assembly_motion import posed_graph
from car2lego3d.assembly_mounts import semantic_node
from car2lego3d.assembly_tools import AssemblyTools
from scripts.probe_rolling_chassis import chassis_evidence, demonstration_operations


MODEL = """0 FILE main.ldr
1 4 100 0 0 1 0 0 0 1 0 0 0 1 assembly.ldr
1 1 -100 0 0 0 0 1 0 1 0 -1 0 0 other.ldr
0 FILE assembly.ldr
1 16 0 0 0 1 0 0 0 1 0 0 0 1 moving.ldr
0 FILE moving.ldr
1 16 10 0 0 1 0 0 0 1 0 0 0 1 3001.dat
0 FILE other.ldr
"""


def test_clone_preserves_internal_frames_colours_and_independent_edits():
    assembly = Assembly(MODEL)
    source, moving, leaf, parent = [node["id"] for node in assembly.graph()["instances"]]
    assembly.transact(0, [dict(op="joint", instance=source, axis=[0, 1, 0], limits=[0, 90]),
        dict(op="joint", instance=moving, axis=[0, 0, 1], origin=[3, 2, 1], limits=[0, 180])])
    original = deepcopy(assembly.graph())
    matrix = np.eye(4)
    matrix[:3, 3] = [20, -50, 70]
    result = assembly.transact(1, [dict(op="clone", source=source, parent=parent,
        matrix=matrix.tolist(), alias="copy")])
    copied = result["clones"][0]
    assert copied["root_joint_omitted"] and copied["joints_copied"] == 1
    mapping = copied["instances"]
    assert mapping[source] == result["aliases"]["copy"]
    assert mapping[source] not in assembly.joints
    assert assembly.joints[mapping[moving]]["instance"] == mapping[moving]
    for angle in (0, 45, 180):
        nodes = {node["id"]: node for node in posed_graph(assembly.graph(),
            {moving: angle, mapping[moving]: angle})["instances"]}
        source_local = np.linalg.inv(nodes[source]["world"]) @ nodes[leaf]["world"]
        copied_local = np.linalg.inv(nodes[mapping[source]]["world"]) @ nodes[mapping[leaf]]["world"]
        np.testing.assert_allclose(source_local, copied_local, atol=1e-8)
        assert nodes[mapping[leaf]]["color"] == nodes[leaf]["color"] == 4
    assembly.transact(2, [dict(op="color", instance=mapping[leaf], color=14)])
    nodes = {node["id"]: node for node in assembly.graph()["instances"]}
    assert nodes[mapping[leaf]]["color"] == 14 and nodes[leaf]["color"] == 4
    assert [semantic_node(nodes[node["id"]]) for node in original["instances"]] == [
        semantic_node(node) for node in original["instances"]]


@pytest.mark.parametrize("fault", ["unknown", "inside", "cycle", "scaled", "stale", "late_failure"])
def test_clone_failures_are_atomic(fault):
    assembly = Assembly(MODEL)
    source, moving = [node["id"] for node in assembly.graph()["instances"][:2]]
    op = dict(op="clone", source=source, alias="copy", matrix=np.eye(4).tolist())
    if fault == "unknown":
        op["source"] = "root/unknown"
    elif fault == "inside":
        op["parent"] = moving
    elif fault == "cycle":
        op["parent"] = source
    elif fault == "scaled":
        op["matrix"][0][0] = 2
    operations = [op]
    if fault == "late_failure":
        operations.append(dict(op="remove", instance="root/unknown"))
    before = deepcopy((assembly.sections, assembly.graph(), assembly.text()))
    with pytest.raises(ValueError):
        assembly.transact(1 if fault == "stale" else 0, operations)
    assert (assembly.sections, assembly.graph(), assembly.text()) == before


def test_clone_aliases_can_be_used_within_one_transaction():
    assembly = Assembly(MODEL)
    source = assembly.graph()["instances"][0]["id"]
    result = assembly.transact(0, [dict(op="clone", source=source, alias="first", matrix=np.eye(4).tolist()),
        dict(op="clone", source="@first", alias="second", matrix=np.eye(4).tolist())])
    assert len(result["clones"]) == 2
    assert result["clones"][1]["source"] == result["aliases"]["first"]
    assert Assembly(assembly.text()).bom() == assembly.bom()


def test_leaf_clone_uses_independent_identity_and_effective_colour():
    assembly = Assembly(MODEL)
    leaf = next(node for node in assembly.graph()["instances"] if node["kind"] == "part")
    result = assembly.transact(0, [dict(op="clone", source=leaf["id"], matrix=np.eye(4).tolist(), alias="leaf")])
    copied = result["clones"][0]
    assert copied["instances"] == {leaf["id"]: result["aliases"]["leaf"]}
    assert not copied["root_joint_omitted"] and copied["joints_copied"] == 0
    assembly.transact(1, [dict(op="color", instance=copied["instance"], color=14)])
    nodes = {node["id"]: node for node in assembly.graph()["instances"]}
    assert nodes[copied["instance"]]["color"] == 14
    assert semantic_node(nodes[leaf["id"]]) == semantic_node(leaf)


def test_protected_original_can_be_cloned_but_not_changed(tmp_path):
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    tools.assembly = Assembly(MODEL)
    original = deepcopy(tools.assembly.graph())
    source = original["instances"][0]["id"]
    leaf = next(node["id"] for node in original["instances"] if node["kind"] == "part")
    tools.protected_state = dict(instances={node["id"]: node for node in original["instances"]},
        joints={}, bindings={}, mounts=[])
    result = tools.call("build", dict(revision=0, strict=False,
        operations=[dict(op="clone", source=source, matrix=np.eye(4).tolist(), alias="copy")]))
    assert result["ok"], result
    cloned_leaf = result["result"]["clones"][0]["instances"][leaf]
    result = tools.call("edit", dict(revision=1, operations=[dict(op="color", instance=cloned_leaf, color=14)]))
    assert result["ok"], result
    graph, checkpoint = deepcopy(tools.assembly.graph()), (tmp_path / "state.json").read_bytes()
    result = tools.call("build", dict(revision=2, strict=False, operations=[
        dict(op="clone", source=source, matrix=np.eye(4).tolist()), dict(op="color", instance=leaf, color=2)]))
    assert not result["ok"] and "Completed module changed" in result["error"]
    assert tools.assembly.graph() == graph and (tmp_path / "state.json").read_bytes() == checkpoint


def test_native_cloned_wheels_mount_and_roll_independently(tmp_path, monkeypatch):
    native = Path(os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalogue not installed")
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", "off")
    tools = AssemblyTools(tmp_path, native)
    operations = demonstration_operations()
    bearings = [index for index, op in enumerate(operations) if op.get("alias", "").endswith("bearing")]
    first = operations[:bearings[1]]
    result = tools.call("build", dict(revision=0, operations=first))
    assert result["ok"], result
    original = deepcopy(tools.assembly.graph())
    ids = result["result"]["aliases"]
    source = ids["fl"]
    for offset in bearings[1:]:
        bearing, module = deepcopy(operations[offset:offset + 2])
        name = module["alias"]
        bearing["parent"], bearing["target"] = ids["chassis"], ids["deck"]
        trial = [bearing, dict(op="clone", source=source, alias=name, matrix=module["matrix"])]
        # Resolve clone descendant IDs after the draft clone, then seat and articulate.
        result = tools.call("build", dict(revision=tools.assembly.revision, operations=trial, strict=False))
        assert result["ok"], result
        copy = result["result"]["clones"][0]
        assert copy["root_joint_omitted"] and copy["joints_copied"] == 0
        axle = copy["instances"][ids["flaxle"]]
        bearing_id = result["result"]["aliases"][name + "bearing"]
        args = dict(source=axle, source_port="0", target=bearing_id, target_port="2")
        installed = tools.call("build", dict(revision=tools.assembly.revision, operations=[
            dict(op="snap", moving=copy["instance"], **args),
            dict(op="articulate", instance=copy["instance"], limits=[0, 360], purpose="rolling", **args)]))
        assert installed["ok"], installed
    nodes = {node["id"]: node for node in tools.assembly.graph()["instances"]}
    assert [semantic_node(nodes[node["id"]]) for node in original["instances"]] == [
        semantic_node(node) for node in original["instances"]]
    assert tools.call("check", {})["result"]["status"] == "pass"
    assert tools.call("motion", dict(samples=9))["result"]["status"] == "pass"
    assert chassis_evidence(tools)["status"] == "pass"
    assert tools.call("export", {})["ok"]
    assert IOArchive(tools.directory / "model.io").assembly.joints == tools.assembly.joints
    assert AssemblyTools(tools.directory, native).assembly.graph() == tools.assembly.graph()
    before = deepcopy(tools.assembly.graph())
    checkpoint = (tools.directory / "state.json").read_bytes()
    failed = tools.call("build", dict(revision=tools.assembly.revision, operations=[dict(op="clone",
        source=source, matrix=nodes[source]["local"], alias="overlap")]))
    assert not failed["ok"] and "collisions" in failed["error"]
    assert tools.assembly.graph() == before and (tools.directory / "state.json").read_bytes() == checkpoint
