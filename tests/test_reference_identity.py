import json

import pytest
from PIL import Image

from car2lego3d import reference_identity
from car2lego3d.common import digest


def test_identity_is_bound_to_the_actual_anchor_and_preserves_uncertainty(tmp_path, monkeypatch):
    anchor = tmp_path / "anchor.png"
    Image.new("RGB", (10, 10), "white").save(anchor)
    expected = {key: None for key in reference_identity.FIELDS}
    expected.update(paint="white", interior="cream", wheels="five circular holes; smooth rim lip")
    requests = []

    def ask(messages, directory, name, **options):
        requests.append((messages, directory, options))
        return {**expected, 'anchor_review': dict(id='overview', status='pass', observed_state='closed',
                                                issues=[], correction='', defects=[])}

    monkeypatch.setattr(reference_identity, "ask_json", ask)
    monkeypatch.setattr(reference_identity, "settings", lambda: dict(model="test-vision"))
    record = reference_identity.lock_identity(anchor, dict(paint="unspecified"), tmp_path / "locks")
    assert record["anchor_sha256"] == digest(anchor)
    assert record["observations"] == expected
    assert record["physical_verified"] is False and record["appearance_accepted"] is False
    assert requests[0][0][1]["content"][-1]["type"] == "image_url"
    assert requests[0][2]["reuse_completed"] is True
    first = requests[0][1] / "identity-lock.json"
    saved = digest(first)
    Image.new("RGB", (10, 10), "red").save(anchor)
    changed = reference_identity.lock_identity(anchor, dict(paint="unspecified"), tmp_path / "locks")
    assert changed["anchor_sha256"] != record["anchor_sha256"]
    assert requests[1][1] != requests[0][1] and digest(first) == saved
    assert json.loads(first.read_text())["observations"]["roof_glazing"] is None


def test_incomplete_identity_cannot_be_used_as_a_lock(tmp_path, monkeypatch):
    anchor = tmp_path / "anchor.png"
    Image.new("RGB", (10, 10)).save(anchor)
    monkeypatch.setattr(reference_identity, "settings", lambda: dict(model="test-vision"))
    monkeypatch.setattr(reference_identity, "ask_json", lambda *args, **kwargs: dict(paint="red"))
    with pytest.raises(ValueError, match="Anchor identity"):
        reference_identity.lock_identity(anchor, {}, tmp_path / "locks")
    assert not list((tmp_path / "locks").rglob("identity-lock.json"))


def test_top_level_typed_defects_are_preserved_for_legacy_identity_responses():
    defect = dict(issue_index=0, kind="rim_geometry", modules=["wheels"],
        expected="five large holes", verification="count enlarged openings")
    response = {key: None for key in reference_identity.FIELDS}
    response.update(wheels="six large openings", anchor_review=dict(id="overview", status="needs_revision",
        observed_state="Six openings are visible", issues=["Six instead of five holes"], correction="Use five holes"),
        defects=[defect])
    observations, review = reference_identity.validate_identity(response)
    assert observations["wheels"] == "six large openings"
    assert review["status"] == "needs_revision" and review["defects"] == [defect]
    response["anchor_review"]["defects"] = []
    with pytest.raises(ValueError, match="Conflicting"):
        reference_identity.validate_identity(response)
