import os
import json

import pytest

from car2lego3d import job_runtime
from car2lego3d.common import write_json
from car2lego3d.desktop import DesktopStore
from car2lego3d.web import JobStore


def test_process_identity_matches_live_process_and_rejects_reused_pid():
    owner = job_runtime.current_owner()
    assert owner["pid"] == os.getpid() and owner["identity"]
    assert job_runtime.alive(owner)
    assert not job_runtime.alive(dict(owner, identity="not-this-process"))
    assert job_runtime.process_identity(-1) is None


def test_desktop_assembly_child_obeys_pause_cancel_and_owner_changes(tmp_path, monkeypatch):
    import time
    request = tmp_path / "request.json"
    write_json(request, {})
    job_runtime.save_runtime(tmp_path / "runtime.json", "host-session", status="running")
    write_json(tmp_path / "status.json", dict(status="paused"))
    checkpoint = job_runtime.desktop_assembly_checkpoint(request)
    waits = []

    def unpause(seconds):
        waits.append(seconds)
        write_json(tmp_path / "status.json", dict(status="processing"))

    monkeypatch.setattr(time, "sleep", unpause)
    checkpoint()
    assert waits == [.25]
    write_json(tmp_path / "status.json", dict(status="cancelled"))
    with pytest.raises(job_runtime.AssemblyCancelled):
        checkpoint()
    job_runtime.save_runtime(tmp_path / "runtime.json", "replacement", status="running")
    with pytest.raises(RuntimeError, match="ownership"):
        checkpoint()


def test_orphaned_processing_job_is_resumable_failure_without_erasing_evidence(tmp_path):
    desktop = DesktopStore(JobStore(tmp_path, None))
    identity = "d" * 32
    directory = tmp_path / identity
    write_json(directory / "request.json", dict(brand="Ferrari", model="F40"))
    write_json(directory / "status.json", dict(status="processing", stage="reconstruction", progress=45))
    original = (directory / "status.json").read_bytes()
    result = desktop.state(identity)
    assert result["status"] == "failed" and "恢复" in result["error_message"]
    assert (directory / "status.json").read_bytes() == original


def test_another_live_owner_cannot_be_resubmitted(tmp_path, monkeypatch):
    import pytest
    desktop = DesktopStore(JobStore(tmp_path, None))
    identity = "e" * 32
    directory = tmp_path / identity
    write_json(directory / "status.json", dict(status="processing", stage="reference_images"))
    job_runtime.save_runtime(directory / "runtime.json", "another-session", status="running")
    assert desktop.state(identity)["status"] == "processing"
    with pytest.raises(RuntimeError, match="另一个"):
        desktop.control(identity, "resume")
    monkeypatch.setattr(job_runtime, "process_identity", lambda pid: "reused-pid")
    assert desktop.state(identity)["status"] == "failed"


def test_orphan_child_blocks_duplicate_execution(tmp_path):
    import pytest
    desktop = DesktopStore(JobStore(tmp_path, None))
    identity = "f" * 32
    write_json(tmp_path / identity / "status.json", dict(status="processing", stage="reference_images"))
    write_json(tmp_path / identity / "runtime.json", dict(status="running",
        owner=dict(pid=-1, identity="dead"), child=job_runtime.current_owner()))
    assert desktop.state(identity)["status"] == "processing"
    with pytest.raises(RuntimeError):
        desktop.control(identity, "resume")
    with pytest.raises(RuntimeError):
        desktop.assert_idle()


def assembly_job(tmp_path):
    directory = tmp_path / ("a" * 32)
    request = directory / "request.json"
    write_json(request, dict(brand="Lamborghini", model="Countach"))
    write_json(directory / "status.json", dict(status="failed", stage="reconstruction", error="old failure"))
    write_json(directory / "desktop.json", dict(artifacts="artifacts", recovery="historical evidence"))
    artifacts = directory / "revisions/new/artifacts"
    return directory, request, artifacts


def test_external_assembly_selects_its_revision_and_preserves_previous_failure(tmp_path):
    directory, request, artifacts = assembly_job(tmp_path)
    original = {name: json.loads((directory / name).read_text()) for name in ("status.json", "desktop.json")}
    record = job_runtime.register_assembly(request, artifacts / "03-lego/agent", artifacts)
    desktop = DesktopStore(JobStore(tmp_path, None))
    assert desktop.artifacts(directory.name) == artifacts
    assert desktop.state(directory.name)["status"] == "processing"
    assert desktop.state(directory.name)["stage"] == "lego"
    history = json.loads((directory / record["previous_execution"]).read_text())
    assert history["status"] == original["status.json"]
    assert history["desktop"] == original["desktop.json"]
    assert desktop.metadata(directory.name)["recovery"] == "historical evidence"
    with pytest.raises(ValueError, match="不能重复启动"):
        desktop.control(directory.name, "resume")


def test_external_assembly_cannot_claim_cross_job_paths_or_dead_owner(tmp_path):
    directory, request, artifacts = assembly_job(tmp_path)
    original = (directory / "status.json").read_bytes()
    for output, publish in [(artifacts / "wrong-output", artifacts),
            (tmp_path / "outside/03-lego/agent", tmp_path / "outside")]:
        with pytest.raises(ValueError, match="artifact"):
            job_runtime.register_assembly(request, output, publish)
    with pytest.raises(ValueError, match="alive"):
        job_runtime.register_assembly(request, artifacts / "03-lego/agent", artifacts,
            owner=dict(pid=-1, identity="dead"))
    assert (directory / "status.json").read_bytes() == original
    assert not (directory / "runtime.json").exists()


def test_external_assembly_cannot_steal_a_live_job(tmp_path):
    directory, request, artifacts = assembly_job(tmp_path)
    job_runtime.save_runtime(directory / "runtime.json", "desktop-host", status="running")
    original = (directory / "runtime.json").read_bytes()
    with pytest.raises(RuntimeError, match="live worker"):
        job_runtime.register_assembly(request, artifacts / "03-lego/agent", artifacts)
    assert (directory / "runtime.json").read_bytes() == original


def test_desktop_child_keeps_host_ownership_including_windows_venv_launcher(tmp_path, monkeypatch):
    directory, request, artifacts = assembly_job(tmp_path)
    write_json(directory / "desktop.json", dict(artifacts="revisions/new/artifacts"))
    parent = dict(pid=os.getppid(), identity="launcher")
    write_json(directory / "runtime.json", dict(status="running", session="desktop-host", child=parent))
    original = (directory / "runtime.json").read_bytes()
    real_identity = job_runtime.process_identity
    monkeypatch.setattr(job_runtime, "process_identity",
        lambda pid: "launcher" if pid == parent["pid"] else real_identity(pid))
    assert job_runtime.register_assembly(request, artifacts / "03-lego/agent", artifacts) is None
    assert (directory / "runtime.json").read_bytes() == original


def test_finished_external_run_does_not_reuse_prior_export_or_certify_a_draft(tmp_path, monkeypatch):
    directory, request, artifacts = assembly_job(tmp_path)
    result = artifacts / "03-lego/agent/agent.json"
    old = dict(schema="assembly-scheduled-agent.v1", status="draft", export=dict(ok=True), elapsed_seconds=1)
    write_json(result, old)
    record = job_runtime.register_assembly(request, result.parent, artifacts)
    desktop = DesktopStore(JobStore(tmp_path, None))
    monkeypatch.setattr(job_runtime, "process_identity", lambda pid: None)
    assert desktop.state(directory.name)["status"] == "failed"
    write_json(result, dict(old, elapsed_seconds=2))
    state = desktop.state(directory.name)
    assert state["status"] == "draft" and state["error_message"] is None
    job_runtime.finish_assembly(request, record)
    assert json.loads((directory / "runtime.json").read_text())["status"] == "stopped"
    assert desktop.state(directory.name)["status"] == "draft"
    write_json(request, dict(brand="Ferrari", model="F40"))
    assert job_runtime.assembly_state(directory, record) is None


def test_external_runtime_exception_records_failed_checkpoint(tmp_path):
    directory, request, artifacts = assembly_job(tmp_path)
    record = job_runtime.register_assembly(request, artifacts / "03-lego/agent", artifacts)
    job_runtime.finish_assembly(request, record, ValueError("module blocked"))
    state = json.loads((directory / "status.json").read_text())
    assert state["status"] == "failed" and state["error"] == "module blocked"
    desktop = DesktopStore(JobStore(tmp_path, None))
    assert desktop.state(directory.name)["error_message"] == "module blocked"


def test_bound_worker_pause_resume_cancel_and_duplicate_rejection(tmp_path):
    directory, request, artifacts = assembly_job(tmp_path)
    record = job_runtime.register_assembly(request, artifacts / "03-lego/agent", artifacts)
    desktop = DesktopStore(JobStore(tmp_path, None))
    assert job_runtime.controllable_assembly(directory, record)
    assert desktop.control(directory.name, "pause")["status"] == "paused"
    assert desktop.control(directory.name, "resume")["status"] == "processing"
    job_runtime.assembly_checkpoint(request, record)
    with pytest.raises(ValueError, match="不能重复启动"):
        desktop.control(directory.name, "retry")
    assert desktop.control(directory.name, "cancel")["status"] == "cancelled"
    with pytest.raises(job_runtime.AssemblyCancelled) as error:
        job_runtime.assembly_checkpoint(request, record)
    job_runtime.finish_assembly(request, record, error.value)
    assert desktop.state(directory.name)["status"] == "cancelled"


def test_checkpoint_waits_for_resume_and_rejects_rebound_inputs(tmp_path, monkeypatch):
    import time
    directory, request, artifacts = assembly_job(tmp_path)
    record = job_runtime.register_assembly(request, artifacts / "03-lego/agent", artifacts)
    write_json(directory / "status.json", dict(status="paused"))
    waits = []

    def resume(delay):
        waits.append(delay)
        write_json(directory / "status.json", dict(status="processing"))

    monkeypatch.setattr(time, "sleep", resume)
    job_runtime.assembly_checkpoint(request, record)
    assert waits == [0.25]
    write_json(request, dict(model="changed"))
    assert not job_runtime.controllable_assembly(directory, record)
    with pytest.raises(RuntimeError, match="ownership or bound inputs changed"):
        job_runtime.assembly_checkpoint(request, record)
    desktop = DesktopStore(JobStore(tmp_path, None))
    with pytest.raises(RuntimeError, match="另一个"):
        desktop.control(directory.name, "cancel")


def test_checkpoint_rejects_replaced_session(tmp_path):
    directory, request, artifacts = assembly_job(tmp_path)
    record = job_runtime.register_assembly(request, artifacts / "03-lego/agent", artifacts)
    write_json(directory / "runtime.json", dict(record, session="replacement"))
    with pytest.raises(RuntimeError, match="ownership or bound inputs changed"):
        job_runtime.assembly_checkpoint(request, record)
