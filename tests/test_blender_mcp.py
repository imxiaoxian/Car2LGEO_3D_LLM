from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import json
import runpy
import socket

import pytest

from car2lego3d import blender_mcp
from car2lego3d.common import ROOT, write_json


def test_mcp_uses_isolated_interpreter_from_local_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("CAR2LEGO_MCP_PYTHON", raising=False)
    def run(command, **kwargs):
        assert command[0] == "isolated-python"
        write_json(tmp_path / "test-mcp-response.json", dict(result=dict(content=[dict(type="text", text="OK")])))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    with patch.object(blender_mcp, "configuration", return_value=(tmp_path, "shared-python")), \
            patch("dotenv.dotenv_values", return_value={"CAR2LEGO_MCP_PYTHON": "isolated-python"}), \
            patch.object(blender_mcp, "upstream", return_value=tmp_path), \
            patch.object(blender_mcp.subprocess, "run", side_effect=run):
        blender_mcp.call("test", {}, tmp_path, "test")


def test_mcp_timeout_is_forwarded_to_worker_and_process_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("CAR2LEGO_MCP_TIMEOUT", "601")
    def run(command, **kwargs):
        assert command[-2:] == ["--timeout", "601.0"]
        assert kwargs["timeout"] == 631.0
        write_json(tmp_path / "test-mcp-response.json", {
            "result": {"content": [{"type": "text", "text": "OK"}]}
        })
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    with patch.object(blender_mcp, "configuration", return_value=(tmp_path, "shared-python")), \
            patch.object(blender_mcp, "upstream", return_value=tmp_path), \
            patch.object(blender_mcp.subprocess, "run", side_effect=run):
        blender_mcp.call("test", {}, tmp_path, "test")


def test_upstream_socket_budget_is_isolated_from_standard_library():
    launcher = runpy.run_path(str(ROOT / "scripts/blender_mcp_server.py"))
    server = SimpleNamespace(socket=socket)
    launcher["configure_socket_timeout"](server, 601)
    with server.socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(180)
        assert connection.gettimeout() == 601
    with socket.socket() as other_connection:
        other_connection.settimeout(180)
        assert other_connection.gettimeout() == 180


def test_blender_preferences_stay_in_run_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("CAR2LEGO_BLENDER", "test-blender")
    probe, process = MagicMock(), MagicMock()
    probe.__enter__.return_value = probe
    probe.connect_ex.side_effect = [1, 0]
    process.poll.return_value = None
    with patch.object(blender_mcp.socket, "socket", return_value=probe), \
            patch.object(blender_mcp, "upstream", return_value=tmp_path), \
            patch("car2lego3d.gpu_lock.GPULock") as gpu_lock, \
            patch.object(blender_mcp.subprocess, "Popen", return_value=process) as launch:
        with blender_mcp.BlenderSession(tmp_path):
            assert launch.call_args.kwargs["env"]["BLENDER_USER_CONFIG"] == str((tmp_path / "blender-user-config").resolve())
        process.terminate.assert_called_once()
        gpu_lock.assert_called_once_with(purpose="blender", timeout=600)
        gpu_lock.return_value.__enter__.assert_called_once()
        gpu_lock.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("code", ["import os", "from . import unsafe", "open('file')", "bpy.ops.wm.open_mainfile()"])
def test_generated_code_rejects_unsupported_host_operations(code):
    with pytest.raises(ValueError):
        blender_mcp.validate_code(code)


def test_modelling_repairs_and_visual_review_use_same_model(tmp_path):
    addon = tmp_path / "addon.py"
    addon.write_text("# Constructed addon fixture", encoding="utf-8")
    session = MagicMock()
    session.__enter__.return_value = session

    def execute(code, name):
        if name == "model-1":
            raise RuntimeError("Constructed Blender execution failure")

    session.execute.side_effect = execute
    responses = [dict(analysis="Constructed model", code="import bpy"),
                 dict(analysis="Constructed repair", code="import bpy"),
                 dict(analysis="Constructed review", code="")]
    with patch.object(blender_mcp, "modelling_model", return_value="gpt-6-astra"), \
            patch.object(blender_mcp, "load_views", return_value=({"views": {}}, {})), \
            patch.object(blender_mcp, "load_spec", return_value={}), \
            patch.object(blender_mcp, "spec_record", return_value={}), \
            patch.object(blender_mcp, "image_content", return_value=[]), \
            patch.object(blender_mcp, "digest", return_value="test-hash"), \
            patch.object(blender_mcp, "upstream", return_value=tmp_path), \
            patch.object(blender_mcp, "render_code", return_value="host export"), \
            patch.object(blender_mcp, "assess_model", return_value={"status": "pass", "failed_checks": 0}), \
            patch.object(blender_mcp, "BlenderSession", return_value=session), \
            patch.object(blender_mcp, "ask_json", side_effect=responses) as ask:
        output = tmp_path / "output"
        blender_mcp.reconstruct(tmp_path / "views.json", output, tmp_path / "spec.json")
    assert [c.args[2] for c in ask.call_args_list] == ["model", "repair-1", "visual-review"]
    assert all(c.kwargs["model"] == "gpt-6-astra" for c in ask.call_args_list)
    assert all(c.kwargs["timeout"] == 180 and c.kwargs["stream"] for c in ask.call_args_list)
    assert all(c.kwargs["reasoning_effort"] == "medium" and c.kwargs["transient_retries"] == 2
               for c in ask.call_args_list)
    record = json.loads((output / "reconstruction.json").read_text(encoding="utf-8"))
    assert record["model"] == record["inputs"]["model"] == "gpt-6-astra"


def test_review_restores_scene_when_patch_fixes_windows_but_destroys_body(tmp_path):
    (tmp_path / "addon.py").write_text("# Constructed fixture")
    output = tmp_path / "output"
    session = MagicMock()
    session.__enter__.return_value = session
    before = dict(status="needs_revision", failed_checks=2, checks=[
        dict(name="left_window", passed=False), dict(name="right_window", passed=False),
        dict(name="body_width", passed=True)])
    after = dict(status="needs_revision", failed_checks=1, checks=[
        dict(name="left_window", passed=True), dict(name="right_window", passed=True),
        dict(name="body_width", passed=False)])

    def execute(code, name):
        if name in {"export-render", "refined-export-render"}:
            content = b"original-scene" if name == "export-render" else b"broken-scene"
            (output / "model.blend").write_bytes(content)
            (output / "render-front.png").write_bytes(content)
            write_json(output / "model-quality.json", before if name == "export-render" else after)
        if name == "restore":
            assert (output / "model.blend").read_bytes() == b"original-scene"
    session.execute.side_effect = execute
    with patch.object(blender_mcp, "modelling_model", return_value="gpt-6-astra"), \
            patch.object(blender_mcp, "load_views", return_value=({"views": {}}, {})), \
            patch.object(blender_mcp, "load_spec", return_value={}), \
            patch.object(blender_mcp, "spec_record", return_value={}), \
            patch.object(blender_mcp, "image_content", return_value=[]), \
            patch.object(blender_mcp, "digest", return_value="test-hash"), \
            patch.object(blender_mcp, "upstream", return_value=tmp_path), \
            patch.object(blender_mcp, "render_code", return_value="host export"), \
            patch.object(blender_mcp, "assess_model", side_effect=[before, after]), \
            patch.object(blender_mcp, "BlenderSession", return_value=session), \
            patch.object(blender_mcp, "ask_json", side_effect=[dict(analysis="Fixture", code="import bpy"),
                                                            dict(analysis="Bad patch", code="import bpy")]):
        blender_mcp.reconstruct(tmp_path / "views.json", output, tmp_path / "spec.json")
    assert (output / "render-front.png").read_bytes() == b"original-scene"
    assert (output / "rejected-review/model.blend").read_bytes() == b"broken-scene"
    selection = json.loads((output / "review-selection.json").read_text())
    assert selection["selected"] == "before-review"
    assert "body_width" in selection["reason"]
