import json

import pytest
import trimesh

from car2lego3d.common import digest, write_json
from car2lego3d.shape_evidence import prepare


@pytest.fixture
def evidence(tmp_path):
    source = tmp_path / "shape"
    source.mkdir()
    mesh = source / "calibrated.glb"
    trimesh.creation.box(extents=[4.7, 1.9, 1.4]).export(mesh)
    specifications, references = tmp_path / "spec.json", tmp_path / "views.json"
    write_json(specifications, dict(model="Fixture"))
    write_json(references, dict(views={}))
    calibration = source / "physical-calibration.json"
    write_json(calibration, dict(glb_units="metres", vehicle_spec=dict(sha256=digest(specifications))))
    write_json(source / "reconstruction.json", dict(status="completed", backend="Hunyuan3D-2mv; four jointly conditioned views",
        frame="X-long/Z-up; front direction unverified", inputs=dict(manifest_sha256=digest(references)),
        vehicle_spec=dict(sha256=digest(specifications)),
        outputs={p.name: digest(p) for p in (mesh, calibration)}))
    return source, tmp_path / "context", specifications, references


def test_full_calibrated_geometry_has_bound_sources_and_explicit_limits(evidence):
    mission, resource, bound = prepare(*evidence)
    geometry = resource()
    assert mission["units"] == "metres" and mission["vertices"] == len(geometry["vertices"])
    assert geometry["bounds_m"][1][0] - geometry["bounds_m"][0][0] == pytest.approx(4.7)
    assert len(bound) == 4 and all(digest(path) == checksum for path, checksum in bound.items())
    assert not geometry["physical_verified"] and not geometry["appearance_accepted"]
    assert "joints" not in geometry and "ports" not in geometry


def test_planner_sees_limits_and_navigation_without_embedding_vertices(evidence, tmp_path):
    from car2lego3d.assembly_agent import AssemblyAgent, wire_json
    from car2lego3d.assembly_tools import AssemblyTools
    mission, resource, _ = prepare(*evidence)
    backend = type("Backend", (), dict(name="codex"))()
    tools = AssemblyTools(tmp_path / "assembly", tmp_path / "native")
    agent = AssemblyAgent(tools, backend, dict(shape_evidence=mission))
    tools.context_resources["shape_evidence"] = resource
    payload = agent.context([], 10)
    assert payload["shape_evidence"] == mission
    result = tools.call("context", dict(resource="shape_evidence:/triangles/0"))
    assert result["ok"] and json.loads(result["result"]["text"]) == resource()["triangles"][0]
    assert '"triangles":[' not in wire_json(payload)


@pytest.mark.parametrize("change", ["mesh", "units", "specifications", "references", "incomplete"])
def test_unbound_or_uncalibrated_evidence_is_rejected(evidence, change):
    source, _, specifications, references = evidence
    if change == "mesh":
        trimesh.creation.box().export(source / "calibrated.glb")
    elif change == "specifications":
        write_json(specifications, dict(model="Other"))
    elif change == "references":
        write_json(references, dict(views={}, changed=True))
    else:
        name = "physical-calibration.json" if change == "units" else "reconstruction.json"
        path = source / name
        data = json.loads(path.read_text())
        data.update(glb_units="mm") if change == "units" else data.update(status="failed")
        write_json(path, data)
        if change == "units":
            record = json.loads((source / "reconstruction.json").read_text())
            record["outputs"][name] = digest(path)
            write_json(source / "reconstruction.json", record)
    with pytest.raises(ValueError):
        prepare(*evidence)
