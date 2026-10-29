"""Connector-driven installation must preserve native geometry and transactions."""
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
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", r"D:\lego\Studio 2.0\ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", "off")
    tools = AssemblyTools(tmp_path, native)
    added = tools.call("edit", dict(revision=0, operations=[dict(op="add", alias="base",
        part="3001.dat", color=4, matrix=np.eye(4).tolist())]))
    assert added["ok"], added
    base = added["result"]["aliases"]["base"]
    ports = tools.catalog.ports("3001.dat")
    hole, stud = next((h, s) for h in ports for s in ports if h.kind == "hole" and s.kind == "stud"
        and compatible(h, s) and np.allclose(np.asarray(h.position)[[0, 2]], np.asarray(s.position)[[0, 2]]))
    args = dict(revision=1, part="3001.dat", color=14, source_port=hole.id, target=base, target_port=stud.id)
    return tools, args


def test_attach_seats_native_part_in_one_revision_and_survives_restart(scene):
    tools, args = scene
    before = deepcopy(tools.assembly.graph()["instances"][0])
    result = tools.call("attach", args)
    assert result["ok"], result
    assert result["revision_before"] == 1 and result["revision_after"] == 2
    after = tools.assembly.graph()["instances"]
    assert after[0] == before
    assert after[1]["id"] == result["result"]["instance"]
    assert after[1]["color"] == 14
    np.testing.assert_allclose(np.asarray(after[1]["world"])[:3, 3], [0, -24, 0])
    assert tools.call("check", {})["result"]["status"] == "pass"
    restored = AssemblyTools(tools.directory, tools.catalog_root)
    assert restored.assembly.revision == 2 and restored.assembly.graph() == tools.assembly.graph()
    records = [json.loads(row) for row in (tools.directory / "trajectory.jsonl").read_text().splitlines()]
    assert [row["tool"] for row in records] == ["edit", "attach", "check"]


def test_attach_matches_separate_add_and_snap_with_one_fewer_mutating_call(scene):
    tools, args = scene
    legacy = AssemblyTools(tools.directory / "legacy", tools.catalog_root)
    legacy.assembly = deepcopy(tools.assembly)
    added = legacy.call("edit", dict(revision=1, operations=[dict(op="add", alias="new",
        part=args["part"], color=args["color"], matrix=np.eye(4).tolist())]))
    old = legacy.call("snap", dict(revision=2, source=added["result"]["aliases"]["new"],
        source_port=args["source_port"], target=args["target"], target_port=args["target_port"]))
    assert old["ok"] and tools.call("attach", args)["ok"]
    assert legacy.assembly.bom() == tools.assembly.bom()
    for a, b in zip(legacy.assembly.graph()["instances"], tools.assembly.graph()["instances"]):
        assert a["part"] == b["part"] and a["color"] == b["color"]
        np.testing.assert_allclose(a["world"], b["world"])
    assert legacy.assembly.revision == 3 and tools.assembly.revision == 2


@pytest.mark.parametrize("mode,expected", [("auto", "pass"), ("required", "fail")])
def test_independent_wheel_disagreement_is_preserved_without_automatic_enforcement(scene, monkeypatch, mode, expected):
    pytest.importorskip("bricklayer")
    tools, _ = scene
    source = tools.directory / "wheel.ldr"
    source.write_text("0 Native wheel calibration\n"
        "1 71 0 0 0 1 0 0 0 1 0 0 0 1 56145.dat\n"
        "1 0 0 0 0 1 0 0 0 1 0 0 0 1 61481.dat\n"
        "1 0 0 0 -10 0 0 -1 0 1 0 1 0 0 18651.dat\n")
    tools.import_file(source)
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", mode)
    result = tools.call("check", {})["result"]
    assert result["status"] == expected, result
    independent = result["bricklayer"]
    assert independent["native_status"] == "pass" and independent["status"] == "fail"
    assert independent["complete"] and independent["disagreement"]
    assert independent["collision_count"] == 1
    assert result["gates"]["physical"] == "unverified"
    parts = {node["id"] for node in tools.assembly.graph()["instances"] if node["kind"] == "part"}
    assert independent["problem_parts"]
    assert all(problem["instance"] in parts for problem in independent["problem_parts"].values())
    saved = json.loads((tools.directory / "bricklayer-audit.json").read_text(encoding="utf-8"))
    assert saved == independent
    exported = tools.call("export", {})
    assert exported["ok"] is (mode == "auto"), exported
    assert (tools.directory / "model.io").exists() is (mode == "auto")
    if mode == "required":
        assert "BrickLayer" in exported["error"]


@pytest.mark.parametrize("fault", ["port", "stale", "collision", "missing_geometry", "protected"])
def test_rejected_attach_leaves_no_part_revision_or_saved_checkpoint(scene, fault, monkeypatch):
    tools, args = scene
    if fault == "collision":
        assert tools.call("attach", args)["ok"]
        args = {**args, "revision": 2}
    elif fault == "port":
        args = {**args, "source_port": "invented"}
    elif fault == "stale":
        args = {**args, "revision": 0}
    elif fault == "missing_geometry":
        args = {**args, "part": "no-such-part.dat"}
    elif fault == "protected":
        from car2lego3d import assembly_mounts
        tools.protected_state = dict(bindings={"fixture": {}})
        # Reject at the real host protection boundary after the proposed edit.
        monkeypatch.setattr(assembly_mounts, "protected_changed", lambda *args: True)
    before = (tools.assembly.graph(), (tools.directory / "state.json").read_bytes())
    result = tools.call("attach", args)
    assert not result["ok"], result
    assert (tools.assembly.graph(), (tools.directory / "state.json").read_bytes()) == before
