"""Probe BF16 LoRA forward/backward support; never update or save weights."""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import time

from common import ROOT, MODEL_ID, REVISION, config, configure_cache, write_json
from gpu_lock import GPULock


def probe(cfg, context_length):
    configure_cache()
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("A BF16-capable CUDA GPU is required")
    model = tokens = result = None
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False,
            local_files_only=True, torch_dtype=torch.bfloat16, attn_implementation="sdpa")
        model.config.use_cache = False
        model = get_peft_model(model, LoraConfig(r=cfg["lora_rank"], lora_alpha=cfg["lora_alpha"],
            lora_dropout=cfg["lora_dropout"], target_modules=cfg["target_modules"], bias="none", task_type="CAUSAL_LM"))
        model.enable_input_require_grads()
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.to("cuda").train()
        # Synthetic repeated tokens exercise kernels only; no optimizer or adapter is saved.
        seed = tokenizer.encode("Assembly capability probe.", add_special_tokens=False)
        ids = (seed * ((context_length + len(seed) - 1) // len(seed)))[:context_length]
        tokens = torch.tensor([ids], dtype=torch.long, device="cuda")
        result = model(input_ids=tokens, attention_mask=torch.ones_like(tokens), labels=tokens)
        loss_finite = bool(torch.isfinite(result.loss).item())
        if not loss_finite:
            raise RuntimeError("Capability loss is not finite")
        result.loss.backward()
        gradients = [(name, parameter.grad) for name, parameter in model.named_parameters() if parameter.requires_grad]
        connected = [name for name, grad in gradients if grad is not None]
        finite = all(bool(torch.isfinite(grad).all().item()) for _, grad in gradients if grad is not None)
        if not connected or not finite:
            raise RuntimeError("LoRA gradients are absent or not finite")
        trainable, total = model.get_nb_trainable_parameters()
        return dict(schema="assembly-lora-capability.v1", status="capability_only_not_trained_or_accepted",
            model_id=MODEL_ID, revision=REVISION, context_length=context_length, dtype="bfloat16",
            lora_rank=cfg["lora_rank"], gradient_checkpointing=True, trainable_parameters=trainable,
            total_parameters=total, parameters_with_gradients=len(connected), gradients_finite=finite,
            loss_finite=loss_finite, elapsed_seconds=time.monotonic() - started,
            peak_allocated_bytes=torch.cuda.max_memory_allocated(), peak_reserved_bytes=torch.cuda.max_memory_reserved(),
            optimizer_steps=0, training_samples_used=0, adapter_saved=False,
            scope="Synthetic kernel and memory probe only; no assembly decision, real training, or quality claim")
    finally:
        model = tokens = result = None
        gc.collect()
        torch.cuda.empty_cache()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--context-length", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/capability-probe.json")
    args = parser.parse_args()
    cfg = config(args.config)
    context_length = args.context_length or cfg["context_length"]
    if context_length not in {cfg["context_length"], cfg["oom_context_length"]}:
        raise ValueError("Probe context must use a configured training context")
    with GPULock(purpose="lora-capability-probe", timeout=600):
        try:
            report = probe(cfg, context_length)
        except Exception as exc:
            report = dict(schema="assembly-lora-capability.v1", status="failed_not_trained",
                model_id=MODEL_ID, revision=REVISION, context_length=context_length,
                error=type(exc).__name__ + ": " + str(exc), optimizer_steps=0, adapter_saved=False)
            # Remove frames retaining CUDA tensors before releasing the cooperative lock.
            exc.__traceback__ = None
            gc.collect()
            import torch
            torch.cuda.empty_cache()
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] == "failed_not_trained":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
