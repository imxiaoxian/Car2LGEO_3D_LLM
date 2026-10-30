import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_candidates import comparison
from car2lego3d.assembly_requirements import vehicle_contract
from car2lego3d.assembly_schedule import module_check
from car2lego3d.assembly_tools import AssemblyTools


def test_joint_repair_keeps_immutable_measurements_without_extra_candidate_votes(tools):
    from car2lego3d.common import digest
    root = tools.module_bindings["chassis"]["roots"][0]
    first = tools.call("candidate", dict(revision=2, module="chassis", label="original"))["result"]
    folder = tools.directory / "module-candidates/chassis" / first["id"]
    before = {name: digest(folder / name) for name in ("candidate.json", "state.json")}
    operation = dict(op="joint", instance=root, type="revolute", axis=[1, 0, 0],
        origin=[0, 0, 0], limits=[-10, 10], purpose="steering")
    assert tools.call("edit", dict(revision=2, operations=[operation]))["ok"]
    assert comparison(tools, "chassis")["selected"] is None
    repaired = tools.call("candidate", dict(revision=3, module="chassis", label="joint repair"))
    assert repaired["ok"], repaired
    second = repaired["result"]
    assert second["id"] != first["id"]
    assert second["structure_id"] == first["structure_id"]
    assert comparison(tools, "chassis")["passing"] == 1
    assert before == {name: digest(folder / name) for name in before}
    assert not tools.call("candidate", dict(revision=3, module="chassis", label="unchanged"))["ok"]
    restored = tools.call("restore_candidate", dict(revision=3, module="chassis", candidate=first["id"]))
    assert restored["ok"], restored
    assert not tools.assembly.graph()["joints"]
    restored = tools.call("restore_candidate", dict(revision=4, module="chassis", candidate=second["id"]))
    assert restored["ok"], restored
    assert tools.assembly.graph()["joints"][root]["axis"] == [1, 0, 0]


def test_legacy_failed_measurement_can_be_repaired_without_overwriting_it(tools, monkeypatch):
    import json
    from car2lego3d import assembly_schedule
    from car2lego3d.common import write_json
    original_check = assembly_schedule.module_check
    def failed(*args, **kwargs):
        return dict(original_check(*args, **kwargs), status="fail")
    monkeypatch.setattr(assembly_schedule, "module_check", failed)
    first = tools.call("candidate", dict(revision=2, module="chassis", label="failed"))["result"]
    path = tools.directory / "module-candidates/chassis" / first["id"] / "candidate.json"
    legacy = {k: v for k, v in first.items() if k not in {"structure_id", "articulation_id"}}
    legacy["schema"] = "assembly-candidate.v1"
    write_json(path, legacy)
    monkeypatch.setattr(assembly_schedule, "module_check", original_check)
    root = tools.module_bindings["chassis"]["roots"][0]
    assert tools.call("edit", dict(revision=2, operations=[dict(op="joint", instance=root,
        type="revolute", axis=[1, 0, 0], origin=[0, 0, 0], limits=[-10, 10], purpose="steering")]))["ok"]
    result = tools.call("candidate", dict(revision=3, module="chassis", label="repaired"))
    assert result["ok"] and result["result"]["measurement"]["status"] == "pass"
    assert json.loads(path.read_text())["measurement"]["status"] == "fail"
    assert comparison(tools, "chassis")["passing"] == 1


@pytest.fixture
def tools(tmp_path):
    root = Path(os.environ.get("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not root.is_dir():
        pytest.skip("Native part library not installed")
    tools = AssemblyTools(tmp_path, root)
    tools.vehicle_contract = vehicle_contract({}, "fixture")
    result = tools.call("edit", dict(revision=0, operations=[
        dict(op="module", name="chassis.ldr", alias="chassis", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@chassis", part="3001.dat", alias="brick", color=4, matrix=np.eye(4).tolist())]))
    assert result["ok"], result
    ids = result["result"]["aliases"]
    assert tools.call("bind_module", dict(revision=1, module="chassis", roots=[ids["chassis"]],
        roles=dict(structure=[ids["brick"]])))["ok"]
    return tools


def test_three_native_trials_required_and_recolor_does_not_count(tools):
    assert module_check(tools, "chassis", compare=False)["status"] == "pass"
    assert module_check(tools, "chassis")["status"] == "unverified"
    first = tools.call("candidate", dict(revision=2, module="chassis", label="single brick"))
    assert first["ok"], first
    graph = tools.assembly.graph()
    brick = next(n["id"] for n in graph["instances"] if n["kind"] == "part")
    parent = tools.module_bindings["chassis"]["roots"][0]
    assert tools.call("edit", dict(revision=2, operations=[dict(op="color", instance=brick, color=14)]))["ok"]
    assert not tools.call("candidate", dict(revision=3, module="chassis", label="renamed"))["ok"]
    for count in (2, 3):
        matrix = np.eye(4)
        matrix[1, 3] = -24 * (count - 1)
        assert tools.call("edit", dict(revision=tools.assembly.revision, operations=[
            dict(op="add", parent=parent, part="3001.dat", color=4, matrix=matrix.tolist())]))["ok"]
        captured = tools.call("candidate", dict(revision=tools.assembly.revision, module="chassis", label=str(count)))
        assert captured["ok"] and captured["result"]["measurement"]["status"] == "pass", captured
    assert comparison(tools, "chassis")["passing"] == 3
    assert module_check(tools, "chassis")["status"] == "pass"
    before = tools.assembly.revision
    restored = tools.call("restore_candidate", dict(revision=before, module="chassis", candidate=first["result"]["id"]))
    assert restored["ok"], restored
    assert tools.assembly.revision == before + 1
    assert len(tools.assembly.bom()) == 1 and tools.assembly.bom()[0]["quantity"] == 1


def test_restore_cannot_erase_new_neighbor_and_completed_parts_are_protected(tools):
    candidate = tools.call("candidate", dict(revision=2, module="chassis", label="one"))["result"]["id"]
    matrix = np.eye(4)
    matrix[0, 3] = 200
    assert tools.call("edit", dict(revision=2, operations=[dict(op="add", part="3001.dat", matrix=matrix.tolist())]))["ok"]
    before = tools.assembly.text()
    assert not tools.call("restore_candidate", dict(revision=3, module="chassis", candidate=candidate))["ok"]
    assert tools.assembly.text() == before
    node = tools.assembly.graph()["instances"][0]
    tools.protected_state = dict(instances={node["id"]: node}, joints={}, bindings={})
    rejected = tools.call("edit", dict(revision=3, operations=[dict(op="remove", instance=node["id"])]))
    assert not rejected["ok"] and "Completed module" in rejected["error"]
    assert tools.assembly.text() == before


def test_new_children_cannot_silently_change_completed_module(tools):
    from copy import deepcopy
    from car2lego3d.assembly_requirements import descendants
    graph = tools.assembly.graph()
    members = descendants(graph, tools.module_bindings["chassis"]["roots"])
    tools.protected_state = deepcopy(dict(instances={n["id"]: n for n in graph["instances"] if n["id"] in members},
        joints={}, bindings=tools.module_bindings))
    before = tools.assembly.text()
    rejected = tools.call("edit", dict(revision=2, operations=[dict(op="add", part="3001.dat",
        parent=tools.module_bindings["chassis"]["roots"][0], matrix=np.eye(4).tolist())]))
    assert not rejected["ok"] and "Completed module" in rejected["error"]
    assert tools.assembly.revision == 2 and tools.assembly.text() == before
    assert "independent submodel" in rejected["error"] and "invalidate" not in rejected["error"]


def test_legacy_duplicate_binding_cannot_pass_module_check_or_become_a_candidate(tools):
    from copy import deepcopy
    binding = tools.module_bindings["chassis"]
    tools.module_bindings["front"] = dict(roots=binding["roots"], roles=dict(body=binding["roles"]["structure"]))
    tools.save()
    before = deepcopy(tools.module_bindings)
    for name in ("front", "chassis"):
        measured = module_check(tools, name, compare=False)
        assert measured["status"] == "fail" and "already assigned" in measured["failures"][0]
        rejected = tools.call("candidate", dict(revision=2, module=name, label="duplicate instance"))
        assert not rejected["ok"]
        assert not list((tools.directory / "module-candidates" / name).glob("*/candidate.json"))
    assert tools.assembly.revision == 2 and tools.module_bindings == before
