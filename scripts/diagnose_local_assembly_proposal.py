"""Replay one recorded local proposal without executing assembly operations."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from car2lego3d.assembly_protocol import DECISION
from car2lego3d.common import digest, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--step", type=int, default=0)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new diagnostic output; preserve prior evidence")
    trace = args.trace.resolve()
    with trace.open(encoding="utf-8") as stream:
        matches = [row for line in stream if line.strip()
                   if (row := json.loads(line))["step"] == args.step]
    if len(matches) != 1:
        parser.error("Expected one recorded decision at this step")
    record = matches[0]
    source = (trace.parent / record["input_file"]).resolve()
    if not source.is_relative_to(trace.parent) or digest(source) != record["input_sha256"]:
        parser.error("Recorded input file integrity failed")
    message = json.dumps(json.loads(source.read_text(encoding="utf-8")), ensure_ascii=False, separators=(",", ":"))
    if message != record["input_text"]:
        parser.error("Recorded input text differs from the complete source payload")
    training = Path(__file__).resolve().parents[1] / "training/assembly_model"
    sys.path.insert(0, str(training))
    from serve import Runtime

    runtime = Runtime(adapter=args.adapter, max_context=1024)
    if runtime.name != record["planner"]:
        parser.error("Replay model differs from the recorded planner")
    report = dict(schema="assembly-proposal-replay.v1", step=args.step,
        trace=str(trace), trace_sha256=digest(trace), input_sha256=digest(source),
        input_text_sha256=sha256(message.encode()).hexdigest(), identity=runtime.identity(),
        runtime_sources={name: digest(training / name) for name in ("serve.py", "common.py", "gpu_lock.py")},
        original_decision=record["decision"], operations_executed=0,
        scope="New deterministic inference replay; original HTTP error body was not retained",
        proposal_returned=False, quality_accepted=False, winner=None)
    body = dict(model=runtime.name, messages=[dict(role="user", content=message)],
        temperature=0, max_tokens=256, response_format=dict(type="json_schema",
            json_schema=dict(name="assembly_decision", strict=True, schema=DECISION)))
    started = time.monotonic()
    try:
        report["response"] = runtime.infer(body)
        report["proposal_returned"] = True
    except Exception as error:
        report["error"] = dict(type=type(error).__name__, message=str(error))
    report.update(inference_evidence=runtime.last_inference,
        elapsed_seconds=time.monotonic() - started, identity_after=runtime.identity())
    write_json(args.output, report)
    print(json.dumps(dict(output=str(args.output), proposal_returned=report["proposal_returned"],
        error=report.get("error"), elapsed_seconds=report["elapsed_seconds"])))


if __name__ == "__main__":
    main()
