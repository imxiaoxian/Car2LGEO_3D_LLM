"""Bind a completed full-epoch adapter to its executed-data/frozen-contract audit."""
import argparse
from math import ceil, isclose
from pathlib import Path
import json

from common import MODEL_ID, REVISION, config, file_hash, read_json, write_json
from serve import validate_adapter


def inspect_release(run, readiness_path, output):
    run, readiness_path = Path(run).resolve(), Path(readiness_path).resolve()
    readiness = read_json(readiness_path)
    if (readiness.get("schema") != "assembly-lifecycle-readiness.v1"
            or readiness.get("all_fit") is not True
            or readiness.get("audit", {}).get("status") != "disjoint"
            or readiness.get("model_id") != MODEL_ID or readiness.get("revision") != REVISION):
        raise ValueError("Requires successful pinned-model lifecycle readiness audit")
    contract_path = Path(readiness["contract_path"])
    if file_hash(contract_path) != readiness["contract_sha256"]:
        raise ValueError("Frozen contract changed after dataset readiness audit")
    contract = read_json(contract_path)
    root = Path(__file__).resolve().parents[2]
    for relative, digest in contract["implementation"].items():
        dependency = (root / relative).resolve()
        if not dependency.is_relative_to(root) or file_hash(dependency) != digest:
            raise ValueError("Frozen implementation differs: " + relative)
    manifest_path = run / "training-manifest.json"
    manifest = validate_adapter(run / "adapter")
    cfg = config()
    if manifest["config"] != cfg:
        raise ValueError("Actual training configuration differs from configured full epoch")
    if (cfg["dtype"] != "bfloat16" or cfg["lora_rank"] != 16 or cfg["epochs"] != 1
            or cfg["batch_size"] != 1 or cfg["gradient_accumulation_steps"] != 16
            or manifest.get("requested_max_steps") is not None):
        raise ValueError("Expected configured BF16 rank-16 full epoch without a step override")
    dataset = Path(manifest["dataset_path"]).parent
    if (file_hash(dataset / "report.json") != readiness["dataset_report_sha256"]
            or manifest["partition_hashes"] != readiness["dataset_hashes"]):
        raise ValueError("Adapter was not trained on the audited executed lifecycle dataset")
    for split, digest in readiness["dataset_hashes"].items():
        if file_hash(dataset / (split + ".jsonl")) != digest:
            raise ValueError("Dataset partition changed: " + split)
    if (manifest["dataset_sha256"] != readiness["dataset_hashes"]["train"]
            or manifest["samples"] != readiness["partitions"]["train"]["samples"]
            or manifest["validation_samples"] != readiness["partitions"]["validation"]["samples"]
            or manifest["excluded_over_context"] != 0
            or manifest["validation_excluded_over_context"] != 0
            or manifest["context_length"] != readiness["context_length"]):
        raise ValueError("Full audited partitions were not trained/validated without exclusions")
    if (manifest.get("split_audit") != readiness["audit"]
            or manifest.get("protocol_hashes") != [contract["protocol_hash"]]
            or manifest.get("snapshot") != contract["snapshot"]
            or cfg["protocol_hash"] != contract["protocol_hash"]
            or cfg["decision_tokens"] != readiness["decision_tokens"]):
        raise ValueError("Adapter contract or partition isolation differs from audited inputs")
    expected_steps = ceil(manifest["samples"] / (cfg["batch_size"] * cfg["gradient_accumulation_steps"]))
    if (manifest["optimizer_steps"] != expected_steps
            or not isclose(manifest.get("actual_epochs", 0), cfg["epochs"], abs_tol=1e-8)):
        raise ValueError("Actual optimizer steps/epochs do not complete the configured full epoch")
    attempt_path = run / f"attempt-context-{manifest['context_length']}.json"
    attempt = read_json(attempt_path)
    if (attempt["status"] != "training_completed_not_accepted"
            or attempt["optimizer_steps"] != manifest["optimizer_steps"]
            or attempt["actual_epochs"] != manifest["actual_epochs"]):
        raise ValueError("Attempt evidence and trained adapter disagree")
    result = dict(schema="assembly-adapter-release-readiness.v1", status="trained_ready_for_independent_comparison",
        model_id=MODEL_ID, revision=REVISION, run_path=str(run), adapter_path=str(run / "adapter"),
        manifest_sha256=file_hash(manifest_path), readiness_sha256=file_hash(readiness_path),
        contract_path=str(contract_path), contract_sha256=file_hash(contract_path),
        execution_hash=contract["execution_hash"], protocol_hash=contract["protocol_hash"],
        skill_hash=contract["skill_hash"], context_version=contract["context_version"], snapshot=contract["snapshot"],
        samples=manifest["samples"], validation_samples=manifest["validation_samples"],
        optimizer_steps=manifest["optimizer_steps"], expected_optimizer_steps=expected_steps,
        actual_epochs=manifest["actual_epochs"], context_length=manifest["context_length"],
        adapter_files=manifest["adapter_files"], partition_hashes=manifest["partition_hashes"],
        attempt_sha256=file_hash(attempt_path), elapsed_seconds=manifest["elapsed_seconds"],
        baseline_validation_metrics=manifest["baseline_validation_metrics"], validation_metrics=manifest["validation_metrics"],
        quality_accepted=False, winner=None, inference_verified=False,
        acceptance="Requires independently executed same-mission Codex/base/adapter comparisons; loss and export are not vehicle acceptance")
    write_json(output, result)
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inspect_release(args.run, args.readiness, args.output)
