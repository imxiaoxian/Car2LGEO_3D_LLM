"""Bounded real base inference using an exact website context fixture."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

from common import file_hash, write_json
from serve import Runtime

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from car2lego3d.assembly_protocol import DECISION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--case-id", default="initial")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()
    if not 1 <= args.max_tokens <= 4096:
        parser.error("--max-tokens must be in 1..4096")
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    matches = [case for case in cases if case.get("id") == args.case_id]
    if len(matches) != 1:
        raise ValueError("Expected one exact case ID")
    case = matches[0]
    if len(case["messages"]) != 1 or case["messages"][0].get("role") != "user":
        raise ValueError("Probe requires the complete website user message")
    input_hash = sha256(case["messages"][0]["content"].encode()).hexdigest()
    if input_hash != case["input_sha256"]:
        raise ValueError("Exact website input hash mismatch")
    runtime = Runtime(max_context=1024)
    report = dict(schema="assembly-base-capability-probe.v1", model=runtime.name,
        fixture_only=bool(case.get("fixture_only")), source_cases_sha256=file_hash(args.cases),
        case_id=args.case_id, input_sha256=input_hash, max_tokens=args.max_tokens,
        service_identity=runtime.identity(), inference_executed=False,
        proposal_returned=False, operations_executed=0, training_samples=0, optimizer_steps=0,
        quality_accepted=False, winner=None,
        scope="Real untrained-base inference only; no assembly tools or quality acceptance")
    body = dict(model=runtime.name, messages=case["messages"], temperature=0,
        max_tokens=args.max_tokens, response_format=dict(type="json_schema",
            json_schema=dict(name="assembly_decision", strict=True, schema=DECISION)))
    started = time.monotonic()
    try:
        report["response"] = runtime.infer(body)
        report.update(inference_executed=True, proposal_returned=True)
    except Exception as exc:
        report["error"] = dict(type=type(exc).__name__, message=str(exc))
    finally:
        report["inference_evidence"] = runtime.last_inference
        report["inference_executed"] = bool((runtime.last_inference or {}).get("generation_completed"))
        report["elapsed_seconds"] = time.monotonic() - started
        write_json(args.output, report)
    print(json.dumps(dict(proposal_returned=report["proposal_returned"],
        elapsed_seconds=report["elapsed_seconds"], error=report.get("error"),
        quality_accepted=False, output=str(args.output))))
    if not report["proposal_returned"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
