import json
import os
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest

from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_catalog import compatible
from car2lego3d.assembly_tools import AssemblyTools


@pytest.fixture
def native():
    root = Path(os.environ.get("LDRAW_LIBRARY_PATH", r"D:\lego\Studio 2.0\ldraw"))
    if not root.is_dir():
        pytest.skip("Native catalog not installed")
    return root


def add(tools, y=0):
    matrix = np.eye(4)
    matrix[1, 3] = y
    result = tools.call("edit", dict(revision=tools.assembly.revision,
        operations=[dict(op="add", part="3001.dat", color=4, matrix=matrix.tolist())]))
    assert result["ok"], result
    return tools.assembly.graph()["instances"][-1]["id"]


def test_restart_preserves_source_metadata_and_revision(tmp_path, native):
    source = tmp_path / "source.io"
    with ZipFile(source, "w") as archive:
        archive.writestr("model.ldr", "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n")
        archive.writestr("unknown.bin", b"opaque\x00metadata")
        archive.comment = b"roundtrip"
    directory = tmp_path / "working"
    tools = AssemblyTools(directory, native)
    tools.import_file(source)
    identity = tools.assembly.graph()["instances"][0]["id"]
    assert tools.call("edit", dict(revision=0, operations=[dict(op="color", instance=identity, color=14)]))["ok"]
    restarted = AssemblyTools(directory, native)
    assert restarted.assembly.revision == 1
    exported = restarted.call("export", {})
    assert exported["ok"], exported
    with ZipFile(directory / "model.io") as archive:
        assert archive.read("unknown.bin") == b"opaque\x00metadata"
        assert archive.comment == b"roundtrip"
    assert IOArchive(directory / "model.io").assembly.bom() == restarted.assembly.bom()
    with ZipFile(source) as archive:
        assert b"1 4 " in archive.read("model.ldr")


def test_schema_failure_is_atomic(tmp_path, native):
    tools = AssemblyTools(tmp_path, native)
    add(tools)
    before = (tools.assembly.text(), (tmp_path / "state.json").read_bytes())
    result = tools.call("edit", dict(revision=1, operations=[dict(op="remove", instance="missing", unexpected=True)]))
    assert not result["ok"]
    assert tools.assembly.revision == 1
    assert (tools.assembly.text(), (tmp_path / "state.json").read_bytes()) == before
    assert not tools.call("retrieve", dict(exclude_models=[]))["ok"]


def test_schema_failure_keeps_full_error_and_adds_actionable_branch_diagnostic(tmp_path, native):
    tools = AssemblyTools(tmp_path, native)
    result = tools.call("build", dict(revision=0, operations=[dict(
        op="module", name="Mounted native wheel", matrix=np.eye(4).tolist(), alias="wheel")]))
    assert not result["ok"]
    assert "is not valid under any of the given schemas" in result["error"]
    diagnostic = result["diagnostic"]
    assert diagnostic["kind"] == "schema_validation"
    assert diagnostic["tool"] == "build"
    assert diagnostic["path"] == "/operations/0/name"
    assert diagnostic["validator"] == "pattern"
    assert diagnostic["operation"] == "module"
    assert diagnostic["expected"] == r"\.ldr$"
    assert "Mounted native wheel" in diagnostic["message"]


def test_cancelled_motion_preserves_checkpoint_and_previous_report(tmp_path, monkeypatch):
    from car2lego3d import assembly_tools
    from car2lego3d.job_runtime import AssemblyCancelled
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    add(tools)
    instance = tools.assembly.graph()["instances"][0]["id"]
    tools.assembly.joints[instance] = dict(axis=[0, 0, 1], origin=[0, 0, 0],
        limits=[-180, 180], purpose="rolling")
    tools.save()
    report = tmp_path / "motion.json"
    report.write_bytes(b"previous immutable evidence")
    before = (tools.assembly.text(), (tmp_path / "state.json").read_bytes(), report.read_bytes())
    validated = []

    def checkpoint():
        if len(validated) == 2:
            raise AssemblyCancelled("stop")

    def validate(*args, **kwargs):
        validated.append(True)
        return dict(collisions=[], unknown=[], invalid_transforms=[], gates=dict(connections="pass"))

    tools.checkpoint = checkpoint
    monkeypatch.setattr(assembly_tools, "validate", validate)
    with pytest.raises(AssemblyCancelled):
        tools.call("motion", dict(samples=181))
    assert len(validated) == 2
    assert (tools.assembly.text(), (tmp_path / "state.json").read_bytes(), report.read_bytes()) == before
    record = json.loads((tmp_path / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["ok"] is False and "AssemblyCancelled" in record["error"]
    assert record["revision_before"] == record["revision_after"]


def test_native_snap_render_export_and_reopen(tmp_path, native):
    tools = AssemblyTools(tmp_path, native)
    target, source = add(tools), add(tools, -100)
    ports = tools.catalog.ports("3001.dat")
    hole, stud = next((h, s) for h in ports for s in ports if h.kind == "hole" and s.kind == "stud"
        and compatible(h, s) and np.allclose(np.array(h.position)[[0, 2]], np.array(s.position)[[0, 2]]))
    result = tools.call("snap", dict(revision=2, source=source, source_port=hole.id, target=target, target_port=stud.id))
    assert result["ok"], result
    report = tools.call("check", {})
    assert report["result"]["status"] == "pass", report
    assert tools.call("render", {})["ok"]
    assert (tmp_path / "assembly.glb").stat().st_size > 100
    assert tools.call("export", {})["ok"]
    assert len(IOArchive(tmp_path / "model.io").assembly.graph()["instances"]) == 2
    assert json.loads((tmp_path / "checks.json").read_text())["gates"]["physical"] == "unverified"


@pytest.mark.parametrize("tool", ["snap", "attach", "build"])
@pytest.mark.parametrize("compatible_count", [0, 12])
def test_incompatible_ports_give_bounded_native_types_without_mutation(tmp_path, monkeypatch, tool, compatible_count):
    from car2lego3d.native_ports import Port
    tools = AssemblyTools(tmp_path, tmp_path / "native")
    target, source = add(tools), add(tools, -100)
    ports = [Port("stud", "stud", 0, (0, 0, 0), (0, 1, 0), altitude=20, area=4)]
    ports += [Port(f"hole-{i}", "hole", 0, (i * 10, 24, 0), (0, 1, 0), altitude=20, area=4)
              for i in range(compatible_count)]
    ports.append(Port("incompatible-hole", "hole", 0, (0, 0, 0), (0, 1, 0), altitude=40, area=4))
    monkeypatch.setattr(tools.catalog, "ports", lambda _: ports)
    before = (tools.assembly.graph(), tools.assembly.text(), (tmp_path / "state.json").read_bytes())
    arguments = dict(revision=2, source_port="stud", target=target, target_port="stud")
    if tool == "snap":
        arguments["source"] = source
    else:
        arguments["part"] = "3001.dat"
        if tool == "build":
            arguments = dict(revision=2, operations=[dict(op="attach",
                **{key: value for key, value in arguments.items() if key != "revision"})])
    result = tools.call(tool, arguments)
    assert result["ok"] is False
    feedback = json.loads(result["error"].split("Incompatible connector types: ", 1)[1])
    assert result["diagnostic"] == feedback
    for endpoint in ("source", "target"):
        assert feedback[endpoint]["part"] == "3001.dat"
        assert feedback[endpoint]["port"]["id"] == "stud"
        assert feedback[endpoint]["port"]["kind"] == "stud"
        suggestions = feedback[endpoint + "_alternatives"]
        assert suggestions["total"] == compatible_count
        assert suggestions["truncated"] is (compatible_count > 8)
        assert [p["id"] for p in suggestions["ports"]] == [f"hole-{i}" for i in range(min(8, compatible_count))]
        assert all(compatible(next(p for p in ports if p.id == row["id"]), ports[0])
                   for row in suggestions["ports"])
    assert "remain unverified" in feedback["scope"]
    assert result["revision_before"] == result["revision_after"] == 2
    assert (tools.assembly.graph(), tools.assembly.text(), (tmp_path / "state.json").read_bytes()) == before
    assert AssemblyTools(tmp_path, tmp_path / "native").assembly.text() == before[1]
    from car2lego3d.assembly_agent import AssemblyAgent
    agent = AssemblyAgent(tools, None, {})
    payload = agent.context([dict(decision=dict(tool=tool), observation=result)], remaining=1)
    assert payload["last"]["diagnostic"] == feedback
    assert payload["last"]["resource"] == "error"
    if tool == "build":
        assert feedback["build_operation"] == 0


@pytest.mark.parametrize("tool", ["snap", "attach"])
def test_native_glass_panel_incompatibility_reports_actual_catalog_ports(tmp_path, native, tool):
    tools = AssemblyTools(tmp_path, native)
    added = tools.call("edit", dict(revision=0, operations=[
        dict(op="add", alias="panel", part="59349.dat", matrix=np.eye(4).tolist()),
        dict(op="add", alias="glass", part="4215a.dat", matrix=np.eye(4).tolist())]))
    assert added["ok"], added
    ids = added["result"]["aliases"]
    before = (tools.assembly.graph(), (tmp_path / "state.json").read_bytes())
    arguments = dict(revision=1, source_port="0:0:2", target=ids["panel"], target_port="0:0:4")
    arguments.update(dict(source=ids["glass"]) if tool == "snap" else dict(part="4215a.dat"))
    result = tools.call(tool, arguments)
    assert not result["ok"], result
    feedback = json.loads(result["error"].split("Incompatible connector types: ", 1)[1])
    for endpoint, part in (("source", "4215a.dat"), ("target", "59349.dat")):
        assert feedback[endpoint]["part"] == part
        selected = next(p for p in tools.catalog.ports(part) if p.id == feedback[endpoint]["port"]["id"])
        assert feedback[endpoint]["port"]["kind"] == selected.kind
        assert feedback[endpoint]["port"]["area"] == selected.area == 2
        opposite = "target" if endpoint == "source" else "source"
        other = next(p for p in tools.catalog.ports(feedback[opposite]["part"])
                     if p.id == feedback[opposite]["port"]["id"])
        expected = [p.id for p in tools.catalog.ports(part) if compatible(p, other)]
        alternatives = feedback[endpoint + "_alternatives"]
        assert alternatives["total"] == len(expected)
        assert [p["id"] for p in alternatives["ports"]] == expected[:8]
    assert (tools.assembly.graph(), (tmp_path / "state.json").read_bytes()) == before


def test_snap_module_keeps_panel_glass_and_joint_together(tmp_path, native):
    tools = AssemblyTools(tmp_path, native)
    matrix, glass = np.eye(4), np.eye(4)
    matrix[:3, 3] = [200, -100, 0]
    glass[1, 3] = -24
    created = tools.call("edit", dict(revision=0, operations=[
        dict(op="add", alias="body", part="3001.dat", color=4, matrix=np.eye(4).tolist()),
        dict(op="module", alias="door", name="door.ldr", matrix=matrix.tolist()),
        dict(op="add", alias="panel", parent="@door", part="3001.dat", color=4, matrix=np.eye(4).tolist()),
        dict(op="add", alias="glass", parent="@door", part="3001.dat", color=47, matrix=glass.tolist()),
        dict(op="joint", instance="@door", axis=[0, 1, 0], origin=[200, -100, 0], limits=[0, 70], purpose="door")]))
    assert created["ok"], created
    aliases = created["result"]["aliases"]
    before = {n["id"]: n for n in tools.assembly.graph()["instances"]}
    ports = tools.catalog.ports("3001.dat")
    hole, stud = next((h, s) for h in ports for s in ports if h.kind == "hole" and s.kind == "stud"
        and compatible(h, s) and np.allclose(np.array(h.position)[[0, 2]], np.array(s.position)[[0, 2]]))
    arguments = dict(revision=1, source=aliases["panel"], source_port=hole.id,
        target=aliases["body"], target_port=stud.id, moving=aliases["door"])
    assert tools.call("snap", arguments)["ok"]
    after = {n["id"]: n for n in tools.assembly.graph()["instances"]}
    np.testing.assert_allclose(after[aliases["panel"]]["world"],
        np.asarray(after[aliases["door"]]["world"]) @ np.asarray(before[aliases["panel"]]["local"]))
    np.testing.assert_allclose(after[aliases["glass"]]["world"],
        np.asarray(after[aliases["door"]]["world"]) @ np.asarray(before[aliases["glass"]]["local"]))
    np.testing.assert_allclose(after[aliases["body"]]["world"], before[aliases["body"]]["world"])
    joint = tools.assembly.joints[aliases["door"]]
    np.testing.assert_allclose(joint["origin"], np.asarray(after[aliases["door"]]["world"])[:3, 3])
    assert joint["purpose"] == "door" and joint["limits"] == [0, 70]
    assert tools.call("check", {})["result"]["status"] == "pass"
    saved = (tools.assembly.text(), (tmp_path / "state.json").read_bytes())
    assert not tools.call("snap", {**arguments, "revision": 2, "target": aliases["glass"]})["ok"]
    assert not tools.call("snap", {**arguments, "revision": 2, "moving": aliases["body"]})["ok"]
    assert (tools.assembly.text(), (tmp_path / "state.json").read_bytes()) == saved


def test_native_shaft_snap_preserves_wheel_facing_and_is_atomic_for_wrong_key(tmp_path, native):
    tools = AssemblyTools(tmp_path, native)
    axle = np.eye(4)
    axle[:3, :3] = [[0, 0, -1], [0, 1, 0], [1, 0, 0]]
    axle[2, 3] = 120
    added = tools.call("edit", dict(revision=0, operations=[
        dict(op="add", alias="rim", part="56145.dat", color=15, matrix=np.eye(4).tolist()),
        dict(op="add", alias="axle", part="18651.dat", color=0, matrix=axle.tolist())]))
    assert added["ok"], added
    ids = added["result"]["aliases"]
    arguments = dict(revision=1, source=ids["axle"], source_port="1",
                     target=ids["rim"], target_port="0")
    snapped = tools.call("snap", arguments)
    assert snapped["ok"], snapped
    graph = tools.assembly.graph()
    moved = next(n for n in graph["instances"] if n["id"] == ids["axle"])
    np.testing.assert_allclose(np.asarray(moved["world"])[:3, :3], axle[:3, :3])
    checked = tools.call("check", {})["result"]
    assert len(checked["components"]) == 1
    assert any({c["a"], c["b"]} == set(ids.values()) and
               {c["port_a"], c["port_b"]} == {"0", "1"} for c in checked["connections"])
    from scipy.spatial.transform import Rotation
    wrong = np.asarray(moved["local"]).copy()
    wrong[:3, :3] = Rotation.from_euler("z", 45, degrees=True).as_matrix() @ wrong[:3, :3]
    assert tools.call("edit", dict(revision=2, operations=[dict(op="transform", instance=ids["axle"],
                                                               matrix=wrong.tolist())]))["ok"]
    saved = (tools.assembly.text(), (tmp_path / "state.json").read_bytes())
    rejected = tools.call("snap", {**arguments, "revision": 3})
    assert not rejected["ok"] and "keyed orientation" in rejected["error"]
    assert (tools.assembly.text(), (tmp_path / "state.json").read_bytes()) == saved


def test_embedded_geometry_is_portable(tmp_path, native):
    source = tmp_path / "embedded.io"
    with ZipFile(source, "w") as archive:
        archive.writestr("model.ldr", "0 FILE main.ldr\n1 47 0 0 0 1 0 0 0 1 0 0 0 1 glass.dat\n0 FILE glass.dat\n3 16 0 0 0 20 0 0 0 -20 0\n")
    tools = AssemblyTools(tmp_path / "work", native)
    tools.import_file(source)
    assert tools.call("render", {})["ok"]
    assert tools.call("export", {})["ok"]
    with ZipFile(tools.directory / "model.io") as archive:
        assert "CustomParts/parts/glass.dat" in archive.namelist()
    assert tools.call("check", {})["result"]["status"] == "unverified"


def test_native_transparent_material_survives_glb_export(tmp_path, native):
    import trimesh
    tools = AssemblyTools(tmp_path, native)
    added = tools.call("edit", dict(revision=0, operations=[dict(op="add", part="3068b.dat",
        color=47, matrix=np.eye(4).tolist())]))
    assert added["ok"]
    queried = tools.call("query_part", dict(part="3068b.dat"))["result"]
    assert queried["native_geometry"]["triangles"] > 0
    assert tools.call("render", {})["ok"]
    scene = trimesh.load(tools.directory / "assembly.glb", force="scene")
    materials = [mesh.visual.material for mesh in scene.geometry.values()]
    assert any(material.alphaMode == "BLEND" and material.baseColorFactor[3] < 255 for material in materials)
