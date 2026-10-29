"""Audit retained trial traces without resuming models or changing their evidence."""
import argparse
from collections import Counter, deque
import json
from pathlib import Path

from car2lego3d.assembly_context import repeated_cycle
from car2lego3d.common import digest, write_json


def audit(directory):
    directory = directory.resolve()
    trace = directory / "agent-decisions.jsonl"
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    state = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    history = deque(maxlen=12)
    stall = None
    revisions, decisions, errors = [], Counter(), Counter()
    for index, record in enumerate(records):
        source = (directory / record["input_file"]).resolve()
        if (record["step"] != index or record["input_hash"] != state["input_hash"] or
                not source.is_relative_to(directory) or digest(source) != record["input_sha256"]):
            raise ValueError("Trace input integrity failed: " + str(source))
        payload = json.dumps(json.loads(source.read_text(encoding="utf-8")), ensure_ascii=False, separators=(",", ":"))
        if payload != record["input_text"]:
            raise ValueError("Trace payload differs from original input")
        observation = record["observation"]
        decisions[record["decision"]["tool"]] += 1
        if not observation["ok"]:
            errors[observation.get("error", "Failed")] += 1
        if observation.get("revision_after", 0) > observation.get("revision_before", 0):
            revisions.append([index, observation["revision_before"], observation["revision_after"]])
        history.append(record)
        if stall is None:
            stall = repeated_cycle(history, observation.get("revision_after"))
    return dict(directory=str(directory), planner=state["planner"],
        recorded_status=state["status"], stop_reason=state.get("stop_reason"),
        input_hash=state["input_hash"], steps=len(records), successful_mutations=len(revisions),
        revisions=revisions, decisions=dict(decisions), errors=dict(errors),
        retrospective_first_cycle=stall, elapsed_seconds=state["elapsed_seconds"],
        io_sha256=digest(directory / "model.io") if (directory / "model.io").exists() else None,
        sources={name: digest(directory / name) for name in ("agent.json", "agent-decisions.jsonl", "trial.json")},
        limitations=["Recorded running status does not establish that a process is alive",
            "Cycle detection is retrospective and did not stop the original run"],
        quality_accepted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new audit output; retained evidence is immutable")
    report = dict(schema="vehicle-trial-trace-audit.v1", auditor_sha256=digest(Path(__file__)),
        detector_sha256=digest(Path(__file__).resolve().parents[1] / "src/car2lego3d/assembly_context.py"),
        trials=[audit(directory) for directory in args.trial], winner=None, quality_accepted=False)
    if len({trial["input_hash"] for trial in report["trials"]}) != 1:
        raise ValueError("Independent trials did not use the same input")
    write_json(args.output, report)
    print(json.dumps(dict(output=str(args.output), trials=[dict(planner=trial["planner"],
        steps=trial["steps"], successful_mutations=trial["successful_mutations"],
        has_io=bool(trial["io_sha256"])) for trial in report["trials"]])))


if __name__ == "__main__":
    main()
