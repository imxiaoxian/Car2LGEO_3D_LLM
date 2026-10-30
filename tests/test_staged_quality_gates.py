from contextlib import contextmanager

import pytest

from car2lego3d.staged_model import checkpoint_identity, phase_quality_issues, require_phase_quality


def quality(*names):
    return dict(checks=[dict(name=name, passed=False) for name in names])


def test_running_gear_defers_body_but_rejects_missing_wheels_and_clipped_renders():
    report = quality("body_role", "roof_role", "overall_length", "door_window_assemblies",
        "tyre_front_left", "rear-quarter.pixels")
    assert {item["name"] for item in phase_quality_issues(report, "running-gear")} == {
        "tyre_front_left", "rear-quarter.pixels"}
    with pytest.raises(ValueError, match="Phase geometry checks require repair"):
        require_phase_quality(report, "running-gear")


def test_completed_modules_remain_gated_as_later_modules_are_added():
    report = quality("body_role", "roof_role", "door_window_assemblies", "side_glass.door_contact")
    assert {item["name"] for item in phase_quality_issues(report, "body")} == {
        "body_role", "side_glass.door_contact"}
    assert {item["name"] for item in phase_quality_issues(report, "cabin")} == {
        "body_role", "roof_role", "side_glass.door_contact"}
    assert len(phase_quality_issues(report, "doors-glass")) == 4


def test_final_model_cannot_advance_with_unresolved_connections_or_missing_windows():
    report = quality("cowl.connection.join_front", "door_window_assemblies")
    assert len(phase_quality_issues(report, "front")) == 1
    with pytest.raises(ValueError, match='"failed_count": 2'):
        require_phase_quality(report, "details")
    require_phase_quality(dict(checks=[dict(name="cowl.connection.join_front", passed=True)]), "details")


def test_downstream_cache_binds_actual_checkpoint_geometry_after_a_repair():
    original = dict(key="same-request", outputs={"model.blend": "before", "semantic-scene.json": "old"})
    repaired = dict(key="same-request", outputs={"model.blend": "after", "semantic-scene.json": "new"})
    assert checkpoint_identity(original) != checkpoint_identity(repaired)
    assert checkpoint_identity(original) == checkpoint_identity(dict(original, analysis="different prose"))


def test_phase_cache_reuses_unchanged_inputs_but_invalidates_actual_consumed_evidence():
    from copy import deepcopy
    from car2lego3d.staged_model import phase_checkpoint_key
    inputs = dict(manifest_sha256="old complete manifest", checkpoint_version=5, exporter_sha256="exporter")
    reference = dict(brief=dict(model="Countach"), images=dict(left=dict(sha256="left", state="closed")),
        reference_repairs=[])
    original = phase_checkpoint_key(inputs, "body", reference, "prior-geometry")
    assert phase_checkpoint_key({**inputs, "manifest_sha256": "changed unrelated rear detail"},
        "body", reference, "prior-geometry") == original
    for changed in (dict(reference, images=dict(left=dict(sha256="new pixels", state="closed"))),
                    dict(reference, images=dict(left=dict(sha256="left", state="open"))),
                    dict(reference, reference_repairs=[dict(id="local repair", status="open")]),
                    dict(reference, brief=dict(model="different variant"))):
        assert phase_checkpoint_key(inputs, "body", changed, "prior-geometry") != original
    assert phase_checkpoint_key(inputs, "body", deepcopy(reference), "repaired prior geometry") != original
    assert phase_checkpoint_key({**inputs, "exporter_sha256": "changed exporter"},
        "body", reference, "prior-geometry") != original


def test_exporter_change_invalidates_reconstruction_checkpoints(tmp_path, monkeypatch):
    from car2lego3d import blender_mcp, staged_model
    from car2lego3d.common import digest

    exporter = tmp_path / 'scripts/blender_export_render.py'
    exporter.parent.mkdir()
    exporter.write_text('old exporter', encoding='utf-8')
    (exporter.parent / 'blender_mesh_guard.py').write_text('guard', encoding='utf-8')
    boundary = exporter.parent / 'blender_boundary_fit.py'
    boundary.write_text('boundary fitter', encoding='utf-8')
    views = tmp_path / 'views.json'
    views.write_text('{}', encoding='utf-8')
    captured = []

    @contextmanager
    def capture(directory, name, inputs):
        captured.append(inputs)
        raise RuntimeError('Captured inputs before build')
        yield

    monkeypatch.setattr(staged_model, 'ROOT', tmp_path)
    monkeypatch.setattr(staged_model, 'stage', capture)
    monkeypatch.setattr(staged_model, 'load_references', lambda path: ({}, {}))
    monkeypatch.setattr(staged_model, 'load_spec', lambda path: {})
    monkeypatch.setattr(staged_model, 'spec_record', lambda path: {})
    monkeypatch.setattr(blender_mcp, 'modelling_model', lambda: 'test-model')
    for content in ('old exporter', 'fixed recursive exporter'):
        exporter.write_text(content, encoding='utf-8')
        with pytest.raises(RuntimeError, match='Captured inputs'):
            staged_model.reconstruct(views, tmp_path / 'output', tmp_path / 'spec.json')
        assert captured[-1]['exporter_sha256'] == digest(exporter)
    assert staged_model.fingerprint(captured[0]) != staged_model.fingerprint(captured[1])
    guard = exporter.parent / 'blender_mesh_guard.py'
    guard.write_text('changed guard', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Captured inputs'):
        staged_model.reconstruct(views, tmp_path / 'output', tmp_path / 'spec.json')
    assert captured[-1]['mesh_guard_sha256'] == digest(guard)
    assert staged_model.fingerprint(captured[-1]) != staged_model.fingerprint(captured[-2])
    boundary.write_text('changed boundary fitter', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Captured inputs'):
        staged_model.reconstruct(views, tmp_path / 'output', tmp_path / 'spec.json')
    assert captured[-1]['boundary_fit_sha256'] == digest(boundary)
    assert staged_model.fingerprint(captured[-1]) != staged_model.fingerprint(captured[-2])
