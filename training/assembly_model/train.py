"""BF16 LoRA training from approved, actually executed assembly trajectories."""
from __future__ import annotations

import argparse
import gc
from hashlib import sha256
import json
from pathlib import Path
import time

from common import ROOT, MODEL_ID, REVISION, config, configure_cache, file_hash, load_samples, tokenize_samples, tokenize_sample_rows, write_json
from dataset_audit import audit_splits, partition_identity
from gpu_lock import GPULock


def train_once(rows, cfg, dataset_path, output, context_length, *, validation_rows=None, max_steps=None, split_audit=None, partition_hashes=None):
    configure_cache()
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments, set_seed

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("A BF16-capable CUDA device is required; no silent CPU/dtype substitution")
    set_seed(cfg["seed"])
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False, local_files_only=True)
    tokenizer.pad_token = tokenizer.eos_token
    encoded, skipped, included_rows = tokenize_sample_rows(rows, tokenizer, context_length)
    validation_encoded, validation_skipped = None, 0
    if validation_rows:
        validation_encoded, validation_skipped = tokenize_samples(validation_rows, tokenizer, context_length)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False,
        local_files_only=True, torch_dtype=torch.bfloat16, attn_implementation="sdpa")
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(r=cfg["lora_rank"], lora_alpha=cfg["lora_alpha"],
        lora_dropout=cfg["lora_dropout"], target_modules=cfg["target_modules"], bias="none", task_type="CAUSAL_LM"))
    model.enable_input_require_grads()

    def collate(batch):
        length = max(len(row["input_ids"]) for row in batch)
        result = {key: [] for key in ("input_ids", "attention_mask", "labels")}
        for row in batch:
            padding = length - len(row["input_ids"])
            result["input_ids"].append(row["input_ids"] + [tokenizer.pad_token_id] * padding)
            result["attention_mask"].append(row["attention_mask"] + [0] * padding)
            result["labels"].append(row["labels"] + [-100] * padding)
        return {key: torch.tensor(value, dtype=torch.long) for key, value in result.items()}

    args = TrainingArguments(output_dir=str(output), num_train_epochs=cfg["epochs"], max_steps=max_steps or -1,
        per_device_train_batch_size=cfg["batch_size"], gradient_accumulation_steps=cfg["gradient_accumulation_steps"],
        per_device_eval_batch_size=1,
        gradient_checkpointing=cfg["gradient_checkpointing"], gradient_checkpointing_kwargs={"use_reentrant": False},
        bf16=True, fp16=False, learning_rate=cfg["learning_rate"], max_grad_norm=cfg["max_grad_norm"],
        logging_steps=1, save_strategy="no", report_to=[], optim="adamw_torch", seed=cfg["seed"],
        dataloader_num_workers=0, remove_unused_columns=False)
    trainer = Trainer(model=model, args=args, train_dataset=encoded, eval_dataset=validation_encoded, data_collator=collate)
    started = time.time()
    phase = "baseline_validation"
    try:
        baseline_validation_metrics = trainer.evaluate(metric_key_prefix="baseline_validation") if validation_encoded else None
        phase = "training"
        outcome = trainer.train()
        phase = "validation"
        validation_metrics = trainer.evaluate(metric_key_prefix="validation") if validation_encoded else None
    except Exception as exc:
        write_json(output / f"attempt-context-{context_length}.json", dict(
            status="failed", context_length=context_length, phase=phase,
            optimizer_steps=int(trainer.state.global_step), actual_epochs=trainer.state.epoch,
            elapsed_seconds=time.time() - started, error_type=type(exc).__name__,
            error=str(exc), trained_adapter=None, quality_accepted=False))
        raise
    write_json(output / f"attempt-context-{context_length}.json", dict(
        status="training_completed_not_accepted", context_length=context_length,
        optimizer_steps=int(trainer.state.global_step), actual_epochs=trainer.state.epoch,
        elapsed_seconds=time.time() - started, quality_accepted=False))
    adapter_path = output / "adapter"
    trainer.save_model(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))
    trainable, total = model.get_nb_trainable_parameters()
    training_identity = partition_identity(included_rows)
    manifest = dict(schema="assembly-lora-training.v1", status="trained_not_accepted", model_id=MODEL_ID, revision=REVISION,
        config=cfg, context_length=context_length, dataset_path=str(dataset_path.resolve()), dataset_sha256=file_hash(dataset_path),
        samples=len(encoded), excluded_over_context=skipped, trainable_parameters=trainable, total_parameters=total,
        elapsed_seconds=time.time() - started, metrics=outcome.metrics, snapshot=cfg["snapshot"],
        optimizer_steps=int(trainer.state.global_step), actual_epochs=trainer.state.epoch, requested_max_steps=max_steps,
        validation_samples=len(validation_encoded or []), validation_excluded_over_context=validation_skipped,
        baseline_validation_metrics=baseline_validation_metrics, validation_metrics=validation_metrics,
        test_metrics=None, split_audit=split_audit, partition_hashes=partition_hashes,
        training_input_sha256=[sha256(row["messages"][0]["content"].encode()).hexdigest() for row in included_rows],
        protocol_hashes=sorted({row["source"]["protocol_hash"] for row in included_rows}),
        training_families=sorted(training_identity["families"]),
        training_module_hashes=sorted(training_identity["modules"]),
        adapter_files={str(p.relative_to(adapter_path)): file_hash(p) for p in adapter_path.rglob("*") if p.is_file()},
        acceptance="Requires same-input comparison against local Codex and untrained base; training loss is not acceptance")
    write_json(output / "training-manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--validation-dataset", type=Path)
    parser.add_argument("--test-dataset", type=Path)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--protocol-hash")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.max_steps is not None and args.max_steps < 1:
        parser.error("--max-steps must be positive")
    cfg = config(args.config)
    rows = load_samples(args.dataset, expected_snapshot=cfg["snapshot"], expected_protocol=args.protocol_hash)
    partitions = {"train": rows}
    partition_paths = {"train": args.dataset}
    for name, path in (("validation", args.validation_dataset), ("test", args.test_dataset)):
        if path:
            partitions[name] = load_samples(path, expected_snapshot=cfg["snapshot"], expected_protocol=args.protocol_hash)
            partition_paths[name] = path
    split_audit = audit_splits(partitions, expected_snapshot=cfg["snapshot"], expected_protocol=args.protocol_hash)
    partition_hashes = {name: file_hash(path) for name, path in partition_paths.items()}
    if args.validate_only:
        configure_cache()
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False, local_files_only=True)
        encoded, skipped = tokenize_samples(rows, tokenizer, cfg["context_length"])
        held_out_fit = {}
        for name, samples in partitions.items():
            if name != "train":
                fitting, excluded = tokenize_samples(samples, tokenizer, cfg["context_length"])
                held_out_fit[name] = dict(fitting_samples=len(fitting), excluded_over_context=excluded)
        print(json.dumps(dict(evidence_verified_samples=len(rows), eligible_samples=len(encoded),
            excluded_over_context=skipped, context_length=cfg["context_length"],
            dataset_sha256=file_hash(args.dataset), split_audit=split_audit, held_out_fit=held_out_fit,
            partition_hashes=partition_hashes, training_started=False, optimizer_steps=0)))
        return
    args.output = args.output.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Training output directory must be new or empty")
    args.output.mkdir(parents=True, exist_ok=True)
    with GPULock(purpose="training", timeout=600):
        try:
            result = train_once(rows, cfg, args.dataset, args.output, cfg["context_length"],
                validation_rows=partitions.get("validation"), max_steps=args.max_steps,
                split_audit=split_audit, partition_hashes=partition_hashes)
        except Exception as exc:
            import torch
            if not isinstance(exc, torch.cuda.OutOfMemoryError):
                raise
            write_json(args.output / "oom-retry.json", dict(context=cfg["context_length"], retry_context=cfg["oom_context_length"], error=type(exc).__name__))
            # The failed frame's traceback retains the model until explicitly released.
            exc.__traceback__ = None
            gc.collect()
            torch.cuda.empty_cache()
            result = train_once(rows, cfg, args.dataset, args.output, cfg["oom_context_length"],
                validation_rows=partitions.get("validation"), max_steps=args.max_steps,
                split_audit=split_audit, partition_hashes=partition_hashes)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
