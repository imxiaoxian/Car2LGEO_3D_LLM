import json

from car2lego3d.assembly_context import action_progress, progress, repeated_cycle


def page(pointer, text, offset=0, total=None, revision=0):
    total = len(text) if total is None else total
    return dict(tool="context", ok=True, revision_after=revision,
        result=dict(resource="guide", pointer=pointer, offset=offset, total=total,
                    text=text, next_offset=offset+len(text) if offset+len(text) < total else None))


def test_read_receipts_require_all_pages_and_preserve_exact_signatures():
    records = {"0": page("/start", "abc", total=6), "1": page("/start", "ef", 4, 6),
               "2": page("/tools/edit", "revision:integer,operations:operation[]")}
    partial = progress(records, 0)
    assert not partial["start_read"]
    assert partial["signatures"]["/tools/edit"] == "revision:integer,operations:operation[]"
    records["3"] = page("/start", "d", 3, 6)
    assert progress(records, 0)["start_read"]


def test_revision_and_success_bound_completion_memory():
    records = {"0": dict(tool="check", ok=True, revision_after=1, result=dict(status="pass")),
               "1": dict(tool="export", ok=True, revision_after=1, result={}),
               "2": dict(tool="check", ok=False, revision_after=2, result=dict(status="pass"))}
    assert progress(records, 1)["check"] == "pass"
    assert progress(records, 1)["exported"]
    assert progress(records, 2)["check"] is None
    assert not progress(records, 2)["exported"]


def test_memory_is_bounded_and_never_combines_mutable_resources_as_guide():
    records = {str(i): page(f"/tools/{i}", "signature") for i in range(20)}
    mutable = page("/start", "not a guide")
    mutable["result"]["resource"] = "observation"
    records["20"] = mutable
    result = progress(records, 0)
    assert not result["start_read"]
    assert len(result["signatures"]) == 4
    assert len(result["recent_reads"]) == 2
    assert result["reads_total"] == 21
    assert result["recent_reads"][-1] == ["observation", "/start", 0, None]


def cycle_record(step, name, arguments, *, before=0, after=0, result=None, ok=True):
    return dict(step=step, decision=dict(tool=name, arguments_json=json.dumps(arguments)),
        observation=dict(tool=name, ok=ok, revision_before=before, revision_after=after, result=result))


def test_repeated_read_note_error_cycle_is_detected_without_mutating():
    pattern = [("context", dict(pointer="/operations/add"), True),
               ("context", dict(pointer="/operations/commit"), False),
               ("remember", dict(text="same note"), True)]
    records = [cycle_record(i, name, arguments, ok=ok) for i, (name, arguments, ok) in enumerate(pattern * 3)]
    assert repeated_cycle(records[:-1], 0) is None
    result = repeated_cycle(records, 0)
    assert result["period"] == 3 and result["last_step"] == 8
    assert len(records) == 9


def test_distinct_context_pages_and_changed_observations_are_progress():
    pages = [cycle_record(i, "context", dict(resource="semantic", offset=400 * i)) for i in range(12)]
    assert repeated_cycle(pages, 0) is None
    changed = [cycle_record(i, "inspect", {}, result=dict(parts=i)) for i in range(12)]
    assert repeated_cycle(changed, 0) is None


def test_repetition_does_not_cross_successful_edits_or_revision_changes():
    records = [cycle_record(i, "edit", {}, before=i, after=i+1) for i in range(12)]
    assert repeated_cycle(records, 12) is None


def test_action_memory_keeps_real_aliases_and_only_explicit_current_lifecycle():
    observations = {"0": dict(tool="build", ok=True, revision_after=1,
        result=dict(aliases=dict(wheel="root/real-wheel", rim="root/real-wheel/rim"))),
        "1": dict(tool="check", ok=True, revision_after=1, result=dict(status="pass")),
        "2": dict(tool="export", ok=True, revision_after=1, result={}),
        "3": dict(tool="preview", ok=False, revision_after=1, result=dict(status="pass"))}
    current = action_progress(observations, 1)
    assert current["mutations"][0]["aliases"]["wheel"] == "root/real-wheel"
    assert current["check"] == "pass" and current["exported"]
    assert current["preview"] is None
    changed = action_progress(observations, 2)
    assert changed["check"] is None and not changed["exported"]
    assert changed["mutations"] == current["mutations"]


def test_joint_mutation_invalidates_old_lifecycle_and_retains_moving_instance():
    records = {"0": dict(tool="check", ok=True, revision_after=1, result=dict(status="pass")),
        "1": dict(tool="export", ok=True, revision_after=1, result={}),
        "2": dict(tool="articulate", ok=True, revision_after=2,
            result=dict(instance="root/wheel", motion="unverified")),
        "3": dict(tool="articulate", ok=False, revision_after=2,
            result=dict(instance="root/rejected"))}
    current = action_progress(records, 2)
    assert current["mutations"] == [dict(step="2", tool="articulate", revision=2,
        aliases={}, omitted_aliases=0, instance="root/wheel")]
    assert current["check"] is None and current["motion"] is None
    assert not current["exported"]


def test_build_signature_memory_waits_for_complete_pages():
    pointer = "/build_operations/articulate"
    records = {"0": page(pointer, "abc", total=6)}
    assert pointer not in progress(records, 0)["signatures"]
    records["1"] = page(pointer, "def", offset=3, total=6)
    assert progress(records, 0)["signatures"][pointer] == "abcdef"


def test_closed_linkage_report_does_not_replace_ordinary_motion():
    records = {"0": dict(tool="motion", ok=True, revision_after=1, result=dict(status="fail")),
        "1": dict(tool="linkage_motion", ok=True, revision_after=1, result=dict(status="pass"))}
    current = action_progress(records, 1)
    assert current["motion"] == "fail" and current["linkage_motion"] == "pass"
    assert action_progress(records, 2)["linkage_motion"] is None
