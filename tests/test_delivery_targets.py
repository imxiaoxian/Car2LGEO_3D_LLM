import json

from car2lego3d.assembly_protocol import protocol_hash
from car2lego3d.delivery_targets import TARGETS, training_audit


def test_final_roster_keeps_user_order_and_excludes_historical_targets():
    assert list(TARGETS) == ["countach", "f40", "nissan-r34", "bugatti-chiron-pur-sport"]
    assert [t["set_num"] for t in TARGETS.values()] == ["10337-1", "10248-1", "42210-1", "42222-1"]


def test_training_audit_rejects_family_duplicates_and_missing_provenance(tmp_path, monkeypatch):
    from car2lego3d import assembly_website
    monkeypatch.setattr(assembly_website, "vehicle_exclusions", lambda corpus, target:
        dict(models=["held"], families=["target-family"], hashes=["repeated"]))
    class Corpus:
        def snapshot(self):
            return "frozen"
    rows = [dict(source=dict(model_id="other", families=["other-family"],
        module_hashes=["repeated"], protocol_hash=protocol_hash())), dict(source={})]
    path = tmp_path / "train.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    report = training_audit(Corpus(), [path], targets={"fixture": {}})
    assert not report["current_comparison_eligible"]
    assert report["datasets"][0]["conflicts"][0]["reasons"] == ["target_duplicate_module"]
    assert report["datasets"][0]["unknown_provenance"] == [2]
    assert report["datasets"][0]["stale_protocol"] == [2]
