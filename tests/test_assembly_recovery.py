from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from car2lego3d.assembly_recovery import seed_checkpoint, recheck_seed
from car2lego3d.assembly_requirements import vehicle_contract
from car2lego3d.assembly_schedule import ModuleScheduler
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def launch_fixture(tmp_path, *, checkpoint=False):
    from car2lego3d.assembly_recovery import record_launch
    directory = tmp_path / ("a" * 32)
    artifacts = directory / "revisions/old/artifacts"
    paths = dict(request=directory / "request.json", semantic=artifacts / "02-mesh/semantic-scene.json",
        references=artifacts / "01-image/views.json", specifications=directory / "vehicle-specs.json")
    for path in paths.values():
        write_json(path, {})
    image = artifacts / "01-image/anchor.png"
    image.write_bytes(b"immutable reference pixels")
    args = dict(paths, output=artifacts / "03-lego/agent", publish_artifacts=artifacts,
        backend="codex", snapshot="frozen-snapshot", corpus_root=tmp_path / "corpus",
        native_root=tmp_path / "native", max_steps=160, context_profile="production")
    record_launch(args, {str(path.resolve()): digest(path) for path in [*paths.values(), image]})
    if checkpoint:
        write_json(args["output"] / "state.json", dict(revision=1))
        write_json(args["output"] / "module-tasks.json", dict(tasks={}))
    return directory, artifacts, args, image


def test_resume_uses_exact_recorded_assembly_inputs(tmp_path):
    from car2lego3d.assembly_recovery import resume_arguments
    directory, artifacts, original, _ = launch_fixture(tmp_path)
    result = resume_arguments(directory, artifacts)
    assert result["request"] == original["request"]
    assert result["output"] == original["output"]
    assert result["retry"] is True and "seed_from" not in result
    assert result["snapshot"] == "frozen-snapshot"


@pytest.mark.parametrize("change", ["reference_pixels", "missing_semantic", "cross_job_path"])
def test_resume_rejects_changed_or_missing_inputs(tmp_path, change):
    from car2lego3d.assembly_recovery import resume_arguments
    directory, artifacts, original, image = launch_fixture(tmp_path, checkpoint=True)
    if change == "reference_pixels":
        image.write_bytes(b"different pixels")
    elif change == "missing_semantic":
        original["semantic"].unlink()
    else:
        launch = original["output"] / "launch.json"
        record = json.loads(launch.read_text())
        record["arguments"]["request"] = str(tmp_path / "other/request.json")
        write_json(launch, record)
    with pytest.raises(ValueError):
        resume_arguments(directory, artifacts)
    assert not list((directory / "revisions").glob("assembly-recovery-*"))


def test_executor_change_forks_cached_inputs_without_altering_source(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent
    from car2lego3d.assembly_recovery import resume_arguments
    directory, artifacts, original, image = launch_fixture(tmp_path, checkpoint=True)
    before = digest(original["output"] / "launch.json")
    monkeypatch.setattr(assembly_agent, "execution_hash", lambda: "changed-executor")
    result = resume_arguments(directory, artifacts)
    assert result["seed_from"] == original["output"]
    assert result["output"] != original["output"]
    assert result["request"] == original["request"]
    assert digest(result["publish_artifacts"] / "01-image/anchor.png") == digest(image)
    assert digest(original["output"] / "launch.json") == before


def test_executor_change_copies_bound_shape_evidence(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent
    from car2lego3d.assembly_recovery import record_launch, resume_arguments
    directory, artifacts, arguments, image = launch_fixture(tmp_path, checkpoint=True)
    shape = artifacts / "02-shape"
    files = [shape / name for name in ("calibrated.glb", "reconstruction.json", "physical-calibration.json")]
    for path in files:
        write_json(path, dict(immutable=path.name))
    arguments["shape_evidence"] = shape
    inputs = [arguments[key] for key in ("request", "semantic", "references", "specifications")]
    record_launch(arguments, {str(path.resolve()): digest(path) for path in [*inputs, image, *files]})
    monkeypatch.setattr(assembly_agent, "execution_hash", lambda: "changed-executor")
    result = resume_arguments(directory, artifacts)
    assert result["shape_evidence"] == result["publish_artifacts"] / "02-shape"
    assert all(digest(result["shape_evidence"] / path.name) == digest(path) for path in files)


@pytest.mark.parametrize("partial_state", [False, True])
def test_failed_migration_can_resume_from_exact_source_without_reusing_partial_state(tmp_path, monkeypatch, partial_state):
    from car2lego3d import assembly_agent
    from car2lego3d.assembly_recovery import resume_arguments
    directory, artifacts, original, _ = launch_fixture(tmp_path, checkpoint=True)
    source_files = [original["output"] / name for name in ("launch.json", "state.json", "module-tasks.json")]
    before = {str(path): digest(path) for path in source_files}
    monkeypatch.setattr(assembly_agent, "execution_hash", lambda: "changed-executor")
    first = resume_arguments(directory, artifacts)
    if partial_state:
        write_json(first["output"] / "state.json", dict(revision=999))
        write_json(first["output"] / "module-tasks.json", dict(tasks="incomplete"))
    pending = first["output"] / "pending-recovery.json"
    pending_before = pending.read_bytes()
    second = resume_arguments(directory, first["publish_artifacts"])
    assert second["seed_from"] == original["output"]
    assert second["output"] not in (first["output"], original["output"])
    assert not (second["output"] / "state.json").exists()
    assert pending.read_bytes() == pending_before
    assert {str(path): digest(path) for path in source_files} == before


@pytest.mark.parametrize("changed", ["launch.json", "state.json", "module-tasks.json", "cross_job"])
def test_pending_migration_rejects_changed_checkpoint_or_cross_job_source(tmp_path, monkeypatch, changed):
    from car2lego3d import assembly_agent
    from car2lego3d.assembly_recovery import resume_arguments
    directory, artifacts, original, _ = launch_fixture(tmp_path, checkpoint=True)
    monkeypatch.setattr(assembly_agent, "execution_hash", lambda: "changed-executor")
    first = resume_arguments(directory, artifacts)
    if changed == "cross_job":
        pending_path = first["output"] / "pending-recovery.json"
        pending = json.loads(pending_path.read_text())
        pending["source"] = str(tmp_path / "other/03-lego/agent")
        write_json(pending_path, pending)
    else:
        write_json(original["output"] / changed, dict(changed=True))
    with pytest.raises(ValueError, match="recovery"):
        resume_arguments(directory, first["publish_artifacts"])


def test_legacy_checkpoint_requires_explicit_migration(tmp_path):
    from car2lego3d.assembly_recovery import resume_arguments
    with pytest.raises(ValueError, match="启动记录"):
        resume_arguments(tmp_path, tmp_path / "artifacts")


@pytest.mark.parametrize("failure", ["input_contract", "historical_run", "seed", "seed_recheck"])
def test_rejected_website_resume_preserves_launch_record(tmp_path, monkeypatch, failure):
    from car2lego3d import assembly_recovery, assembly_website

    _, _, arguments, _ = launch_fixture(tmp_path)
    output = arguments["output"]
    launch = output / "launch.json"
    before = launch.read_bytes()
    mission = dict(request=dict(data=dict(brand="Lamborghini", model="Countach")),
        vehicle_contract=vehicle_contract(dict(brand="Lamborghini", model="Countach"), "mesh"))
    monkeypatch.setattr(assembly_website, "prepare_resources", lambda *args: (mission, {}, {}))
    monkeypatch.setattr(assembly_website, "load_references", lambda *args: ({}, {}))
    monkeypatch.setattr(assembly_website, "planner", lambda *args: type("Backend", (), {"name": "codex"})())
    arguments.update(snapshot=None, context_profile="shared-text", retry=True)

    def reject(*args):
        raise ValueError("invalid seed evidence")

    if failure == "input_contract":
        write_json(output / "module-tasks.json", dict(input_hash="different-input", planner="codex", tasks={}))
        message = "Module task inputs"
    elif failure == "historical_run":
        write_json(output / "agent.json", dict(schema="historical-whole-car"))
        message = "Historical whole-car"
    else:
        arguments["seed_from"] = tmp_path / "previous-run"
        monkeypatch.setattr(assembly_recovery, "seed_checkpoint", reject if failure == "seed" else lambda *args: {})
        if failure == "seed_recheck":
            monkeypatch.setattr(assembly_recovery, "seed_checkpoint", lambda *args: {"source": "previous-run"})
            monkeypatch.setattr(assembly_recovery, "recheck_seed", reject)
        message = "invalid seed evidence"

    with pytest.raises(ValueError, match=message):
        assembly_website._run(**arguments)
    assert launch.read_bytes() == before


def test_validated_website_launch_is_recorded_before_execution(tmp_path, monkeypatch):
    from car2lego3d import assembly_schedule, assembly_website

    _, _, arguments, _ = launch_fixture(tmp_path)
    mission = dict(request=dict(data=dict(brand="Lamborghini", model="Countach")),
        vehicle_contract=vehicle_contract(dict(brand="Lamborghini", model="Countach"), "mesh"))
    monkeypatch.setattr(assembly_website, "prepare_resources", lambda *args: (mission, {}, {}))
    monkeypatch.setattr(assembly_website, "load_references", lambda *args: ({}, {}))
    monkeypatch.setattr(assembly_website, "planner", lambda *args: type("Backend", (), {"name": "codex"})())
    arguments.update(snapshot=None, context_profile="shared-text", retry=True)

    def execution(scheduler, **kwargs):
        launch = json.loads((scheduler.tools.directory / "launch.json").read_text())
        assert launch["arguments"]["context_profile"] == "shared-text"
        assert launch["arguments"]["backend"] == "codex"
        assert launch["arguments"]["snapshot"] == scheduler.contract["snapshot"]
        raise RuntimeError("reached execution")

    monkeypatch.setattr(assembly_schedule.ModuleScheduler, "run", execution)
    with pytest.raises(RuntimeError, match="reached execution"):
        assembly_website._run(**arguments)


def setup_source(tmp_path):
    source = AssemblyTools(tmp_path / "old", tmp_path / "native")
    request = tmp_path / "job/request.json"
    write_json(request, dict(brand="Lamborghini", model="Countach"))
    source.call("edit", dict(revision=0, operations=[dict(op="module", name="chassis.ldr", alias="chassis",
        matrix=np.eye(4).tolist()), dict(op="add", parent="@chassis", part="3001.dat", alias="brick",
        matrix=np.eye(4).tolist())]))
    source.save()
    mission = dict(request=dict(sha256=digest(request)), specifications=dict(sha256="spec"),
        semantic=dict(sha256="mesh"), references=dict(sha256="views"),
        vehicle_contract=vehicle_contract(dict(brand="Lamborghini", model="Countach"), "mesh"))
    schedule = dict(planner="codex", input_hash="old-input", tasks={},
        contract=dict(mission=deepcopy(mission), snapshot="snapshot", exclusions=source.exclusions,
            execution_hash="old-executor"))
    write_json(source.directory / "module-tasks.json", schedule)
    return source, request, mission


def test_new_revision_preserves_source_and_never_inherits_acceptance(tmp_path):
    source, request, mission = setup_source(tmp_path)
    before = digest(source.directory / "state.json")
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    record = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    assert digest(source.directory / "state.json") == before
    assert target.assembly.graph() == source.assembly.graph()
    assert record["acceptance"] == "unverified" and not record["carried_modules"]
    assert record["source_execution_hash"] == "old-executor"
    with pytest.raises(ValueError, match="already has state"):
        seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)


def test_recovery_retires_only_incompatible_wheels_without_touching_source(tmp_path):
    from car2lego3d.assembly_mounts import semantic_node
    source, request, mission = setup_source(tmp_path)
    chassis, brick = source.assembly.graph()["instances"]
    source.module_bindings["chassis"] = dict(roots=[chassis["id"]], roles=dict(structure=[brick["id"]]))
    result = source.call("edit", dict(revision=source.assembly.revision, operations=[
        dict(op="module", parent=chassis["id"], name="wheel.ldr", alias="wheel", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@wheel", part="41896.dat", alias="rim", matrix=np.eye(4).tolist()),
        dict(op="joint", instance="@wheel", axis=[0, 0, 1], limits=[0, 360], purpose="rolling")]))
    assert result["ok"], result
    ids = result["result"]["aliases"]
    source.module_bindings["wheel_front_left"] = dict(roots=[ids["wheel"]], roles=dict(rim=[ids["rim"]]))
    source.save()
    schedule = json.loads((source.directory / "module-tasks.json").read_text())
    schedule["tasks"] = {name: dict(status="assembled", attempts=[]) for name in source.module_bindings}
    write_json(source.directory / "module-tasks.json", schedule)
    before = {name: digest(source.directory / name) for name in ("state.json", "module-tasks.json")}
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    record = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    retired, = record["retired_modules"]
    assert retired["module"] == "wheel_front_left" and retired["geometry_preserved_in_source"]
    assert retired["reasons"] == ["wheel_not_mounted_under_steering"]
    assert target.module_bindings == {"chassis": source.module_bindings["chassis"]}
    nodes = {n["id"]: n for n in target.assembly.graph()["instances"]}
    assert ids["wheel"] not in nodes and ids["rim"] not in nodes and ids["wheel"] not in target.assembly.joints
    assert semantic_node(nodes[chassis["id"]]) == semantic_node(chassis)
    assert semantic_node(nodes[brick["id"]]) == semantic_node(brick)
    assert target.assembly.revision == source.assembly.revision + 1
    assert {name: digest(source.directory / name) for name in before} == before


def test_recovery_refuses_to_remove_wheel_owned_by_another_binding(tmp_path):
    source, request, mission = setup_source(tmp_path)
    chassis, brick = source.assembly.graph()["instances"]
    source.module_bindings = dict(
        wheel_front_left=dict(roots=[chassis["id"]], roles=dict(rim=[brick["id"]])),
        chassis=dict(roots=[chassis["id"]], roles=dict(structure=[brick["id"]])))
    source.save()
    before = digest(source.directory / "state.json")
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    with pytest.raises(ValueError, match="overlaps another module"):
        seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    assert not (target.directory / "state.json").exists()
    assert digest(source.directory / "state.json") == before


@pytest.mark.parametrize("changed", ["request", "specifications", "semantic", "references", "vehicle_contract"])
def test_changed_vehicle_inputs_cannot_seed_previous_geometry(tmp_path, changed):
    source, request, mission = setup_source(tmp_path)
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    altered = deepcopy(mission)
    altered[changed] = {"changed": True}
    with pytest.raises(ValueError, match="inputs changed"):
        seed_checkpoint(target, source.directory, altered, "codex", "snapshot", request)
    assert not (target.directory / "state.json").exists()


def test_competing_planner_or_live_worker_cannot_seed(tmp_path, monkeypatch):
    from car2lego3d import job_runtime
    source, request, mission = setup_source(tmp_path)
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    for planner, snapshot in (("local_finetuned", "snapshot"), ("codex", "different")):
        with pytest.raises(ValueError, match="planner, corpus"):
            seed_checkpoint(target, source.directory, mission, planner, snapshot, request)
    write_json(request.parent / "runtime.json", dict(owner=dict(pid=123, identity="other")))
    monkeypatch.setattr(job_runtime, "executing", lambda runtime: True)
    with pytest.raises(ValueError, match="live worker"):
        seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)


@pytest.mark.parametrize("relation", ["child", "windows_launcher", "wrong_revision", "unrelated"])
def test_seed_recognizes_only_website_child_bound_to_selected_revision(tmp_path, monkeypatch, relation):
    import os
    from car2lego3d import job_runtime
    source, request, mission = setup_source(tmp_path)
    artifacts = request.parent / "revisions/new/artifacts"
    target = AssemblyTools(artifacts / "03-lego/agent", source.catalog_root)
    child = job_runtime.current_owner() if relation != "windows_launcher" else dict(pid=os.getppid(), identity="launcher")
    if relation == "unrelated":
        child = dict(pid=123, identity="other")
    write_json(request.parent / "desktop.json", dict(artifacts=
        "revisions/wrong/artifacts" if relation == "wrong_revision" else "revisions/new/artifacts"))
    write_json(request.parent / "runtime.json", dict(status="running", owner=dict(pid=321, identity="host"), child=child))
    real_identity = job_runtime.process_identity
    monkeypatch.setattr(job_runtime, "process_identity", lambda pid:
        "host" if pid == 321 else (child["identity"] if pid == child["pid"] else real_identity(pid)))
    if relation in {"wrong_revision", "unrelated"}:
        with pytest.raises(ValueError, match="live worker"):
            seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
        assert not (target.directory / "state.json").exists()
    else:
        recovery = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
        assert recovery["source_revision"] == source.assembly.revision


def test_previous_assembled_module_is_rechecked_and_failure_stays_pending(tmp_path, monkeypatch):
    from car2lego3d import assembly_schedule
    source, request, mission = setup_source(tmp_path)
    source.module_bindings = {"chassis": dict(roots=[], roles={})}
    source.save()
    schedule = json.loads((source.directory / "module-tasks.json").read_text())
    schedule["tasks"] = dict(chassis=dict(status="assembled", attempts=[dict(status="module_checked")]))
    write_json(source.directory / "module-tasks.json", schedule)
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    recovery = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    backend = type("Backend", (), {"name": "codex"})()
    scheduler = ModuleScheduler(target, backend, mission, {})
    seen = []

    def check(tools, name):
        seen.append(name)
        return dict(status="fail", failures=["current_checker_failed"])

    monkeypatch.setattr(assembly_schedule, "module_check", check)
    recheck_seed(scheduler, recovery)
    assert seen == ["chassis"]
    assert scheduler.tasks["chassis"]["status"] == "pending"
    assert scheduler.tasks["chassis"]["measurement"]["failures"] == ["current_checker_failed"]
    assert scheduler.tasks["chassis"]["reason"] == "current_checker_rejected_previous_module"
    assert not recovery["carried_modules"]
    assert recovery["destination_input_hash"] != "old-input"


@pytest.mark.parametrize("unfinished", [True, False])
def test_recovery_quarantines_only_unfinished_conflict_and_preserves_geometry_and_evidence(tmp_path, unfinished):
    source, request, mission = setup_source(tmp_path)
    nodes = source.assembly.graph()["instances"]
    binding = dict(roots=[nodes[0]["id"]], roles=dict(structure=[nodes[1]["id"]]))
    source.module_bindings = dict(chassis=binding, front=dict(roots=binding["roots"],
        roles=dict(body=binding["roles"]["structure"])))
    source.save()
    schedule_path = source.directory / "module-tasks.json"
    schedule = json.loads(schedule_path.read_text())
    schedule["tasks"] = dict(chassis=dict(status="assembled"),
        front=dict(status="running" if unfinished else "assembled"))
    write_json(schedule_path, schedule)
    before = {name: digest(source.directory / name) for name in ("state.json", "module-tasks.json")}
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    record = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    assert target.assembly.text() == source.assembly.text()
    assert target.assembly.joints == source.assembly.joints
    assert target.module_bindings["chassis"] == binding
    assert {name: digest(source.directory / name) for name in before} == before
    assert record["acceptance"] == "unverified" and not record["carried_modules"]
    if unfinished:
        assert "front" not in target.module_bindings
        entry, = record["quarantined_bindings"]
        assert entry["binding"] == source.module_bindings["front"] and entry["geometry_preserved"]
        assert entry["conflicts"][0]["owner"] == "chassis"
        assert target.assembly.revision == source.assembly.revision + 1
    else:
        assert target.module_bindings == source.module_bindings
        assert not record["quarantined_bindings"]
        assert target.assembly.revision == source.assembly.revision


@pytest.mark.parametrize("status", ["running", "interrupted", "blocked"])
def test_unfinished_module_candidates_are_preserved_and_rechecked(tmp_path, monkeypatch, status):
    from car2lego3d import assembly_schedule
    from car2lego3d.assembly_candidates import capture
    source, request, mission = setup_source(tmp_path)
    nodes = source.assembly.graph()["instances"]
    source.vehicle_contract = mission["vehicle_contract"]
    source.module_bindings["chassis"] = dict(roots=[nodes[0]["id"]], roles=dict(structure=[nodes[1]["id"]]))
    measurement = dict(status="pass", signature="source-signature", native_evidence=[])
    monkeypatch.setattr(assembly_schedule, "module_check", lambda *args, **kwargs: deepcopy(measurement))
    candidate = capture(source, source.assembly.revision, "chassis", "saved before interruption")
    schedule = json.loads((source.directory / "module-tasks.json").read_text())
    schedule["tasks"] = dict(chassis=dict(status=status, attempts=[dict(status=status)]))
    write_json(source.directory / "module-tasks.json", schedule)
    paths = [source.directory / "state.json", source.directory / "module-tasks.json",
        source.directory / "module-candidates/chassis" / candidate["id"] / "candidate.json"]
    before = {str(path): digest(path) for path in paths}
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    recovery = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    scheduler = ModuleScheduler(target, type("Backend", (), {"name": "codex"})(), mission, {})
    measurement.update(status="fail", failures=["new checker rejected candidate"])
    recheck_seed(scheduler, recovery)
    restored = target.directory / "module-candidates/chassis" / candidate["id"] / "candidate.json"
    record = json.loads(restored.read_text())
    assert record["measurement"]["status"] == "fail"
    assert record["recovered_from"]["sha256"] == before[str(paths[-1])]
    assert digest(restored.parent / "state.json") == candidate["snapshot_sha256"]
    assert recovery["rechecked_candidates"] == [dict(module="chassis", id=candidate["id"], status="fail")]
    assert not recovery["carried_modules"] and scheduler.tasks["chassis"]["status"] == "pending"
    assert {str(path): digest(path) for path in paths} == before


def native_completed_chassis(tmp_path):
    from car2lego3d.assembly_catalog import NativeCatalog
    from car2lego3d.assembly_candidates import capture
    from car2lego3d.assembly_schedule import module_check
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native part library not installed")
    source, request, mission = setup_source(tmp_path)
    source.catalog_root, source.catalog = native, NativeCatalog(native)
    source.vehicle_contract = mission["vehicle_contract"]
    chassis, brick = source.assembly.graph()["instances"]
    source.module_bindings["chassis"] = dict(roots=[chassis["id"]], roles=dict(structure=[brick["id"]]))
    for count in (1, 2, 3):
        if count > 1:
            matrix = np.eye(4)
            matrix[1, 3] = -24 * (count - 1)
            result = source.call("edit", dict(revision=source.assembly.revision, operations=[
                dict(op="add", parent=chassis["id"], part="3001.dat", matrix=matrix.tolist())]))
            assert result["ok"], result
        trial = capture(source, source.assembly.revision, "chassis", str(count))
        assert trial["measurement"]["status"] == "pass"
    measurement = module_check(source, "chassis")
    assert measurement["status"] == "pass"
    schedule_path = source.directory / "module-tasks.json"
    schedule = read_json(schedule_path)
    schedule["tasks"] = dict(chassis=dict(status="assembled", attempts=[], measurement=measurement),
        steering=dict(status="running", attempts=[]))
    write_json(schedule_path, schedule)
    result = source.call("edit", dict(revision=source.assembly.revision, operations=[
        dict(op="module", name="unfinished.ldr", alias="unfinished", matrix=np.eye(4).tolist()),
        dict(op="add", parent="@unfinished", part="3001.dat", matrix=np.eye(4).tolist())]))
    assert result["ok"], result
    source.save()
    assert module_check(source, "chassis")["status"] == "fail"
    return source, request, mission, measurement["signature"]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_recovery_withdraws_colliding_trial_without_changing_completed_chassis(tmp_path):
    from car2lego3d.assembly_schedule import module_signature
    source, request, mission, signature = native_completed_chassis(tmp_path)
    before = {str(path): digest(path) for path in source.directory.rglob("*") if path.is_file()}
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    recovery = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    scheduler = ModuleScheduler(target, type("Backend", (), {"name": "codex"})(), mission, {})
    recheck_seed(scheduler, recovery)
    assert recovery["safe_boundary"]["status"] == "restored"
    assert len(recovery["safe_boundary"]["removed_unfinished_instances"]) == 2
    assert recovery["carried_modules"] == ["chassis"]
    assert module_signature(target, "chassis") == signature
    assert target.assembly.revision == source.assembly.revision + 1
    assert scheduler.tasks["chassis"]["measurement"]["status"] == "pass"
    assert scheduler.tasks["steering"]["status"] == "pending"
    assert {str(path): digest(path) for path in source.directory.rglob("*") if path.is_file()} == before
    assert recovery["acceptance"] == "unverified"


def test_bad_native_wheel_layout_cannot_be_hidden_by_a_tyre_free_snapshot(tmp_path):
    source, request, mission, _ = native_completed_chassis(tmp_path)
    for side, y, z in (("left", -82, -200), ("right", 0, 200)):
        mount = np.eye(4)
        mount[:3, 3] = [600, y, z]
        result = source.call("edit", dict(revision=source.assembly.revision, operations=[
            dict(op="module", name=f"rear_{side}.ldr", alias="wheel", matrix=mount.tolist()),
            dict(op="add", parent="@wheel", part="2696.dat", alias="tyre", matrix=np.eye(4).tolist())]))
        assert result["ok"], result
        aliases = result["result"]["aliases"]
        source.module_bindings[f"wheel_rear_{side}"] = dict(roots=[aliases["wheel"]],
            roles=dict(tyre=[aliases["tyre"]]))
    source.save()
    before = {str(path): digest(path) for path in source.directory.rglob("*") if path.is_file()}
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    recovery = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    assert recovery["source_wheel_layout"]["status"] == "fail"
    assert any(issue.get("delta_mm") == pytest.approx(32.8)
        for issue in recovery["source_wheel_layout"]["issues"]
        if issue["code"] == "wheel_ground_contact_mismatch")
    seeded = digest(target.directory / "state.json")
    scheduler = ModuleScheduler(target, type("Backend", (), {"name": "codex"})(), mission, {})
    recheck_seed(scheduler, recovery)
    boundary = recovery["safe_boundary"]
    assert boundary["status"] == "unavailable"
    assert boundary["reason"] == "source_vehicle_layout_requires_structural_repair"
    assert not recovery["carried_modules"]
    assert scheduler.tasks["chassis"]["status"] == "pending"
    assert scheduler.tasks["chassis"]["measurement"]["status"] == "fail"
    assert digest(target.directory / "state.json") == seeded
    assert {str(path): digest(path) for path in source.directory.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("change", ["completed_geometry", "completed_binding", "missing_snapshot", "additional_completed"])
def test_recovery_cannot_discard_or_modify_completed_work_to_find_boundary(tmp_path, change):
    source, request, mission, _ = native_completed_chassis(tmp_path)
    if change == "completed_geometry":
        brick = source.module_bindings["chassis"]["roles"]["structure"][0]
        assert source.call("edit", dict(revision=source.assembly.revision, operations=[
            dict(op="color", instance=brick, color=14)]))["ok"]
    elif change == "completed_binding":
        source.module_bindings["chassis"]["roles"]["structure"].append(
            next(n["id"] for n in source.assembly.graph()["instances"] if n["kind"] == "part"
                and n["id"] not in source.module_bindings["chassis"]["roles"]["structure"]))
    elif change == "missing_snapshot":
        (source.directory / "module-candidates").rename(source.directory / "preserved-candidates")
    else:
        from car2lego3d.assembly_schedule import module_signature
        root = next(n["id"] for n in source.assembly.graph()["instances"]
            if n["kind"] == "module" and n["id"] not in source.module_bindings["chassis"]["roots"])
        part = next(n["id"] for n in source.assembly.graph()["instances"] if n["parent"] == root)
        source.module_bindings["front"] = dict(roots=[root], roles=dict(body=[part]))
        schedule = read_json(source.directory / "module-tasks.json")
        schedule["tasks"]["front"] = dict(status="assembled", attempts=[],
            measurement=dict(signature=module_signature(source, "front")))
        write_json(source.directory / "module-tasks.json", schedule)
    source.save()
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    recovery = seed_checkpoint(target, source.directory, mission, "codex", "snapshot", request)
    before = digest(target.directory / "state.json")
    scheduler = ModuleScheduler(target, type("Backend", (), {"name": "codex"})(), mission, {})
    recheck_seed(scheduler, recovery)
    assert recovery["safe_boundary"]["status"] == "unavailable"
    assert digest(target.directory / "state.json") == before
    assert not recovery["carried_modules"]
    assert scheduler.tasks["chassis"]["status"] == "pending"


def test_no_edit_failed_recovery_keeps_completion_intent_but_rechecks_it(tmp_path):
    from car2lego3d.assembly_recovery import completed_history
    source, request, mission, _ = native_completed_chassis(tmp_path)
    failed = AssemblyTools(tmp_path / "failed", source.catalog_root)
    previous = seed_checkpoint(failed, source.directory, mission, "codex", "snapshot", request)
    schedule = read_json(source.directory / "module-tasks.json")
    schedule["tasks"]["chassis"] = dict(status="running", attempts=[])
    write_json(failed.directory / "module-tasks.json", schedule)
    assert digest(failed.directory / "state.json") == previous["source_files"]["state.json"]
    tasks, history = completed_history(failed.directory, schedule)
    assert tasks["chassis"]["status"] == "assembled"
    assert history[0]["recovered_completion_intent"] == ["chassis"]
    for path in (source.directory / "module-candidates").rglob("*"):
        if path.is_file():
            destination = failed.directory / path.relative_to(source.directory)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
    target = AssemblyTools(tmp_path / "new", source.catalog_root)
    recovery = seed_checkpoint(target, failed.directory, mission, "codex", "snapshot", request)
    scheduler = ModuleScheduler(target, type("Backend", (), {"name": "codex"})(), mission, {})
    recheck_seed(scheduler, recovery)
    assert recovery["safe_boundary"]["status"] == "restored"
    assert recovery["carried_modules"] == ["chassis"]
    failed.assembly.revision += 1
    failed.save()
    tasks, history = completed_history(failed.directory, schedule)
    assert tasks["chassis"]["status"] == "running" and not history
