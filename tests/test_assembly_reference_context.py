from copy import deepcopy

from PIL import Image
import pytest

from car2lego3d.assembly_reference_context import prepare_module_reference_sheets, reference_scope
from car2lego3d.common import digest
from car2lego3d.reference_plan import reference_plan


def countach_views():
    return {shot.id: shot.record() for shot in reference_plan("Countach")}


@pytest.mark.parametrize("module,required", [
    ("wheel_front_right", {"front_running_gear", "right", "front_right"}),
    ("wheel_rear_left", {"rear_running_gear", "left", "rear_left"}),
    ("door_right", {"door_right_closed", "door_right_open", "right"}),
    ("hood", {"front_cover_open", "front_cabin_join"}),
    ("headlamp_mechanism", {"popup_lamps", "headlamps"}),
    ("rear_lights", {"taillamps", "back"}),
    ("rear_cover", {"rear_cover_open", "powertrain_cargo"}),
])
def test_scopes_have_identity_and_module_details(module, required):
    views = countach_views()
    before = deepcopy(views)
    result = reference_scope(dict(id=module), views)
    assert {"overview", *required} <= result["views"].keys()
    assert len(result["views"]) <= 8
    assert set(result["views"]) | set(result["omitted_views"]) == set(views)
    assert views == before


def test_opposite_side_door_is_not_a_direct_reference():
    scope = reference_scope(dict(id="door_right"), countach_views())
    assert scope["views"]["door_right_open"]["relation"] == "same_side"
    assert "door_left_open" not in scope["views"]
    assert "door_left_closed" not in scope["views"]
    assert "scissor_hinge" not in scope["views"]


def test_opposite_side_inference_stays_explicit_when_needed():
    scope = reference_scope(dict(id="wheel_front_right"), countach_views())
    assert scope["views"]["front_running_gear"]["relation"] == "opposite_side"


def test_legacy_views_stay_available_and_selection_is_order_independent():
    rows = {name: {} for name in ("front", "back", "left", "right")}
    assert reference_scope(dict(id="cabin"), rows)["views"] == reference_scope(
        dict(id="cabin"), dict(reversed(list(rows.items()))))["views"]
    assert set(reference_scope(dict(id="cabin"), rows)["views"]) == set(rows)
    with pytest.raises(ValueError):
        reference_scope(dict(id="cabin"), rows, limit=9)


def test_sheets_bind_original_hashes_bound_pixels_and_reject_stale_images(tmp_path):
    rows = countach_views()
    images = {}
    for number, view in enumerate(rows):
        source = tmp_path / (view + ".png")
        Image.new("RGB", (40 + number, 80), (number, 20, 50)).save(source)
        images[view] = (source, digest(source))
    result = prepare_module_reference_sheets(tmp_path / "scoped", rows, images, ["door_right", "steering"])
    for item in result.values():
        assert item["sha256"] == digest(item["file"])
        assert set(item["source_sha256"]) == set(item["scope"]["views"])
        assert item["pixels"][0] <= 1280 and item["pixels"][1] <= 704
        with Image.open(item["file"]) as image:
            assert image.size == tuple(item["pixels"])
    repeated = prepare_module_reference_sheets(tmp_path / "scoped", rows, images, ["door_right"])
    assert result["door_right"] == repeated["door_right"]
    images["door_right_open"][0].write_bytes(b"stale reference")
    with pytest.raises(ValueError, match="checksum mismatch"):
        prepare_module_reference_sheets(tmp_path / "scoped", rows, images, ["door_right"])
