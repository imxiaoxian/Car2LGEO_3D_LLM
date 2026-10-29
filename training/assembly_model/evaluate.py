"""Replay held-out proposer inputs; this is not full-car acceptance."""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from urllib.error import HTTPError
import time
from hashlib import sha256
from jsonschema import Draft202012Validator

from common import MODEL_ID, REVISION, file_hash, write_json


def identity(url, expected_model):
    with urlopen(url.rstrip("/") + "/health", timeout=15) as response:
        result = json.loads(response.read())
    if result.get("model") != expected_model or result.get("base_model") != MODEL_ID or result.get("revision") != REVISION:
        raise ValueError("Service model identity does not match the pinned comparison target")
    if expected_model == "local_finetuned" and not (result.get("adapter") or {}).get("manifest_sha256"):
        raise ValueError("Fine-tuned evaluation requires an identified training manifest")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", choices=["local_base", "local_finetuned"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()
    if not 1 <= args.max_tokens <= 4096:
        parser.error("--max-tokens must be in 1..4096")
    if urlsplit(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Evaluation service must be on loopback")
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases:
        raise ValueError("No held-out cases; proposal replay not performed")
    before = identity(args.url, args.model)
    results = []
    for case in cases:
        if len(case["messages"]) != 1 or case["messages"][0].get("role") != "user":
            raise ValueError("Replay must contain the exact website user payload")
        actual_hash = sha256(case["messages"][0]["content"].encode()).hexdigest()
        if actual_hash != case["input_sha256"]:
            raise ValueError("Replay input content hash mismatch")
        started = time.monotonic()
        body = dict(model=args.model, messages=case["messages"], temperature=0, max_tokens=args.max_tokens,
            response_format=dict(type="json_schema", json_schema=dict(name="assembly_decision", strict=True, schema=case["decision_schema"])))
        row = dict(id=case["id"], input_sha256=case["input_sha256"])
        try:
            request = Request(args.url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=900) as response:
                row["response"] = json.loads(response.read())
            completion = row["response"]
            if completion.get("model") != args.model or completion["choices"][0].get("finish_reason") != "stop":
                raise ValueError("Proposal model identity or completion state is invalid")
            decision = json.loads(completion["choices"][0]["message"]["content"])
            Draft202012Validator(case["decision_schema"]).validate(decision)
            if not isinstance(json.loads(decision["arguments_json"]), dict):
                raise ValueError("Proposal tool arguments are not an object")
            row["proposal_returned"] = True
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            try:
                details = json.loads(details)
            except json.JSONDecodeError:
                pass
            row.update(proposal_returned=False, http_status=exc.code, error=details)
        except Exception as exc:
            row.update(proposal_returned=False, error=str(exc))
        row["elapsed_seconds"] = time.monotonic() - started
        results.append(row)
    after = identity(args.url, args.model)
    report = dict(schema="assembly-proposal-replay.v1", model=args.model, service_identity=before,
        identity_unchanged=before == after, cases_sha256=file_hash(args.cases), results=results,
        max_tokens=args.max_tokens,
        scope="Proposal replay only; website must execute identical tools and measure structure/appearance to select a winner")
    write_json(args.output, report)
    if before != after:
        raise ValueError("Service model identity changed during evaluation; report is invalid")
    print(json.dumps(dict(cases=len(cases), proposals=sum(r["proposal_returned"] for r in results), output=str(args.output))))


if __name__ == "__main__":
    main()
