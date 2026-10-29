"""Shared-input vehicle trials; fixtures do not establish vehicle quality."""
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from car2lego3d.assembly_corpus import Corpus
from car2lego3d.common import digest, write_json
from car2lego3d.vehicle_assembly_trials import execute_trial, freeze_case, validate_case


@pytest.fixture
def trial_inputs(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    write_json(source / "request.json", dict(brand="Lamborghini", model="Countach",
        scale=None, scale_mode="auto", assembly_planner="codex", parent_design_id="unshared"))
    write_json(source / "semantic.json", dict(schema="car2lego3d.semantic-scene.v1", units="metres",
        nodes=[dict(id="body", role="body", parent=None,
            local_transform=np.eye(4).tolist(), world_transform=np.eye(4).tolist())]))
    write_json(source / "specifications.json", dict(model="Countach"))
    views = {}
    for i, view in enumerate(("front", "back", "left", "right")):
        image = source / (view + ".png")
        Image.new("RGB", (8, 8), (i * 50, 10, 20)).save(image)
        views[view] = dict(file=image.name, sha256=digest(image))
    write_json(source / "views.json", dict(schema_version=1, views=views))
    native = tmp_path / "native"
    native.mkdir()
    corpus = Corpus(tmp_path / "corpus")
    corpus.db.execute("INSERT INTO models VALUES(?,?,?,?,?,?)",
        ("target", "source-hash", "family", "{}", "parsed", None))
    corpus.db.commit()
    snapshot = corpus.freeze()["snapshot"]
    corpus.db.close()
    return dict(case="countach", request=source / "request.json", semantic=source / "semantic.json",
        specifications=source / "specifications.json", references=source / "views.json",
        native_root=native, corpus_root=tmp_path / "corpus", snapshot=snapshot,
        output=tmp_path / "trial", exclude_models=["target"], max_steps=8, max_seconds=60)


def test_freeze_strips_routing_and_copies_every_reference(trial_inputs):
    manifest = freeze_case(**trial_inputs)
    path = trial_inputs["output"] / "case.json"
    assert validate_case(path) == manifest
    request = json.loads((path.parent / manifest["inputs"]["request"]).read_text())
    assert request == dict(brand="Lamborghini", model="Countach", scale=None, scale_mode="auto")
    assert len([p for p in manifest["files"] if p.endswith(".png")]) == 4
    assert manifest["exclusions"]["models"] == ["target"]
    assert manifest["quality_accepted"] is False and manifest["winner"] is None
    with pytest.raises(ValueError, match="new or empty"):
        freeze_case(**trial_inputs)


@pytest.mark.parametrize("changed", ["inputs/semantic.json", "inputs/references/front.png"])
def test_trial_rejects_changed_semantics_or_images(trial_inputs, changed):
    freeze_case(**trial_inputs)
    (trial_inputs["output"] / changed).write_bytes(b"changed")
    with pytest.raises(ValueError, match="input changed"):
        validate_case(trial_inputs["output"] / "case.json")


@pytest.mark.parametrize("change", ["snapshot", "exclusions", "budget"])
def test_unfrozen_or_unidentified_heldout_case_is_rejected(trial_inputs, change):
    if change == "snapshot":
        trial_inputs["snapshot"] = None
    elif change == "exclusions":
        trial_inputs["exclude_models"] = ["does-not-exist"]
    else:
        trial_inputs["max_steps"] = True
    with pytest.raises(ValueError):
        freeze_case(**trial_inputs)
    assert not (trial_inputs["output"] / "case.json").exists()


def test_planner_setup_failure_is_preserved_without_fallback(trial_inputs, monkeypatch):
    from car2lego3d import vehicle_assembly_trials as trials
    freeze_case(**trial_inputs)
    selected = []

    def unavailable(name, directory):
        selected.append(name)
        raise ValueError("Selected model is unavailable")

    monkeypatch.setattr(trials, "planner", unavailable)
    output = trial_inputs["output"] / "failed"
    with pytest.raises(ValueError, match="unavailable"):
        execute_trial(trial_inputs["output"] / "case.json", "local_finetuned", output)
    record = json.loads((output / "trial.json").read_text())
    assert selected == ["local_finetuned"]
    assert record["status"] == "failed" and record["planner"] == "local_finetuned"
    assert record["winner"] is None


def test_trial_contract_preserves_source_doors_reference_repairs_and_images(trial_inputs, monkeypatch):
    from car2lego3d import vehicle_assembly_trials as trials
    scene = json.loads(trial_inputs["semantic"].read_text())
    for side in ("front_left", "front_right", "rear_left", "rear_right"):
        scene["nodes"].append(dict(id=side, role="door_assembly", parent=None,
            local_transform=np.eye(4).tolist(), world_transform=np.eye(4).tolist()))
    write_json(trial_inputs["semantic"], scene)
    references = json.loads(trial_inputs["references"].read_text())
    repairs = [dict(id="wheel", kind="rim_geometry", status="open", modules=["front_left_wheel"])]
    references["repair_tasks"] = repairs
    write_json(trial_inputs["references"], references)
    freeze_case(**trial_inputs)
    observed = {}

    class Agent:
        def __init__(self, tools, selected, mission, **budgets):
            observed.update(contract=mission["vehicle_contract"], images=tools.reference_images)

        def run(self):
            return dict(status="draft", input_hash="test", stop_reason="test_complete")

    monkeypatch.setattr(trials, "planner", lambda name, directory: object())
    monkeypatch.setattr(trials, "AssemblyAgent", Agent)
    execute_trial(trial_inputs["output"] / "case.json", "local_finetuned", trial_inputs["output"] / "run")
    assert observed["contract"]["reference_repairs"] == repairs
    assert {m for m in observed["contract"]["modules"] if m.startswith("door_")} == {
        "door_front_left", "door_front_right", "door_rear_left", "door_rear_right"}
    assert set(observed["images"]) == {"front", "back", "left", "right"}
    assert all(digest(path) == sha for path, sha in observed["images"].values())


def test_independent_trials_execute_same_mission_with_real_tools(trial_inputs, monkeypatch):
    from car2lego3d import vehicle_assembly_trials as trials
    native = Path("D:/lego/Studio 2.0/ldraw")
    if not native.is_dir():
        pytest.skip("Native part library is not installed")
    trial_inputs["native_root"] = native
    freeze_case(**trial_inputs)
    selected = []

    class ScriptedPlanner:
        def __init__(self, name):
            self.name = name
            self.actions = iter([
                ("edit", dict(revision=0, operations=[dict(op="add", part="3001.dat",
                    color=4, matrix=np.eye(4).tolist())])),
                ("export", {}), ("finish", {}), ("finish", {}), ("finish", {})])

        def propose(self, payload, step):
            tool, arguments = next(self.actions)
            return dict(tool=tool, arguments_json=json.dumps(arguments), reason="Executed fixture")

    def backend(name, directory):
        selected.append(name)
        return ScriptedPlanner(name)

    monkeypatch.setattr(trials, "planner", backend)
    outputs = []
    for name in ("codex", "local_finetuned"):
        output = trial_inputs["output"] / name
        outputs.append(execute_trial(trial_inputs["output"] / "case.json", name, output))
        assert (output / "model.io").is_file()
        state = json.loads((output / "agent.json").read_text())
        assert state["stop_reason"] == "repair_budget_exhausted"
        assert not state["module_progress"]["completion_ready"]
        assert state["contract"]["exclusions"]["models"] == ["target"]
        assert state["steps"] == 5
    assert selected == ["codex", "local_finetuned"]
    assert outputs[0]["mission_sha256"] == outputs[1]["mission_sha256"]
    assert outputs[0]["input_hash"] != outputs[1]["input_hash"]
    assert any("not a controlled context comparison" in item for item in outputs[0]["limitations"])
    assert outputs[0]["mission_sha256"] == outputs[1]["mission_sha256"]
    assert outputs[0]["case_sha256"] == outputs[1]["case_sha256"]
    assert all(r["winner"] is None and not r["quality_accepted"] for r in outputs)


def test_large_semantics_remain_exact_and_are_read_lazily(trial_inputs, monkeypatch):
    from car2lego3d import vehicle_assembly_trials as trials
    data = json.loads(trial_inputs["semantic"].read_text())
    data["nodes"][0]["detail"] = "Full-detail-geometry-source" * 10000
    write_json(trial_inputs["semantic"], data)
    manifest = freeze_case(**trial_inputs)
    path = trial_inputs["output"] / manifest["inputs"]["semantic"]
    assert path.stat().st_size > 150000
    resource = trials.semantic_resource(path, digest(path))
    calls, original = [], trials.read

    def observed_read(source):
        calls.append(source)
        return original(source)

    monkeypatch.setattr(trials, "read", observed_read)
    assert calls == []
    assert resource() == data
    assert resource() is resource()
    assert calls == [path]
    assert resource()["nodes"][0]["detail"] == data["nodes"][0]["detail"]


def test_large_reference_audit_is_preserved_without_truncation(trial_inputs):
    from car2lego3d import vehicle_assembly_trials as trials
    data = json.loads(trial_inputs["references"].read_text())
    data["audit"] = "Unabridged reference provenance " * 10000
    write_json(trial_inputs["references"], data)
    manifest = freeze_case(**trial_inputs)
    path = trial_inputs["output"] / manifest["inputs"]["references"]
    assert path.stat().st_size > 150000
    assert trials.document_resource(path, digest(path))() == data


def test_scene_navigation_preserves_exact_source_pointer_and_world_bounds():
    from car2lego3d.vehicle_assembly_trials import scene_index
    matrix = np.eye(4)
    matrix[0, 3] = 2
    data = dict(schema="car2lego3d.semantic-scene.v1", units="metres", nodes=[
        dict(id="body", role="body", parent=None,
            local_transform=matrix.tolist(), world_transform=matrix.tolist(),
            geometry=dict(vertices_m=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                triangles=[[0, 1, 2]], material_indices=[0]))])
    result = scene_index(data)
    assert result["roots"] == ["body"]
    assert result["nodes"]["body"]["pointer"] == "semantic:/nodes/0"
    assert result["nodes"]["body"]["world_bounds_m"] == [[2, 0, 0], [3, 1, 0]]
    assert result["nodes"]["body"]["vertices"] == 3


def test_website_resources_support_real_size_sources_and_preserve_exact_nodes(trial_inputs):
    from car2lego3d.assembly_website import prepare_resources
    scene = json.loads(trial_inputs["semantic"].read_text())
    scene["nodes"][0]["detail"] = "Full original geometry provenance " * 10000
    write_json(trial_inputs["semantic"], scene)
    arguments = {key: trial_inputs[key] for key in ("output", "request", "semantic", "specifications", "references")}
    mission, resources, bound = prepare_resources(**arguments)
    assert resources["semantic"]() == scene
    assert resources["semantic_index"]()["nodes"]["body"]["pointer"] == "semantic:/nodes/0"
    assert mission["semantic"]["bytes"] > 150000
    assert "data" not in mission["semantic"]
    assert len(bound) == 10
    assert all(digest(Path(name)) == expected for name, expected in bound.items())
