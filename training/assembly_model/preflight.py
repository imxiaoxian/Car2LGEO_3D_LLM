"""Report readiness without starting training or claiming model acceptance."""
import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

from common import ROOT, MODEL_ID, REVISION, config, configure_cache, load_samples, read_json, tokenize_samples, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--protocol-hash")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/preflight.json")
    args = parser.parse_args()
    cfg = config(args.config)
    packages = {}
    for name in ("torch", "transformers", "peft", "accelerate", "huggingface-hub", "jsonschema"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    gpu = {}
    if packages["torch"]:
        import torch
        gpu = dict(cuda=torch.cuda.is_available(), bf16=torch.cuda.is_bf16_supported())
        if gpu["cuda"]:
            props = torch.cuda.get_device_properties(0)
            gpu.update(name=props.name, total_memory=props.total_memory)
    cached = ROOT / "runtime/model-cache.json"
    dataset = dict(eligible_samples=0, status="not_provided")
    if args.dataset:
        try:
            rows = load_samples(args.dataset, expected_snapshot=cfg["snapshot"], expected_protocol=args.protocol_hash)
            dataset = dict(eligible_samples=0, evidence_verified_samples=len(rows),
                status="tokenization_unverified", path=str(args.dataset.resolve()))
            configure_cache()
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False, local_files_only=True)
            encoded, skipped = tokenize_samples(rows, tokenizer, cfg["context_length"])
            dataset.update(eligible_samples=len(encoded), excluded_over_context=skipped,
                context_length=cfg["context_length"], status="verified")
        except Exception as exc:
            dataset.update(eligible_samples=0, status="rejected", error=str(exc))
    report = dict(schema="assembly-training-preflight.v1", python=sys.executable, model_id=MODEL_ID, revision=REVISION,
        packages=packages, gpu=gpu, model_cache_manifest=read_json(cached) if cached.exists() else None,
        dataset=dataset, training_started=False, trained_adapter=None, quality_comparison="not_performed")
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
