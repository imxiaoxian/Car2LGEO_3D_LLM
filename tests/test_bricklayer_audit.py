"""Exercise the real external validator and failure boundaries of its worker."""
from importlib import metadata
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from car2lego3d import bricklayer_audit as audit


def brick(x=0, y=0, part="3001.dat"):
    return f"1 4 {x} {y} 0 1 0 0 0 1 0 0 0 1 {part}\n"


@pytest.mark.parametrize("text,status,gate", [
    (brick() + brick(y=-24), "pass", "connectivity"),
    (brick() + brick(), "fail", "collisions"),
    (brick() + brick(x=200, y=-100), "fail", "ground_support"),
    (brick() + brick(x=200), "fail", "connectivity"),
])
def test_real_sdk_distinguishes_connections_collisions_support_and_components(text, status, gate):
    pytest.importorskip("bricklayer")
    result = audit.inspect_text("0 Fixture\n" + text)
    assert result["status"] == status
    assert result["complete"] and result["expected_parts"] == result["imported_parts"] == 2
    assert result["gates"][gate] == ("pass" if status == "pass" else "fail")
    if gate == "collisions":
        assert result["problem_parts"]["0"]["part"] == "3001.dat"
        assert result["problem_parts"]["0"]["position_ldu"] == [0, 0, 0]


def test_partial_import_cannot_pass_even_when_supported_subset_is_valid():
    pytest.importorskip("bricklayer")
    result = audit.inspect_text("0 Fixture\n" + brick() + brick(x=200, part="no-such-part.dat"))
    assert result["status"] != "pass" and not result["complete"]
    assert result["expected_parts"] == 2 and result["imported_parts"] == 1
    assert result["warnings"] and result["index_space"] == "imported_subset_only"


def test_actual_worker_checks_source_without_modifying_it(tmp_path):
    pytest.importorskip("bricklayer")
    source, output = tmp_path / "model.ldr", tmp_path / "audit.json"
    content = ("0 Fixture\n" + brick() + brick(y=-24)).encode()
    source.write_bytes(content)
    result = audit.audit_ldraw(source, output, mode="required")
    assert result["status"] == "pass", result
    assert result["physical_verified"] is False and result["appearance_accepted"] is False
    assert source.read_bytes() == content
    assert json.loads(output.read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("mode,enabled", [("off", False), ("auto", False), ("required", True)])
def test_missing_sdk_is_explicit_and_cannot_pass(tmp_path, monkeypatch, mode, enabled):
    source = tmp_path / "model.ldr"
    source.write_text(brick())
    def unavailable(*args):
        raise metadata.PackageNotFoundError("bricklayer-sdk")
    monkeypatch.setattr(audit.metadata, "version", unavailable)
    result = audit.audit_ldraw(source, tmp_path / "audit.json", mode=mode)
    assert result["status"] == "unverified" and not result["complete"]
    assert result["enabled"] is enabled and result["available"] is False
    assert result["enforced"] is (mode == "required")


@pytest.mark.parametrize("failure", ["timeout", "crash", "source_changed"])
def test_worker_failures_and_changed_input_never_reuse_a_pass(tmp_path, monkeypatch, failure):
    source = tmp_path / "model.ldr"
    source.write_text(brick())
    monkeypatch.setattr(audit.metadata, "version", lambda name: "fixture")
    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "crash":
            return SimpleNamespace(returncode=1, stderr=b"native worker crash")
        source.write_text(brick(x=200))
        Path(command[command.index("--output") + 1]).write_text(json.dumps(dict(status="pass", complete=True)))
        return SimpleNamespace(returncode=0, stderr=b"")
    monkeypatch.setattr(audit.subprocess, "run", run)
    result = audit.audit_ldraw(source, tmp_path / "audit.json", mode="required", timeout=.01)
    assert result["status"] == "unverified" and result["complete"] is False
    assert result["reason"] == {"timeout": "worker_timeout", "crash": "worker_failed",
        "source_changed": "source_changed_during_audit"}[failure]


def test_source_cannot_be_overwritten_by_report(tmp_path):
    source = tmp_path / "model.ldr"
    source.write_text(brick())
    with pytest.raises(ValueError, match="overwrite"):
        audit.audit_ldraw(source, source)
    assert source.read_text() == brick()


@pytest.mark.parametrize("mode,blocked", [("auto", False), ("required", True)])
def test_studio_export_preserves_diagnostics_and_enforces_only_requested_mode(tmp_path, monkeypatch, mode, blocked):
    pytest.importorskip("bricklayer")
    from car2lego3d.studio import export_studio
    source = tmp_path / "model.ldr"
    source.write_text("0 Disconnected fixture\n" + brick() + brick(x=200))
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", mode)
    directory = tmp_path / "export"
    if blocked:
        with pytest.raises(ValueError, match="BrickLayer"):
            export_studio(source, directory)
        assert not (directory / "model.io").exists()
    else:
        assert export_studio(source, directory).is_file()
    report = json.loads((directory / "bricklayer-audit.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail" and report["component_count"] == 2
    assert report["enforced"] is blocked
