from copy import deepcopy

from car2lego3d.reference_gate import assess_reference_gate, phase_modules


def local():
    return dict(status="needs_revision", sha256="a" * 64, issues=["Six holes instead of five"],
        defects=[dict(issue_index=0, kind="rim_geometry", modules=["wheels"],
            expected="five holes", verification="Count openings in resolved rim geometry and render")])


def test_local_defects_admit_modelling_but_preserve_raw_failure_and_repair_debt():
    row = local()
    before = deepcopy(row)
    gate = assess_reference_gate(dict(overview=row))
    assert row == before and row["status"] == "needs_revision"
    assert gate["allowed"] and gate["status"] == "ready_with_repairs"
    assert not gate["image_correct"] and not gate["appearance_accepted"]
    task = gate["repair_tasks"][0]
    assert task["status"] == "open" and task["source_sha256"] == row["sha256"]
    assert task["evidence"] == [] and task["modules"] == ["wheels"]
    assert assess_reference_gate(dict(overview=row)) == gate
    row["sha256"] = "b" * 64
    assert assess_reference_gate(dict(overview=row))["repair_tasks"][0]["id"] != task["id"]


def test_mixed_local_and_structural_defects_block_the_view():
    row = local()
    row["issues"].append("Door glass belongs to the body")
    row["defects"].append(dict(issue_index=1, kind="moving_ownership", modules=["doors"],
        expected="glass moves with door", verification="hinge sweep"))
    gate = assess_reference_gate(dict(left=row))
    assert not gate["allowed"] and gate["blocked_views"] == ["left"]


def test_historical_missing_and_malformed_evidence_fail_closed():
    variants = []
    for key in ("sha256", "defects", "issues"):
        row = local()
        del row[key]
        variants.append(row)
    for status in ("indeterminate", "pass", "unknown", None):
        variants.append({**local(), "status": status})
    for indices in ([1], [0, 0]):
        row = local()
        row["defects"] = [{**row["defects"][0], "issue_index": i} for i in indices]
        variants.append(row)
    for row in variants:
        assert not assess_reference_gate(dict(front=row))["allowed"]


def test_clean_pass_is_separate_from_appearance_acceptance():
    gate = assess_reference_gate(dict(front=dict(status="pass", issues=[], sha256="image")))
    assert gate["allowed"] and gate["image_correct"]
    assert gate["repair_tasks"] == [] and gate["appearance_accepted"] is False
    assert not assess_reference_gate({})["allowed"]
    assert not assess_reference_gate(dict(front=dict(status="pass", issues=[])))["allowed"]


def test_actual_wheel_labels_route_to_running_gear_without_losing_review_labels():
    row = local()
    row["defects"][0]["modules"] = ["front_left_wheel"]
    task = assess_reference_gate(dict(overview=row))["repair_tasks"][0]
    assert task["modules"] == ["front_left_wheel"]
    assert task["phase_modules"] == ["wheels"]


def test_minor_trim_routes_to_affected_phase_domains():
    assert phase_modules(dict(kind="minor_trim", modules=["rear_door_window_trim"])) == [
        "body", "doors", "glazing", "rear"]
    assert phase_modules(dict(kind="minor_trim", modules=["headlamp_surround"])) == ["body", "lights"]
