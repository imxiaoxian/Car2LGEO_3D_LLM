"""Bounded planner memory derived only from executed host observations."""

import json


def repeated_cycle(history, revision):
    """Detect repeated observations at an unchanged assembly revision."""
    records = list(history)
    for period in range(1, 5):
        window = records[-3 * period:]
        if len(window) != 3 * period:
            continue
        if any(record["observation"].get("revision_before") != revision or
               record["observation"].get("revision_after") != revision for record in window):
            continue
        fingerprints = []
        for record in window:
            decision = record["decision"]
            try:
                arguments = json.loads(decision.get("arguments_json", "{}"))
            except (TypeError, ValueError):
                arguments = decision.get("arguments_json")
            observation = record["observation"]
            fingerprints.append(json.dumps([decision["tool"], arguments,
                observation.get("ok"), observation.get("result"), observation.get("error")],
                sort_keys=True, ensure_ascii=False))
        if fingerprints[:period] == fingerprints[period:2 * period] == fingerprints[2 * period:]:
            return dict(period=period, repetitions=3, revision=revision,
                first_step=window[0]["step"], last_step=window[-1]["step"],
                tools=[record["decision"]["tool"] for record in window[:period]])
    return None


def progress(observations, revision):
    guide_pages, recent, signatures = {}, [], {}
    check, exported = None, False
    for record in observations.values():
        if not record.get("ok"):
            continue
        tool, result = record.get("tool"), record.get("result", {})
        if record.get("revision_after") == revision:
            if tool == "check":
                check = result.get("status", "unverified")
            elif tool == "export":
                exported = True
        if tool != "context":
            continue
        resource, pointer = result["resource"], result["pointer"]
        recent.append([resource, pointer, result["offset"], result["next_offset"]])
        if resource != "guide":
            continue
        total = result["total"]
        pages = guide_pages.setdefault(pointer, dict(total=total, characters={}))
        if pages["total"] != total:
            pages = guide_pages[pointer] = dict(total=total, characters={})
        pages["characters"].update(enumerate(result["text"], result["offset"]))
        if all(i in pages["characters"] for i in range(total)):
            text = "".join(pages["characters"][i] for i in range(total))
            if pointer.startswith(("/tools/", "/operations/", "/build_operations/")):
                signatures.pop(pointer, None)
                signatures[pointer] = text
    start = guide_pages.get("/start", {})
    complete = bool(start) and len(start["characters"]) == start["total"]
    return dict(start_read=complete, signatures=dict(list(signatures.items())[-4:]),
                recent_reads=recent[-2:], reads_total=len(recent), check=check, exported=exported)


def action_progress(observations, revision):
    """Retain real mutation IDs and explicit lifecycle actions without executing them."""
    actions, check, motion, linkage_motion, preview, exported = [], None, None, None, None, False
    for step, record in observations.items():
        if not record.get("ok"):
            continue
        tool, result = record.get("tool"), record.get("result", {})
        if tool in {"edit", "build", "attach", "snap", "articulate", "graft", "bind_module", "restore_candidate"}:
            row = dict(step=step, tool=tool, revision=record.get("revision_after"))
            aliases = result.get("aliases", {})
            row["aliases"] = dict(list(aliases.items())[-8:])
            row["omitted_aliases"] = max(0, len(aliases) - 8)
            if "instance" in result:
                row["instance"] = result["instance"]
            actions.append(row)
        if record.get("revision_after") == revision:
            if tool == "check":
                check = result.get("status", "unverified")
            elif tool == "motion":
                motion = result.get("status", "unverified")
            elif tool == "linkage_motion":
                linkage_motion = result.get("status", "unverified")
            elif tool == "preview":
                preview = result.get("status", "unverified")
            elif tool == "export":
                exported = True
    return dict(revision=revision, mutations=actions[-3:], omitted_mutations=max(0, len(actions) - 3),
        check=check, motion=motion, linkage_motion=linkage_motion, preview=preview, exported=exported,
        guidance="These are executed actions, not acceptance. Recheck after edits; inspect actual preview, "
            "repair observed failures, export the current revision, then explicitly request finish. "
            "Source placements and instance counts alone do not establish completion.")
