from car2lego3d.common import digest, write_json
from car2lego3d.creation_evidence import evidence
from car2lego3d.reference_gate import assess_reference_gate


def reviewed_reference(tmp_path):
    images = tmp_path / "01-image"
    path = images / "shots/overview.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"reviewed reference pixels")
    row = dict(status="needs_revision", issues=["six holes"], sha256=digest(path),
        defects=[dict(issue_index=0, kind="rim_geometry", modules=["wheels"],
            expected="five holes", verification="count wheel openings")])
    gate = assess_reference_gate(dict(overview=row))
    report = images / "content-review/report.json"
    manifest = dict(shots=dict(overview=dict(file="shots/overview.png", sha256=digest(path), state="closed")))
    snapshot = report.parent / "reference-manifest.json"
    write_json(snapshot, manifest)
    write_json(report, dict(shots=dict(overview=row), reference_gate=gate,
        manifest_snapshot=dict(file=snapshot.name), manifest_sha256=digest(snapshot)))
    write_json(images / "views.json", dict(manifest,
        content_review=dict(file="content-review/report.json", sha256=digest(report))))
    write_json(images / "reference-gate.json", gate)
    return gate, path, report


def test_historical_results_without_new_checks_are_unverified(tmp_path):
    write_json(tmp_path / "06-acceptance/delivery-report.json", dict(status="accepted"))
    report = evidence(tmp_path)
    assert not report["reference_gate"]["allowed"]
    assert report["module_progress"]["status"] == "unverified"
    assert not report["acceptance"]["accepted"]


def test_v2_module_tasks_are_visible_but_stale_assembled_tasks_do_not_count(tmp_path):
    from car2lego3d.common import write_json
    tasks = {"chassis": dict(id="chassis", dependencies=[], status="assembled", attempts=[],
        measurement=dict(status="pass", candidates=dict(status="pass")))}
    write_json(tmp_path / "03-lego/agent/module-tasks.json", dict(schema="vehicle-assembly-schedule.v2",
        planner="codex", active=None, input_hash="fixture", required=1, complete=1,
        contract=dict(mission=dict(vehicle_contract=dict(modules={"chassis": {"roles": ["structure"]}}))),
        tasks=tasks))
    report = evidence(tmp_path)
    assert report["module_tasks"]["schema"] == "vehicle-assembly-schedule.v2"
    assert report["module_tasks"]["required"] == 1
    assert report["module_tasks"]["complete"] == 0
    assert report["module_tasks"]["tasks"][0]["status"] == "unverified"
    assert not report["module_tasks"]["tasks"][0]["evidence_current"]
    assert not report["acceptance"]["accepted"]


def test_raw_reference_findings_and_open_repairs_remain_visible(tmp_path):
    gate, _, _ = reviewed_reference(tmp_path)
    report = evidence(tmp_path)
    assert report["reference_gate"]["allowed"] and not report["reference_gate"]["image_correct"]
    assert report["repair_tasks"] == gate["repair_tasks"] and not report["acceptance"]["accepted"]


def test_baseline_scope_exclusions_do_not_rewrite_raw_image_review_or_certify_delivery(tmp_path, monkeypatch):
    from car2lego3d.baseline_detail import COUNTACH_RIM_SCOPE
    gate, _, _ = reviewed_reference(tmp_path)
    task = gate["repair_tasks"][0]
    monkeypatch.setitem(COUNTACH_RIM_SCOPE, task["id"], task["source_sha256"])
    report = evidence(tmp_path, dict(brand="Lamborghini", model="Countach"))
    assert not report["repair_tasks"]
    assert report["excluded_reference_repairs"][0]["task"] == task
    assert report["reference_gate"] == gate and not report["reference_gate"]["image_correct"]
    assert not report["acceptance"]["accepted"]
    assert evidence(tmp_path, dict(brand="Nissan", model="R34"))["repair_tasks"] == [task]


def test_reference_verdict_expires_when_pixels_or_report_change(tmp_path):
    gate, image, report = reviewed_reference(tmp_path)
    assert evidence(tmp_path)["reference_gate"]["allowed"]
    image.write_bytes(b"new reference pixels")
    stale = evidence(tmp_path)["reference_gate"]
    assert stale["stale"] and not stale["allowed"] and stale["status"] == "unverified"
    assert stale["raw_gate"] == gate
    image.write_bytes(b"reviewed reference pixels")
    report.write_text('{}', encoding="utf-8")
    assert not evidence(tmp_path)["reference_gate"]["allowed"]


def test_in_progress_source_replacement_expires_copied_reference_verdict(tmp_path):
    _, image, _ = reviewed_reference(tmp_path)
    source = tmp_path / "01-image/car2lego/references/overview.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(image.read_bytes())
    manifest = source.parent / "manifest.json"
    write_json(manifest, dict(images=[dict(view="overview", path="references/overview.png", sha256=digest(source))]))
    assert evidence(tmp_path)["reference_gate"]["allowed"]
    source.write_bytes(b"replacement before copying shots")
    write_json(manifest, dict(images=[dict(view="overview", path="references/overview.png", sha256=digest(source))]))
    assert not evidence(tmp_path)["reference_gate"]["allowed"]


def test_unbound_historical_reference_verdict_is_preserved_but_unverified(tmp_path):
    gate = dict(status="blocked", allowed=False, blocked_views=["left"])
    write_json(tmp_path / "01-image/reference-gate.json", gate)
    result = evidence(tmp_path)["reference_gate"]
    assert result["raw_gate"] == gate and result["status"] == "unverified"
    assert result["blocked_views"] == [] and not result["allowed"]


def test_reference_verdict_binds_camera_state_and_identity_contract(tmp_path):
    import json
    reviewed_reference(tmp_path)
    path = tmp_path / "01-image/views.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["shots"]["overview"]["state"] = "door open"
    write_json(path, manifest)
    assert not evidence(tmp_path)["reference_gate"]["allowed"]
    manifest["shots"]["overview"]["state"] = "closed"
    manifest["brief"] = dict(model="different body variant")
    write_json(path, manifest)
    assert not evidence(tmp_path)["reference_gate"]["allowed"]


def test_stale_module_checks_cannot_close_repairs(tmp_path):
    task = dict(id="rim", status="open", expected="five holes")
    write_json(tmp_path / "01-image/reference-gate.json", dict(status="ready_with_repairs", allowed=True, repair_tasks=[task]))
    write_json(tmp_path / "03-lego/agent/state.json", dict(revision=2))
    write_json(tmp_path / "03-lego/agent/module-progress.json", dict(revision=1, status="pass", completion_ready=True,
        modules=[dict(id="wheels", status="pass")], repair_tasks=[dict(task, status="closed")]))
    report = evidence(tmp_path)
    assert report["module_progress"]["stale"]
    assert report["repair_tasks"][0]["status"] == "open"
    assert not report["acceptance"]["accepted"]


def test_delivery_gates_expire_when_the_audited_file_changes(tmp_path):
    from car2lego3d.assembly_evidence import snapshot_files
    io = tmp_path / "model.io"
    io.write_bytes(b"audited bytes")
    write_json(tmp_path / "06-acceptance/delivery-report.json", dict(status="not_accepted",
        gates=dict(archive="pass", digital="pass"), evidence_files=snapshot_files([io])))
    assert evidence(tmp_path)["acceptance"]["gates"]["archive"] == "pass"
    io.write_bytes(b"different bytes")
    report = evidence(tmp_path)["acceptance"]
    assert not report["evidence_current"]
    assert report["gates"]["archive"] == report["gates"]["digital"] == "unverified"


def test_historical_unbound_gate_labels_are_not_current_evidence(tmp_path):
    write_json(tmp_path / "06-acceptance/delivery-report.json", dict(status="accepted",
        gates=dict(archive="pass", digital="pass", motion="pass")))
    report = evidence(tmp_path)["acceptance"]
    assert not report["evidence_current"] and not report["accepted"]
    assert set(report["gates"].values()) == {"unverified"}
