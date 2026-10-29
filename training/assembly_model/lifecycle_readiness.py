"""Audit executed lifecycle data and exact pinned-tokenizer runtime budgets."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from common import MODEL_ID, REVISION, configure_cache, file_hash, load_samples, read_json, write_json
from dataset_audit import audit_splits


def inspect_dataset(dataset, contract_path, output, context_length=1024, decision_tokens=256):
    dataset, contract_path = Path(dataset).resolve(), Path(contract_path).resolve()
    frozen = read_json(contract_path)
    report_path = dataset / "report.json"
    report = read_json(report_path)
    if (report.get("status") != "executed_replayed_not_quality_accepted" or report.get("rejected")
            or report.get("expected_sources") != 57 or len(report.get("sources", [])) != 57
            or report.get("contract", {}).get("sha256") != file_hash(contract_path)):
        raise ValueError("Expected all 57 executed and independently replayed frozen lifecycle sources")
    for key in ("execution_hash", "protocol_hash", "snapshot"):
        if report[key] != frozen[key]:
            raise ValueError("Dataset runtime contract differs: " + key)
    root = Path(__file__).resolve().parents[2]
    for relative, expected in frozen["implementation"].items():
        dependency = (root / relative).resolve()
        if not dependency.is_relative_to(root) or file_hash(dependency) != expected:
            raise ValueError("Frozen implementation differs: " + relative)
    splits = {name: load_samples(dataset / (name + ".jsonl"),
        expected_snapshot=frozen["snapshot"], expected_protocol=frozen["protocol_hash"])
        for name in ("train", "validation", "test")}
    audit = audit_splits(splits, expected_snapshot=frozen["snapshot"], expected_protocol=frozen["protocol_hash"])
    configure_cache()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False, local_files_only=True)
    measured, summaries = {}, {}
    for split, rows in splits.items():
        cases = []
        for index, row in enumerate(rows):
            messages = row["messages"]
            prefix = tokenizer.apply_chat_template(messages[:1], tokenize=True, add_generation_prompt=True)
            whole = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
            if whole[:len(prefix)] != prefix:
                raise ValueError("Assistant prefix mismatch")
            answer_tokens = len(whole) - len(prefix)
            cases.append(dict(index=index, input_sha256=sha256(messages[0]["content"].encode()).hexdigest(),
                input_tokens=len(prefix), assistant_tokens=answer_tokens, training_tokens=len(whole),
                runtime_tokens=len(prefix) + decision_tokens, tool=json.loads(messages[1]["content"])["tool"],
                fits=len(whole) <= context_length and len(prefix) + decision_tokens <= context_length
                    and answer_tokens <= decision_tokens))
        measured[split] = cases
        summaries[split] = dict(samples=len(cases), tool_counts=dict(Counter(c["tool"] for c in cases)),
            max_training_tokens=max(c["training_tokens"] for c in cases),
            max_runtime_tokens=max(c["runtime_tokens"] for c in cases),
            max_assistant_tokens=max(c["assistant_tokens"] for c in cases),
            oversized=sum(not c["fits"] for c in cases))
    result = dict(schema="assembly-lifecycle-readiness.v1", model_id=MODEL_ID, revision=REVISION,
        contract_path=str(contract_path), contract_sha256=file_hash(contract_path),
        context_length=context_length, decision_tokens=decision_tokens,
        dataset_report_sha256=file_hash(report_path),
        dataset_hashes={name: file_hash(dataset / (name + ".jsonl")) for name in splits},
        audit=audit, partitions=summaries, cases=measured,
        all_fit=all(c["fits"] for cases in measured.values() for c in cases),
        training_started=False, quality_accepted=False,
        scope="Executed-source provenance and token budgets only; not autonomous planner or whole-car acceptance")
    write_json(output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}))
    if not result["all_fit"]:
        raise ValueError("Lifecycle runtime/training/output budget exceeded; no silent truncation")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inspect_dataset(args.dataset, args.contract, args.output)
