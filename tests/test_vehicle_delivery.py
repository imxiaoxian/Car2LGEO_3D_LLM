import pytest

from car2lego3d.common import digest, write_json
from car2lego3d.vehicle_delivery import (audit, core_case, human_appearance, record_appearance,
    report, review_inputs, single_report, website_acceptance)


@pytest.mark.parametrize("make,model,expected", [
    ("Lamborghini", "Countach 5000 Quattrovalvole", "countach"),
    ("法拉利", "F40", "f40"), ("BMW", "M4 Competition Coupe", "bmw-m4"),
    ("莲花", "For Me", "lotus-for-me"), ("Lotus", "Emeya", None),
    ("BMW", "M40", None), ("Ferrari", "F400", None)])
def test_core_vehicle_identity(make, model, expected):
    assert core_case(make, model) == expected


@pytest.mark.parametrize("make,model,expected", [
    ("日产", "《速度与激情2》日产 Skyline GT-R (R34) 跑车", "nissan-r34"),
    ("Nissan", "Skyline GT-R R34", "nissan-r34"),
    ("Nissan", "Skyline GT-R R340", None),
    ("布加迪", "布加迪 Chiron Pur Sport 超级跑车", "bugatti-chiron-pur-sport"),
    ("Bugatti", "Chiron Pur Sport", "bugatti-chiron-pur-sport"),
    ("Bugatti", "Chiron Sport", None)])
def test_local_library_replacement_identities(make, model, expected):
    assert core_case(make, model) == expected


def test_missing_artifacts_and_self_ratings_cannot_certify_delivery(tmp_path):
    write_json(tmp_path / "06-acceptance/review.json", dict(
        appearance=dict(status="pass", reviewer="model"), studio=dict(status="pass", reviewer="model")))
    result = audit("countach", tmp_path)
    assert result["status"] == "not_accepted"
    assert "io" in result["missing"]
    assert result["gates"]["appearance"] == result["gates"]["studio"] == "unverified"
    assert not result["inspection_views_complete"]
    from car2lego3d.assembly_evidence import files_current
    assert files_current(result["evidence_files"])
    write_json(tmp_path / "03-lego/agent/checks.json", dict(status="pass"))
    assert not files_current(result["evidence_files"])


def test_delivery_evidence_expires_when_reference_pixels_change(tmp_path):
    from car2lego3d.assembly_evidence import files_current
    path = tmp_path / "01-image/shots/overview.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"reference at review time")
    result = audit("countach", tmp_path)
    assert files_current(result["evidence_files"])
    path.write_bytes(b"changed reference")
    assert not files_current(result["evidence_files"])


def test_full_delivery_requires_all_four_cases(tmp_path):
    with pytest.raises(ValueError, match="all four"):
        report({"countach": tmp_path})
    result = report({case: tmp_path / case for case in ("countach", "f40", "nissan-r34", "bugatti-chiron-pur-sport")})
    assert not result["accepted"]
    assert len(result["vehicles"]) == 4


def test_corrupt_archive_is_failed_instead_of_aborting_the_report(tmp_path):
    target = tmp_path / "04-studio/model.io"
    target.parent.mkdir()
    target.write_bytes(b"not a Studio archive")
    result = audit("f40", tmp_path)
    assert result["gates"]["archive"] == "fail"
    assert result["issues"]


def website_fixture(tmp_path):
    evidence = dict(schema="vehicle-website-evidence.v1", scope="website_transport_and_preview_only", cases=[])
    vehicles = []
    for case in ("countach", "f40", "nissan-r34", "bugatti-chiron-pur-sport"):
        io = tmp_path / (case + ".io")
        io.write_bytes((case + " test archive bytes").encode())
        solid, wire = tmp_path / (case + ".png"), tmp_path / (case + "-wire.png")
        solid.write_bytes(b"solid fixture")
        wire.write_bytes(b"wireframe fixture")
        preview = dict(foreground_ratio=.2, solid=dict(file=str(solid), sha256=digest(solid)),
            wireframe=dict(file=str(wire), sha256=digest(wire)))
        download = dict(kind="io", file=str(io), sha256=digest(io), bytes=io.stat().st_size)
        evidence["cases"].append(dict(case=case, status="website_transport_verified", preview=preview, downloads=[download]))
        vehicles.append(dict(case=case, files=dict(io=dict(download))))
    path = tmp_path / "website.json"
    write_json(path, evidence)
    return path, evidence, vehicles


def test_live_website_evidence_is_bound_to_each_delivered_io(tmp_path):
    path, evidence, vehicles = website_fixture(tmp_path)
    assert website_acceptance(path, vehicles)["status"] == "pass"
    vehicles[0]["files"]["io"]["sha256"] = "different accepted archive"
    result = website_acceptance(path, vehicles)
    assert result["status"] == "fail"
    assert "Downloaded IO differs" in result["cases"][0]["issues"][0]


def test_countach_can_be_website_checked_without_waiting_for_other_vehicles(tmp_path):
    path, evidence, vehicles = website_fixture(tmp_path)
    evidence["cases"] = evidence["cases"][:1]
    write_json(path, evidence)
    assert website_acceptance(path, vehicles[:1], required_cases={"countach"})["status"] == "pass"
    assert website_acceptance(path, vehicles)["status"] == "fail"


def test_user_appearance_decision_expires_when_any_candidate_input_changes(tmp_path):
    paths = [tmp_path / name for name in ("04-studio/model.io", "03-lego/agent/assembly-graph.json",
        "06-acceptance/presentation.json")]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test candidate bytes")
    review = dict(schema="vehicle-appearance-review.v1", case="countach", status="pass",
        reviewer_kind="human", reviewer="website_user", inputs=review_inputs(tmp_path))
    write_json(tmp_path / "06-acceptance/user-review.json", review)
    assert human_appearance(tmp_path, "countach")["status"] == "pass"
    assert human_appearance(tmp_path, "f40")["status"] == "unverified"
    for path in paths:
        path.write_bytes(b"revised candidate")
        assert human_appearance(tmp_path, "countach")["status"] == "unverified"
        path.write_bytes(b"test candidate bytes")
    review["reviewer_kind"] = "agent"
    write_json(tmp_path / "06-acceptance/user-review.json", review)
    assert human_appearance(tmp_path, "countach")["status"] == "unverified"


def test_incomplete_vehicle_cannot_receive_user_acceptance(tmp_path):
    with pytest.raises(ValueError, match="Model changed"):
        record_appearance("countach", tmp_path, status="pass", inputs={})


def test_appearance_binding_includes_materials_and_preview_dependencies(tmp_path):
    material = tmp_path / "native" / "LDConfig.ldr"
    material.parent.mkdir()
    material.write_text("original materials")
    write_json(tmp_path / "03-lego/agent/checks.json", dict(evidence_files=[dict(file=str(material), sha256=digest(material))]))
    before = review_inputs(tmp_path)
    material.write_text("changed materials")
    assert review_inputs(tmp_path)["dependencies_sha256"] != before["dependencies_sha256"]
    before = review_inputs(tmp_path)
    write_json(tmp_path / "05-presentation/scene.json", dict(colors={"47": [0, 0, 0, 128]}))
    assert review_inputs(tmp_path)["dependencies_sha256"] != before["dependencies_sha256"]


def test_single_vehicle_delivery_still_requires_website_and_preserves_its_evidence(tmp_path, monkeypatch):
    import car2lego3d.vehicle_delivery as delivery
    path, evidence, vehicles = website_fixture(tmp_path)
    evidence["cases"] = evidence["cases"][:1]
    write_json(path, evidence)
    monkeypatch.setattr(delivery, "audit", lambda case, _: dict(vehicles[0], status="accepted", evidence_files=[]))
    assert not single_report("countach", tmp_path)["accepted"]
    result = single_report("countach", tmp_path, path)
    assert result["accepted"]
    from car2lego3d.assembly_evidence import files_current
    assert files_current(result["evidence_files"])
    path.write_text("{}")
    assert not files_current(result["evidence_files"])


@pytest.mark.parametrize("fault", ["missing_case", "duplicate_case", "empty_preview", "same_wireframe", "changed_file", "failed_job"])
def test_website_transport_cannot_pass_incomplete_or_changed_evidence(tmp_path, fault):
    path, evidence, vehicles = website_fixture(tmp_path)
    entry = evidence["cases"][0]
    if fault == "missing_case":
        evidence["cases"].pop()
    elif fault == "duplicate_case":
        evidence["cases"][1] = dict(entry)
    elif fault == "empty_preview":
        entry["preview"]["foreground_ratio"] = 0
    elif fault == "same_wireframe":
        entry["preview"]["wireframe"] = entry["preview"]["solid"]
    elif fault == "changed_file":
        from pathlib import Path
        Path(entry["downloads"][0]["file"]).write_bytes(b"changed after browser download")
    else:
        entry["status"] = "failed"
    write_json(path, evidence)
    assert website_acceptance(path, vehicles)["status"] == "fail"


def test_four_vehicle_acceptance_also_requires_live_website_evidence(tmp_path, monkeypatch):
    import car2lego3d.vehicle_delivery as delivery
    path, evidence, vehicles = website_fixture(tmp_path)
    monkeypatch.setattr(delivery, "audit", lambda case, _: {**next(row for row in vehicles if row["case"] == case), "status": "accepted"})
    cases = {row["case"]: tmp_path for row in vehicles}
    result = report(cases)
    assert result["vehicle_delivery_accepted"] and not result["accepted"]
    assert report(cases, path)["accepted"]
