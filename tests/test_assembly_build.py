"""Native connector programs are atomic, nested, and independent of object category."""
from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_catalog import compatible
from car2lego3d.assembly_tools import AssemblyTools


@pytest.fixture
def scene(tmp_path, monkeypatch):
    native = Path(os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalogue not installed")
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", "off")
    tools = AssemblyTools(tmp_path, native)
    ports = tools.catalog.ports("3001.dat")
    hole, stud = next((h, s) for h in ports for s in ports if h.kind == "hole" and s.kind == "stud"
        and compatible(h, s) and np.allclose(np.asarray(h.position)[[0, 2]], np.asarray(s.position)[[0, 2]]))
    operations = [dict(op="module", name="structure.ldr", alias="group", matrix=np.eye(4).tolist()),
        dict(op="add", part="3001.dat", color=4, alias="base", parent="@group", matrix=np.eye(4).tolist()),
        dict(op="attach", part="3001.dat", color=14, alias="middle", parent="@group",
            source_port=hole.id, target="@base", target_port=stud.id),
        dict(op="attach", part="3001.dat", color=1, alias="top", parent="@group",
            source_port=hole.id, target="@middle", target_port=stud.id)]
    return tools, operations


def test_connector_program_publishes_one_revision_and_preserves_native_layout(scene):
    tools, operations = scene
    result = tools.call("build", dict(revision=0, operations=operations))
    assert result["ok"], result
    assert result["revision_before"] == 0 and result["revision_after"] == 1
    assert result["result"]["native_status"] == "pass"
    aliases = result["result"]["aliases"]
    nodes = {n["id"]: n for n in tools.assembly.graph()["instances"]}
    for name, y in (("base", 0), ("middle", -24), ("top", -48)):
        node = nodes[aliases[name]]
        assert node["parent"] == aliases["group"]
        np.testing.assert_allclose(np.asarray(node["world"])[:3, 3], [0, y, 0])
    assert tools.call("check", {})["result"]["status"] == "pass"
    restored = AssemblyTools(tools.directory, tools.catalog_root)
    assert restored.assembly.graph() == tools.assembly.graph()
    records = [json.loads(row) for row in (tools.directory / "trajectory.jsonl").read_text().splitlines()]
    assert [row["tool"] for row in records] == ["build", "check"]


@pytest.mark.parametrize("fault", ["collision", "port", "alias", "duplicate", "missing", "nonrigid", "stale", "protected"])
def test_program_failure_restores_entire_graph_and_checkpoint(scene, fault, monkeypatch):
    tools, operations = scene
    assert tools.call("build", dict(revision=0, operations=operations))["ok"]
    before = (deepcopy(tools.assembly.graph()), (tools.directory / "state.json").read_bytes())
    trial = deepcopy(operations)
    trial[0]["name"] = "another.ldr"
    if fault == "collision":
        trial[-1]["target"] = "@base"
    elif fault == "port":
        trial[-1]["source_port"] = "invented"
    elif fault == "alias":
        trial[-1]["target"] = "@undefined"
    elif fault == "duplicate":
        trial[-1]["alias"] = "base"
    elif fault == "missing":
        trial[-1]["part"] = "no-such-part.dat"
    elif fault == "nonrigid":
        trial[-1]["matrix"] = (np.eye(4) * 2).tolist()
    elif fault == "protected":
        from car2lego3d import assembly_mounts
        tools.protected_state = dict(bindings={"completed": {}})
        monkeypatch.setattr(assembly_mounts, "protected_changed", lambda *args: True)
    result = tools.call("build", dict(revision=0 if fault == "stale" else 1, operations=trial))
    assert not result["ok"], result
    assert (tools.assembly.graph(), (tools.directory / "state.json").read_bytes()) == before


def test_build_rechecks_end_state_and_matches_individual_connector_calls(scene):
    tools, operations = scene
    individual = AssemblyTools(tools.directory / "individual", tools.catalog_root)
    assert tools.call("build", dict(revision=0, operations=operations))["ok"]
    aliases = {}
    for original in operations:
        op = deepcopy(original)
        action, alias = op.pop("op"), op.pop("alias")
        for key in ("parent", "target"):
            if op.get(key, "").startswith("@"):
                op[key] = aliases[op[key][1:]]
        if action == "attach":
            result = individual.call("attach", dict(revision=individual.assembly.revision, **op))
            aliases[alias] = result["result"]["instance"]
        else:
            result = individual.call("edit", dict(revision=individual.assembly.revision,
                operations=[dict(op=action, alias=alias, **op)]))
            aliases.update(result["result"]["aliases"])
        assert result["ok"], result
    assert individual.assembly.revision == 4 and tools.assembly.revision == 1
    def placements(scene):
        return [(n["part"], n["color"], n["world"]) for n in scene.assembly.graph()["instances"] if n["kind"] == "part"]
    assert placements(tools) == placements(individual)
    assert tools.assembly.bom() == individual.assembly.bom()


def test_strict_build_does_not_accept_missing_evidence_or_disconnected_parts(scene):
    tools, operations = scene
    loose = deepcopy(operations[:2])
    matrix = np.eye(4)
    matrix[0, 3] = 200
    loose.append(dict(op="add", part="3001.dat", matrix=matrix.tolist()))
    assert not tools.call("build", dict(revision=0, operations=loose))["ok"]
    assert tools.assembly.revision == 0 and not tools.assembly.graph()["instances"]
    draft = tools.call("build", dict(revision=0, operations=loose, strict=False))
    assert draft["ok"] and draft["result"]["native_status"] == "unverified"
    assert tools.call("check", {})["result"]["status"] == "fail"


def test_car_wheel_installs_tyre_and_axle_with_native_connectors(scene):
    tools, _ = scene
    operations = [dict(op="module", name="wheel.ldr", alias="wheel", matrix=np.eye(4).tolist()),
        dict(op="add", alias="rim", parent="@wheel", part="56145.dat", color=71, matrix=np.eye(4).tolist()),
        dict(op="attach", alias="tyre", parent="@wheel", part="61481.dat", color=0,
            source_port="0", target="@rim", target_port="2"),
        dict(op="attach", alias="axle", parent="@wheel", part="18651.dat", color=0,
            source_port="1", target="@rim", target_port="0")]
    result = tools.call("build", dict(revision=0, operations=operations))
    assert result["ok"], result
    assert result["result"]["native_status"] == "pass"
    checked = tools.call("check", {})
    assert checked["result"]["status"] == "pass", checked
    assert len(checked["result"]["components"]) == 1
    exported = tools.call("export", {})
    assert exported["ok"] and exported["result"]["archive"]["archive_roundtrip_verified"]
    from car2lego3d.assembly_graph import IOArchive
    assert IOArchive(tools.directory / "model.io").assembly.bom() == tools.assembly.bom()
