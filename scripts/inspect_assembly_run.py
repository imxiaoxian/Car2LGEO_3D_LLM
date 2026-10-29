"""Read-only progress and prompt-size diagnostics for a live module run."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

from car2lego3d.assembly_prompt_codec import unpack_ports
from car2lego3d.common import write_json


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def pending_execution(attempt, state):
    if not attempt or state.get("status") != "running":
        return None
    step = state.get("steps", 0)
    request = attempt / "inputs" / f"{step:04d}.json"
    if not request.is_file():
        return dict(step=step, phase="checkpoint_or_context")
    decision_file = attempt / "planner" / f"{step:04d}" / "decision.json"
    decision = load(decision_file)
    if decision:
        phase, since = "tool_or_checkpoint", decision_file
    else:
        phase, since = "planner", request
    return dict(step=step, phase=phase, tool=decision.get("tool"),
        elapsed_seconds=round(max(0, time.time() - since.stat().st_mtime), 1),
        evidence=str(since),
        scope="Artifact timing; process stack or job controls distinguish active work from a checkpoint wait.")


def part_diagnosis(part, result):
    ports = unpack_ports(result.get("ports", []))
    return dict(part=part, bytes=size(result), ports=len(ports),
        port_kinds=dict(Counter(port.get("kind", "unknown") for port in ports)))


def decision_timings(attempt):
    if not attempt:
        return []
    timings = []
    for request in sorted((attempt / "inputs").glob("*.json")):
        folder = attempt / "planner" / request.stem
        decision = folder / "decision.json"
        if not decision.is_file():
            continue
        item = dict(step=int(request.stem), tool=load(decision).get("tool"),
            request_to_decision_seconds=round(max(0, decision.stat().st_mtime - request.stat().st_mtime), 1))
        following = attempt / "inputs" / f"{int(request.stem) + 1:04d}.json"
        if following.is_file():
            item["decision_to_next_input_seconds"] = round(
                max(0, following.stat().st_mtime - decision.stat().st_mtime), 1)
        events = folder / "events.jsonl"
        if events.is_file():
            for line in events.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "turn.completed":
                    item["usage"] = event.get("usage", {})
        timings.append(item)
    return timings


def motion_diagnosis(report, revision):
    if not report:
        return None
    joints = []
    for row in report.get("joints", []):
        failed = [pose for pose in row.get("poses", []) if pose.get("status") != "pass"]
        pairs = Counter(tuple(sorted((collision.get("a", "unknown"), collision.get("b", "unknown"))))
            for pose in failed for collision in pose.get("collisions", []))
        joints.append(dict(instance=row["instance"], purpose=row.get("purpose"),
            samples=len(row.get("poses", [])), limits=row.get("limits"), unit=row.get("unit"),
            range_issues=row.get("range_issues", []), nonpassing_poses=len(failed),
            pose_failures=[dict(value=pose.get("value"), status=pose.get("status"),
                connectivity=pose.get("connectivity"), collisions=len(pose.get("collisions", [])),
                unknown=pose.get("unknown"), invalid=pose.get("invalid")) for pose in failed[:12]],
            pose_failures_omitted=max(0, len(failed)-12),
            collision_pairs=[dict(a=pair[0], b=pair[1], occurrences=count)
                             for pair, count in pairs.most_common(12)],
            collision_pairs_omitted=max(0, len(pairs)-12)))
    combined = []
    for row in report.get("combined", []):
        statuses = Counter(pose.get("status", "unverified") for pose in row.get("poses", []))
        combined.append(dict(instances=row["instances"], samples=sum(statuses.values()), statuses=dict(statuses)))
    return dict(revision=report.get("revision"), current=report.get("revision") == revision,
        status=report.get("status"), joints=joints, combined=combined,
        combined_joint_states=report.get("combined_joint_states"),
        unknown_joint_purposes=report.get("unknown_joint_purposes", []),
        mechanism_response=report.get("mechanism_response"),
        scope="Diagnostic summary only. Carrier-only clearance does not certify installed wheel response.")


def inspect(directory):
    directory = Path(directory)
    schedule = load(directory / "module-tasks.json")
    checks = load(directory / "checks.json")
    assembly_state = load(directory / "state.json")
    motion = load(directory / "motion.json")
    active = schedule.get("active")
    task = schedule.get("tasks", {}).get(active, {})
    attempts = task.get("attempts", [])
    attempt = directory / attempts[-1]["directory"] if attempts else None
    state = load(attempt / "agent.json") if attempt else {}
    inputs = sorted((attempt / "inputs").glob("*.json")) if attempt else []
    payload = load(inputs[-1]) if inputs else {}
    evidence = payload.get("known_evidence", {})
    parts = evidence.get("parts", {})
    part_summary = [part_diagnosis(part, result) for part, result in parts.items()]
    latest = []
    candidates = []
    if active:
        for candidate_path in sorted((directory / "module-candidates" / active).glob("*/candidate.json")):
            record = load(candidate_path)
            measurement = record.get("measurement", {})
            candidates.append(dict(id=record.get("id"), structure_id=record.get("structure_id", record.get("id")),
                status=measurement.get("status"), label=record.get("label"),
                failures=measurement.get("failures", []),
                scope="Saved measurement only; dependency freshness and current selection require executor verification."))
    trace = attempt / "agent-decisions.jsonl" if attempt else None
    if trace and trace.is_file():
        # Stream complete lines; input_text is intentionally excluded from the report.
        with trace.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # A live writer may not have completed its final line.
                observation = record.get("observation", {})
                latest.append(dict(step=record.get("step"), decision=record.get("decision"),
                    ok=observation.get("ok"), error=observation.get("error"),
                    revision=observation.get("revision_after")))
                latest = latest[-4:]
    return dict(schema="assembly-run-diagnosis.v1", read_only=True, directory=str(directory.resolve()),
        progress=dict(active=active, complete=schedule.get("complete"), required=schedule.get("required"),
            status=state.get("status"), steps=state.get("steps"), reason=state.get("stop_reason")),
        pending_execution=pending_execution(attempt, state),
        structural_check=dict(revision=checks.get("revision"), status=checks.get("status"),
            current=checks.get("revision") == assembly_state.get("revision") if checks else False,
            parts=checks.get("coverage", {}).get("parts"), connections=len(checks.get("connections", [])),
            components=len(checks.get("components", [])), collisions=len(checks.get("collisions", [])),
            unknown=len(checks.get("unknown", []))),
        motion_check=motion_diagnosis(motion, assembly_state.get("revision")),
        prompt=dict(file=str(inputs[-1]) if inputs else None, bytes=size(payload),
            fields={key: size(value) for key, value in payload.items()},
            queried_parts=sorted(part_summary, key=lambda row: row["bytes"], reverse=True)),
        recent_decisions=latest,
        candidate_measurements=candidates,
        decision_timings=decision_timings(attempt),
        timing_scope="Filesystem intervals include checkpoint pauses and process overhead; they are not pure API or tool latency.",
        limits="Local module evidence only; it does not certify a complete vehicle or appearance.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("Use a new output path to preserve earlier evidence")
    result = inspect(args.directory)
    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
