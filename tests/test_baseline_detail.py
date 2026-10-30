from copy import deepcopy

import pytest

from car2lego3d.baseline_detail import COUNTACH_RIM_SCOPE, detail_standard, scope_repairs
from car2lego3d.assembly_requirements import vehicle_contract
from car2lego3d.assembly_schedule import task_plan
from car2lego3d.blender_mcp import modelling_prompt
from car2lego3d.reference_plan import reference_plan, shot_fingerprint, validate_plan


@pytest.mark.parametrize("brand,model,case", [
    ("Lamborghini", "Countach 5000 Quattrovalvole", "countach"),
    ("Ferrari", "F40", "f40"), ("Nissan", "Skyline GT-R (R34)", "nissan-r34"),
    ("Bugatti", "Chiron Pur Sport", "bugatti-chiron-pur-sport"),
    ("", "Lamborghini Countach 5000 Quattrovalvole", "countach"),
    ("", "Ferrari F40", "f40"),
    ("兰博基尼", "Countach", "countach"),
    ("法拉利", "F40", "f40"),
    ("日产", "Skyline GT-R (R34)", "nissan-r34"),
    ("布加迪", "Chiron Pur Sport", "bugatti-chiron-pur-sport"),
    ("", "兰博基尼 Countach", "countach"),
    ("", "法拉利 F40", "f40"),
    ("", "日产 Skyline GT-R (R34)", "nissan-r34"),
    ("", "布加迪 Chiron Pur Sport", "bugatti-chiron-pur-sport"),
    ("", "Skyline GT-R (R34)", "nissan-r34"),
    ("", "R34", "nissan-r34"),
    ("", "Chiron Pur Sport", "bugatti-chiron-pur-sport"),
])
def test_baseline_scope_resolves_request_and_physical_spec_identity(brand, model, case):
    standard = detail_standard(dict(brand=brand, model=model))
    assert standard["case"] == case
    assert len(standard["source"]["sha256"]) == 64
    assert standard["acceptance"] == "unverified"
    assert "knowledge-base IO" in modelling_prompt(standard)


def test_wrong_brand_and_unrelated_vehicle_cannot_inherit_baseline_scope():
    assert detail_standard(dict(brand="Ferrari", model="Countach")) is None
    assert detail_standard(dict(brand="BMW", model="M4")) is None
    for model in ("Ferrari Countach", "Ferrari R34", "法拉利 Skyline GT-R (R34)",
            "BMW Chiron Pur Sport", "宝马 R34"):
        assert detail_standard(dict(model=model)) is None


@pytest.mark.parametrize("model", ["Countach", "Lamborghini Countach", "兰博基尼 Countach"])
def test_combined_identity_keeps_baseline_specific_module_requirements(model):
    contract = vehicle_contract(dict(model=model), "scene")
    assert contract["detail_standard"]["case"] == "countach"
    assert "headlamp_mechanism" in contract["modules"]
    assert "hood" in contract["modules"] and "rear_cover" in contract["modules"]
    assert "suspension" not in contract["modules"]


def test_each_car_has_its_own_baseline_mechanism_budget_and_safe_schedule():
    cases = [("Lamborghini", "Countach", False, True), ("Ferrari", "F40", False, False),
        ("Nissan", "R34", True, True), ("Bugatti", "Chiron Pur Sport", False, True)]
    for brand, model, suspension, steering in cases:
        contract = vehicle_contract(dict(brand=brand, model=model), "scene")
        modules = contract["modules"]
        assert ("suspension" in modules) is suspension
        assert ("steering" in modules) is steering
        assert modules["front_lights"]["roles"] == ["lamp"]
        assert modules["door_left"]["roles"] == ["panel", "hinge"]
        seen = set()
        for name, task in task_plan(contract).items():
            assert set(task["dependencies"]) <= seen
            seen.add(name)
        assert seen == set(modules)


def test_scope_exclusion_is_bound_to_exact_reviewed_finding_and_preserves_raw_evidence():
    key, source_hash = next(iter(COUNTACH_RIM_SCOPE.items()))
    reviewed = dict(id=key, source_sha256=source_hash, kind="rim_geometry", status="open", expected="five openings")
    unsafe = [dict(reviewed, id="another defect"), dict(reviewed, source_sha256="new pixels"),
        dict(reviewed, kind="wheel_missing"), dict(id="rim fit", kind="rim_geometry")]
    original = deepcopy(reviewed)
    required, excluded = scope_repairs([reviewed, *unsafe], detail_standard(dict(model="Countach")))
    assert required == unsafe and len(excluded) == 1
    assert reviewed == original and excluded[0]["task"]["status"] == "open"
    assert excluded[0]["status"] == "not_required_by_baseline"
    assert len(scope_repairs([reviewed], detail_standard(dict(model="F40")))[0]) == 1


def test_unknown_cover_scope_is_not_silently_certified_or_added_as_real_car_requirement():
    contract = vehicle_contract(dict(brand="Bugatti", model="Chiron Pur Sport"), "scene")
    assert "hood" not in contract["modules"] and "rear_cover" not in contract["modules"]
    assert contract["scope_review_required"] == ["cover_articulation"]


@pytest.mark.parametrize("identity,suspension,steering", [
    ("Lamborghini Countach", False, True), ("Ferrari F40", False, False),
    ("Nissan Skyline GT-R (R34)", True, True), ("Bugatti Chiron Pur Sport", False, True),
    ("兰博基尼 Countach", False, True), ("法拉利 F40", False, False),
    ("日产 Skyline GT-R (R34)", True, True), ("布加迪 Chiron Pur Sport", False, True),
])
def test_reference_requirements_follow_each_baseline_mechanism_scope(identity, suspension, steering):
    shots = reference_plan(identity)
    validate_plan(shots)
    by_id = {shot.id: shot for shot in shots}
    assert not {"pedal_footwell", "seat_mounts"} & by_id.keys()
    assert {"dashboard", "seats_pedals", "passenger_seat", "headlamps", "taillamps"} <= by_id.keys()
    front = by_id["front_running_gear"]
    assert ("suspension" in front.modules) is suspension
    assert ("steering" in front.modules) is steering
    assert ("20 degrees" in front.state) is steering
    if identity.startswith(("Nissan", "Bugatti")):
        assert "remains unverified" in by_id["rear_cover_open"].focus


def test_scope_changes_invalidate_only_changed_camera_contracts():
    from car2lego3d.reference_plan import baseline_shots
    original = reference_plan()
    before = {shot.id: shot for shot in original}
    after = {shot.id: shot for shot in baseline_shots(original, "Ferrari F40")}
    assert before["headlamps"].focus != after["headlamps"].focus
    assert shot_fingerprint(before["headlamps"], {}) != shot_fingerprint(after["headlamps"], {})
    assert shot_fingerprint(before["overview"], {}) == shot_fingerprint(after["overview"], {})
    assert before["pedal_footwell"].id == "pedal_footwell"
