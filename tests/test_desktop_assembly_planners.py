"""Website routing fixtures; these do not run models or certify car quality."""
from pathlib import Path

from PIL import Image
import pytest

from car2lego3d.common import digest, write_json
from car2lego3d.desktop import DesktopStore, assembly_mission, read
from car2lego3d.web import JobStore


def test_shared_mission_is_identical_for_independent_planners():
    design = dict(brand="BMW", model="M4", year=None, variant="", scale=None,
                  scale_mode="auto", custom_request="Smooth body", color=4, seed=42,
                  reconstruction_backend="gpt-blender")
    left = dict(design, assembly_planner="codex", created_at="first",
                parent_design_id="a" * 32, reuse_library=False)
    right = dict(design, assembly_planner="local_finetuned", created_at="second",
                 parent_design_id="b" * 32, reuse_library=True)
    assert assembly_mission(left) == assembly_mission(right) == design
    assert left["assembly_planner"] == "codex"
    assert right["assembly_planner"] == "local_finetuned"


def test_explicit_fresh_generation_bypasses_existing_archive(tmp_path, monkeypatch):
    desktop = DesktopStore(JobStore(tmp_path / "jobs", None))
    monkeypatch.setattr(desktop, "run", lambda identity: None)
    result = desktop.submit(dict(make="Lamborghini", model="Countach", reuse_library=False))
    desktop.worker.join(5)
    assert result["id"]
    assert read(desktop.directory(result["id"]) / "request.json")["reuse_library"] is False


@pytest.mark.parametrize("backend,planner", [("hunyuan-mv", "legacy"), ("hybrid", "codex")])
def test_explicit_reconstruction_routing_is_persisted(tmp_path, monkeypatch, backend, planner):
    desktop = DesktopStore(JobStore(tmp_path / "jobs", None))
    monkeypatch.setattr(desktop, "run", lambda identity: None)
    result = desktop.submit(dict(make="BMW", model="M4", reuse_library=False,
        reconstruction_backend=backend, assembly_planner=planner))
    desktop.worker.join(5)
    assert read(desktop.directory(result["id"]) / "request.json")["reconstruction_backend"] == backend


@pytest.mark.parametrize("backend,planner", [("hunyuan-mv", "codex"), ("hybrid", "legacy")])
def test_incompatible_reconstruction_and_planner_fail_before_job_creation(tmp_path, backend, planner):
    desktop = DesktopStore(JobStore(tmp_path / "jobs", None))
    with pytest.raises(ValueError):
        desktop.submit(dict(make="BMW", model="M4", reconstruction_backend=backend, assembly_planner=planner))
    assert not list(desktop.root.glob("*/request.json"))


@pytest.mark.parametrize("selected", ["codex", "local_finetuned"])
def test_submission_persists_explicit_planner_over_process_default(
        tmp_path, monkeypatch, selected):
    monkeypatch.setenv("ASSEMBLY_PLANNER",
                       "local_finetuned" if selected == "codex" else "codex")
    desktop = DesktopStore(JobStore(tmp_path / "jobs", None))
    monkeypatch.setattr(desktop, "run", lambda identity: None)
    result = desktop.submit(dict(make="Ferrari", model="F40",
        customization_text="Constructed routing test", assembly_planner=selected))
    desktop.worker.join(5)
    request = read(desktop.directory(result["id"]) / "request.json")
    assert request["assembly_planner"] == result["assembly_planner"] == selected
    assert request["scale"] is None and request["scale_mode"] == "auto"
    assert request["year"] is None


@pytest.mark.parametrize("selected", ["codex", "local_finetuned"])
@pytest.mark.parametrize("agent_succeeds", [True, False])
def test_selected_assembly_planner_runs_independently(
        tmp_path, ready_research, monkeypatch, selected, agent_succeeds):
    alternate = "local_finetuned" if selected == "codex" else "codex"
    monkeypatch.setenv("ASSEMBLY_PLANNER", alternate)
    research, _ = ready_research()
    desktop = DesktopStore(JobStore(tmp_path / "jobs", research))
    job = "f" * 32
    directory = desktop.root / job
    artifacts = directory / "artifacts"
    request = dict(brand="Ferrari", model="F40", year=None, scale=None,
                   scale_mode="auto", color=None, assembly_planner=selected,
                   reuse_library=False)
    write_json(directory / "request.json", request)
    write_json(directory / "status.json", dict(status="pending"))
    commands, geometry_calls = [], []

    def command(identity, arguments):
        assert identity == job
        args = [str(arg) for arg in arguments]
        commands.append(args)
        output = Path(args[args.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        if "image" in args:
            views = {}
            for index, view in enumerate(("front", "back", "left", "right")):
                image = output / f"{view}.png"
                Image.new("RGB", (32, 32), (index * 60, 20, 30)).save(image)
                views[view] = dict(file=image.name, sha256=digest(image))
            write_json(output / "views.json", dict(schema_version=1, views=views))
        elif "reconstruct" in args:
            (output / "calibrated.glb").write_bytes(b"constructed mesh fixture")
            write_json(output / "semantic-scene.json", dict(modules=[]))
        elif "car2lego3d.assembly_website" in args:
            assert args[args.index("--planner") + 1] == selected
            for flag, expected in (
                    ("--request", directory / "assembly-mission.json"),
                    ("--semantic", artifacts / "02-mesh/semantic-scene.json"),
                    ("--references", artifacts / "01-image/views.json"),
                    ("--specifications", directory / "vehicle-specs.json"),
                    ("--publish-artifacts", artifacts)):
                assert Path(args[args.index(flag) + 1]) == expected
            if not agent_succeeds:
                raise ValueError(f"{selected} failed: retained independent diagnosis")
            write_json(output / "agent.json", dict(planner=selected, export=dict(ok=True)))
            studio = artifacts / "04-studio/model.io"
            studio.parent.mkdir(parents=True, exist_ok=True)
            studio.write_bytes(b"constructed archive fixture")
        else:
            pytest.fail(f"Unexpected fallback or chained planner command: {args}")

    monkeypatch.setattr(desktop, "command", command)
    monkeypatch.setattr(desktop, "geometry", lambda identity: geometry_calls.append(identity))
    desktop.active = job
    desktop.run(job)

    state = desktop.state(job)
    assert state["status"] == ("draft" if agent_succeeds else "failed")
    assert len(commands) == 3
    assert geometry_calls == ([job] if agent_succeeds else [])
    assert read(directory / "request.json")["assembly_planner"] == selected
    mission = read(directory / "assembly-mission.json")
    assert "assembly_planner" not in mission
    assert mission["model"] == "F40" and mission["scale_mode"] == "auto"
    assert desktop.active is None
    if not agent_succeeds:
        assert selected in state["error_message"]
        assert not (artifacts / "04-studio/model.io").exists()


def test_missing_finetuned_service_does_not_construct_codex(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent

    monkeypatch.delenv("ASSEMBLY_LOCAL_FINETUNED_URL", raising=False)

    def unexpected(*args, **kwargs):
        pytest.fail("A missing local model must not switch to Codex")

    monkeypatch.setattr(assembly_agent, "CodexPlanner", unexpected)
    with pytest.raises(ValueError, match="localhost"):
        assembly_agent.planner("local_finetuned", tmp_path)


def test_missing_codex_does_not_construct_local_model(tmp_path, monkeypatch):
    from car2lego3d import assembly_agent

    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(assembly_agent.shutil, "which", lambda name: None)

    def unexpected(*args, **kwargs):
        pytest.fail("A missing Codex must not switch to the local model")

    monkeypatch.setattr(assembly_agent, "LocalPlanner", unexpected)
    with pytest.raises(ValueError, match="not configured"):
        assembly_agent.planner("codex", tmp_path)


def test_cancelled_assembly_can_resume_repeatedly_after_host_takes_ownership(tmp_path, monkeypatch):
    from car2lego3d.assembly_recovery import record_launch
    desktop = DesktopStore(JobStore(tmp_path, None))
    identity = "a" * 32
    directory = tmp_path / identity
    artifacts = directory / "artifacts"
    paths = dict(request=directory / "request.json", semantic=artifacts / "02-mesh/semantic-scene.json",
        references=artifacts / "01-image/views.json", specifications=directory / "vehicle-specs.json")
    for path in paths.values():
        write_json(path, {})
    record_launch(dict(paths, output=artifacts / "03-lego/agent", publish_artifacts=artifacts,
        backend="codex", snapshot="frozen", corpus_root=tmp_path / "corpus", native_root=tmp_path / "native",
        context_profile="production", max_steps=160), {str(path.resolve()): digest(path) for path in paths.values()})
    calls = []

    def command(job, args):
        calls.append(list(map(str, args)))
        raise ValueError("Retained module diagnostic")

    monkeypatch.setattr(desktop, "command", command)
    monkeypatch.setattr(desktop, "run", lambda *args: pytest.fail("Must not regenerate references"))
    for _ in range(2):
        write_json(directory / "status.json", dict(status="cancelled"))
        desktop.control(identity, "resume")
        desktop.worker.join(5)
        assert desktop.active is None
        assert desktop.state(identity)["status"] == "failed"
        assert read(directory / "runtime.json")["status"] == "stopped"
    assert len(calls) == 2
    assert all("car2lego3d.assembly_website" in command and "--snapshot" in command for command in calls)
