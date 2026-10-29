"""Reproducible planner comparisons; exports alone never select a winner."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .assembly_protocol import protocol_hash
from .common import digest, write_json

CORE_CASES = {"countach", "f40", "nissan-r34", "bugatti-chiron-pur-sport"}
SUPPORTED_CASES = CORE_CASES | {"lotus-for-me", "bmw-m4"}
PLANNERS = {"codex", "local_base", "local_finetuned"}
GATES = ("digital", "motion", "archive", "completeness", "appearance", "studio")


def compare(records, required_cases=CORE_CASES):
    """Consume measurements, not model self-ratings. Unknown gates block promotion."""
    records = list(records)
    required_cases = set(required_cases)
    if not required_cases or not required_cases.issubset(SUPPORTED_CASES):
        raise ValueError("Comparison requires supported vehicle cases")
    grouped = {}
    for record in records:
        if record.get("planner") not in PLANNERS or record.get("case") not in SUPPORTED_CASES:
            raise ValueError("Unsupported planner or vehicle case")
        if not isinstance(record.get("input_hash"), str) or not record["input_hash"]:
            raise ValueError("Comparison requires an input contract hash")
        for field, maximum in (("appearance_score", 100), ("tool_success_rate", 1),
                               ("hard_failures", None), ("repairs", None), ("seconds", None)):
            value = record.get(field)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum)):
                raise ValueError(f"Invalid comparison metric: {field}")
        if any(type(record[field]) is not int for field in ("hard_failures", "repairs")):
            raise ValueError("Failure and repair counts must be integers")
        gates = record.get("gates", {})
        if (not isinstance(gates, dict) or any(value not in {"pass", "fail", "unverified"}
                for value in gates.values())
                or record["hard_failures"] != sum(gates.get(g) == "fail" for g in GATES)):
            raise ValueError("Failure counts must agree with measured gates")
        key = (record["case"], record["planner"])
        if key in grouped:
            raise ValueError("Duplicate planner/case result")
        grouped[key] = record
    planners = {r["planner"] for r in records}
    reasons = []
    if not {"codex", "local_base", "local_finetuned"}.issubset(planners):
        reasons.append("Codex, base and fine-tuned results are all required")
    for case in sorted(required_cases):
        rows = [grouped.get((case, p)) for p in sorted(planners)]
        if not rows or any(r is None for r in rows):
            reasons.append(f"Missing complete planner comparison: {case}")
            continue
        if len({r["input_hash"] for r in rows}) != 1:
            reasons.append(f"Inputs differ: {case}")
        if any(r.get("gates", {}).get(g) not in {"pass", "fail"} for r in rows for g in GATES):
            reasons.append(f"Required measurements are unverified: {case}")
    metrics = {}
    for name in sorted(planners):
        rows = [r for r in records if r["planner"] == name and r["case"] in required_cases]
        if not rows:
            continue
        metrics[name] = dict(cases=len(rows),
            hard_failures=sum(r.get("hard_failures", 0) for r in rows),
            appearance=sum(r.get("appearance_score", 0) for r in rows) / len(rows),
            tool_success_rate=sum(r.get("tool_success_rate", 0) for r in rows) / len(rows),
            repairs=sum(r.get("repairs", 0) for r in rows),
            seconds=sum(r.get("seconds", 0) for r in rows))
    winner = None
    if not reasons:
        # Quality precedes repair cost and latency. A tie does not change the default.
        contenders = [n for n in ("codex", "local_finetuned") if metrics[n]["hard_failures"] == 0
            and all(grouped[(c,n)]["gates"][g] == "pass" for c in required_cases
                    for g in GATES)]
        ranked = sorted(contenders, key=lambda n: (-metrics[n]["appearance"],
            -metrics[n]["tool_success_rate"], metrics[n]["repairs"], metrics[n]["seconds"]))
        if ranked:
            candidate = ranked[0]
            baseline = metrics["local_base"]
            score = lambda m: (m["appearance"], m["tool_success_rate"], -m["repairs"])
            if score(metrics[candidate]) > score(baseline) and (len(ranked) == 1 or score(metrics[candidate]) > score(metrics[ranked[1]])):
                winner = candidate
            else:
                reasons.append("No reproducible quality improvement or tied quality")
        else:
            reasons.append("Neither assembly planner passes all required gates")
    return dict(schema="assembly-comparison.v1", protocol_hash=protocol_hash(),
        required_cases=sorted(required_cases), status="accepted" if winner else "pending",
        winner=winner, reasons=reasons, metrics=metrics, records=records)


def collect_case(directory, case, review=None):
    """Only read actual host artifacts and reviews tied to their hashes."""
    directory = Path(directory)
    state = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    records = [json.loads(r) for r in (directory / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    graph = directory / "assembly-graph.json"
    model = directory / "model.io"
    actual_graph = json.loads(graph.read_text(encoding="utf-8"))
    from .assembly_graph import Assembly, IOArchive
    saved = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    assembly = Assembly(saved["text"], saved["source_sha256"])
    assembly.revision, assembly.joints = saved["revision"], saved["joints"]
    if actual_graph != assembly.graph():
        raise ValueError("Exported graph differs from current assembly")
    revision = actual_graph["revision"]
    if state.get("status") not in {"draft", "complete"}:
        raise ValueError("Assembly run has not completed")
    for key, filename in (("checks", "checks.json"), ("motion", "motion.json")):
        observation = state.get(key, {})
        measured = json.loads((directory / filename).read_text(encoding="utf-8"))
        if (not observation.get("ok") or observation.get("revision_after") != revision
                or measured != observation.get("result") or measured.get("revision") != revision):
            raise ValueError("Missing or stale assembly measurement: " + key)
    exported = state.get("export", {})
    if not exported.get("ok") or exported.get("revision_after") != revision:
        raise ValueError("Missing or stale assembly export")
    restored = IOArchive(model).assembly
    if restored.bom() != assembly.bom() or restored.graph()["instances"] != actual_graph["instances"]:
        raise ValueError("IO differs from the evaluated assembly")
    review = review or {}
    bound = review.get("graph_sha256") == digest(graph) and review.get("io_sha256") == digest(model)
    appearance = review.get("appearance", {}) if bound else {}
    required_views = {"whole": 6, "detail": 8}
    images = appearance.get("images", [])
    hashes = [i.get("sha256") for i in images]
    views = [(i.get("category"), i.get("view")) for i in images]
    evidence = (len(set(hashes)) == len(hashes) and len(set(views)) == len(views)
        and all(i.get("view") and i.get("sha256") for i in images)
        and all(sum(i.get("category") == k for i in images) >= count for k, count in required_views.items()))
    for item in images:
        image = (directory / item["file"]).resolve()
        if not image.is_relative_to(directory.resolve()) or not image.is_file() or digest(image) != item["sha256"]:
            evidence = False
    from .assembly_evidence import module_report_current
    module_path = directory / "module-progress.json"
    modules = json.loads(module_path.read_text(encoding="utf-8")) if module_path.is_file() else {}
    completeness = (modules.get("status", "unverified") if module_report_current(directory, modules)
        and state.get("module_progress") == modules else "unverified")
    if completeness == "pass" and not modules.get("completion_ready"):
        completeness = "unverified"
    gates = dict(digital=state["checks"]["result"].get("status", "unverified"),
        motion=state.get("motion", {}).get("result", {}).get("status", "unverified"),
        archive="pass" if state.get("export", {}).get("result", {}).get("archive", {}).get("archive_roundtrip_verified") else "unverified",
        completeness=completeness,
        appearance=appearance["status"] if appearance.get("status") in {"pass", "fail"} and evidence and appearance.get("reviewer") else "unverified",
        studio=review["studio"]["status"] if bound and review.get("studio", {}).get("status") in {"pass", "fail"} and review["studio"].get("reviewer") else "unverified")
    return dict(case=case, planner=state["planner"], input_hash=state["input_hash"], gates=gates,
        appearance_score=float(appearance.get("score", 0)) if gates["appearance"] == "pass" else 0,
        hard_failures=sum(v == "fail" for v in gates.values()),
        tool_success_rate=sum(r["observation"]["ok"] for r in records) / max(1,len(records)),
        repairs=sum(not r["observation"]["ok"] for r in records), seconds=state["elapsed_seconds"],
        artifacts=[dict(file=str(p.resolve()), sha256=digest(p)) for p in (graph, model,
            directory / "state.json", directory / "checks.json", directory / "motion.json",
            directory / "agent.json", directory / "agent-decisions.jsonl", module_path) if p.is_file()],
        evidence=dict(directory=str(directory.resolve()), review=review))


def validate_selection(evidence, directory):
    """Recompute selection from pinned evidence; a bare winner string cannot promote."""
    from .assembly_agent import execution_hash, skill_bundle
    try:
        report_path = (Path(directory) / evidence["report"]).resolve()
        if not report_path.is_relative_to(Path(directory).resolve()) or digest(report_path) != evidence["report_sha256"]:
            return False
        report = json.loads(report_path.read_text(encoding="utf-8"))
        actual = [collect_case(row["evidence"]["directory"], row["case"], row["evidence"]["review"])
                  for row in report["records"]]
        for row in actual:
            state = json.loads((Path(row["evidence"]["directory"]) / "agent.json").read_text(encoding="utf-8"))
            if state["contract"]["execution_hash"] != execution_hash():
                return False
        if actual != report["records"]:
            return False
        recomputed = compare(actual)
        if (recomputed["status"] != "accepted" or recomputed["winner"] != evidence["winner"]
                or report != recomputed
                or evidence["protocol_hash"] != protocol_hash() or evidence["skill_hash"] != skill_bundle()["sha256"]):
            return False
        for row in report["records"]:
            if not row.get("artifacts") or any(digest(Path(a["file"])) != a["sha256"] for a in row["artifacts"]):
                return False
        return evidence.get("status") == "accepted"
    except (KeyError, ValueError, OSError, TypeError):
        return False


def publish_selection(report_path, directory):
    """Publish only a replayable comparison; keep pending evidence for diagnosis."""
    from .assembly_agent import skill_bundle
    report_path, directory = Path(report_path), Path(directory)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    pinned = directory / "reports" / (digest(report_path) + ".json")
    pinned.parent.mkdir(parents=True, exist_ok=True)
    if not pinned.exists():
        pinned.write_bytes(report_path.read_bytes())
    evidence = dict(schema="assembly-selection.v1", status=report.get("status"),
        winner=report.get("winner"), report=str(pinned.relative_to(directory)),
        report_sha256=digest(pinned), protocol_hash=protocol_hash(), skill_hash=skill_bundle()["sha256"])
    if not validate_selection(evidence, directory):
        raise ValueError("Comparison has no current reproducible winner; default remains unchanged")
    write_json(directory / "winner.json", evidence)
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True, help="JSON list: case, directory, optional review file")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--select-default", type=Path, help="Directory for the evidence-backed winner manifest")
    args = parser.parse_args()
    rows = json.loads(args.cases.read_text(encoding="utf-8"))
    report = compare([collect_case(r["directory"], r["case"], json.loads(Path(r["review"]).read_text(encoding="utf-8")) if r.get("review") else None) for r in rows])
    write_json(args.output, report)
    if args.select_default:
        publish_selection(args.output, args.select_default)
    print(json.dumps({k:v for k,v in report.items() if k != "records"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
