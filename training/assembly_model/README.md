# Independent local assembly planner

The local LoRA model and local Codex are independent candidate planners. The
website supplies identical mission inputs and deterministic tool contracts to
each. Select a default from actual same-input quality evidence. Training loss,
successful proposal JSON and IO export alone are not vehicle acceptance.

## Isolated environment

Run from this repository. Environments and caches stay under this directory.
The main environment and D:/lego are not changed.

```powershell
uv venv --python .venv/Scripts/python.exe training/assembly_model/.venv
$env:UV_CACHE_DIR = 'D:/Car2LGEO_3D_LLM/training/assembly_model/cache/uv'
uv pip install --python training/assembly_model/.venv/Scripts/python.exe --index-url https://download.pytorch.org/whl/cu124 torch==2.5.1
uv pip install --python training/assembly_model/.venv/Scripts/python.exe -r training/assembly_model/requirements.txt
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/download_model.py
```

Model: Qwen/Qwen2.5-Coder-1.5B-Instruct, fixed upstream commit
2e1fd397ee46e1388853d2af2c993145b0f1098a. Download does not execute remote
code. runtime/model-cache.json records cached dependency hashes.
If the CDN returns an expired cached redirect, rerun download_model.py with
--fresh-weight-download. This requests a fresh address from the pinned upstream
revision, verifies the exact HTTP range on resume, then checks full size/SHA256.

Run the real BF16/LoRA kernel and memory probe after downloading:

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/capability_probe.py
```

This performs forward/backward on synthetic repeated tokens with no optimizer
step, never saves an adapter, and does not create training data. Its report is
capability-only and cannot qualify a model for website selection.

## Approved data and training

Each JSONL sample contains actual website user/assistant messages, source model,
family, module hashes, corpus snapshot, protocol hash and trajectory hash. It
also references evidence through `evidence: {path, sha256}`. Paths remain inside
the dataset directory. Evidence must contain execution_succeeded,
structure_passed and source_review_approved all equal to true; plus
trajectory_sha256, input_sha256 and decision_sha256. Message hashes bind the
exact UTF-8 content strings. The checked dataset builder generates evidence.
Parsing an IO successfully does not make it eligible. Test fixtures are never
emitted as training data.

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/train.py --dataset data/assembly-agent/training/train.jsonl --output training/assembly_model/outputs/run-001 --validate-only
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/train.py --dataset data/assembly-agent/training/train.jsonl --output training/assembly_model/outputs/run-001
```

When disjoint held-out partitions are available, supply them explicitly:

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/train.py --dataset data/assembly-agent/training/train.jsonl --validation-dataset data/assembly-agent/training/validation.jsonl --test-dataset data/assembly-agent/training/test.jsonl --output training/assembly_model/outputs/run-001 --validate-only
```

Use the same arguments without --validate-only to train. Each supplied partition
must be nonempty and evidence-verified, use the configured snapshot and one frozen
protocol, and have no family, module, model, trajectory or identical-input overlap
with another partition. The audit records partition file hashes. A training-only
dataset is labeled held_out_not_provided and cannot establish generalization.
One source group can support a capability experiment but not a held-out quality
comparison. Do not duplicate samples to inflate the dataset.

The first actual source-approved run is saved at
`runs/source-step-20261017/training-manifest.json`:153 training samples,
6 validation samples and3 test samples, with disjoint family/module/model/
trajectory/exact-input partitions. Training completed10 optimizer steps with
the default1024-token context and no exclusions. The adapter is
`runs/source-step-20261017/adapter`, labeled`trained_not_accepted`. Validation
and test each cover only one held-out family. See VALIDATION.md for exact
hashes and evidence; neither teacher-forced loss nor proposal validity selects
a default planner.

The first adapter subsequently failed an actual empty-scene multi-step tool
loop: zero of three expected parts and no successful mutation or finish.
Single-step wrapping and supervised loss do not establish loop competence.
VALIDATION.md records the failure and the lifecycle-data correction.

BF16 LoRA defaults: rank16, context1024, batch1, accumulation16 and gradient
checkpointing. CUDA OOM permits one retry with context512. Over-context samples
are explicitly excluded and counted; context is never silently truncated. Zero
eligible samples fail before GPU loading. The manifest records exact input hash,
hyperparameters and adapter hashes with trained_not_accepted status.
The validate-only command also tokenizes with the pinned cached tokenizer and
reports evidence-verified, fitting and excluded sample counts without loading
model weights. Missing tokenizer dependencies or an entirely over-context
dataset fail validation; approved provenance alone is not training readiness.
The manifest records actual optimizer steps and epochs, requested step limits,
partition audit, and only the complete training rows that fit the chosen context.
Each Trainer attempt records its context, outcome and actual steps. Optional
validation loss is measured before and after training with teacher forcing;
the test partition is audited but never used by training or validation. Loss is
not executed assembly quality. --max-steps N runs a bounded optimizer smoke test;
it is not a substitute for the configured epoch or an acceptance result.

## Complete lifecycle source demonstrations

`loop_data.py --regenerate` executes approved source placements using the frozen
current website context, then independently replays every input, decision and
tool observation. It retains context, inspect, remember, edit, check, export and
finish decisions. Finish requires a successful current-revision export and IO
roundtrip. Partial assemblies are labeled as actions within a checked completed
source module; they are not individually claimed to be structurally complete.

The original train/validation/test family and duplicate-module isolation is
retained. Held-out source demonstrations remain held out. Generic exact inputs
shared between partitions are excluded with held-out priority. No Codex test
trajectory becomes training data. The generator follows every context
`next_offset` before taking the next source action.

```powershell
.venv/Scripts/python.exe training/assembly_model/loop_data.py --dataset data/assembly-agent/source-demonstrations-20261017/dataset --snapshot 668d4cc69acf48633f657ea63666f1283e57889ad025cbeb6038d0527056c158 --output training/assembly_model/datasets/lifecycle-v3-20261018 --regenerate --defer-slow --contract data/assembly-agent/executed-benchmark-v3-final-20261018/contract.json
```

Output directories must be new or empty. `--audit-only` replays old-context
sources without emitting training JSONL; an execution-hash change intentionally
rejects them. After generation, confirm report execution/protocol hashes match
the frozen runtime, run the pinned tokenizer validation, then train to a new run
directory. A completed scripted source demonstration is training provenance,
not autonomous model acceptance. Preserve earlier adapters and failed loops.

`--contract` verifies the frozen context, execution, protocol, skill, snapshot
and implementation file hashes before each source and before publication.
`--resume` can reuse only a complete successful demonstration under that exact
contract; incomplete attempts remain preserved with retry suffixes.
`--defer-slow` changes execution order without changing source indices or splits.
`report.partial.json` is progress only; any rejected source prevents publication
of a training dataset.

The frozen-v3 run completed all 57 source demonstrations and independent
replays with zero rejected sources: 992 train, 24 validation and 18 test
decisions. Before training, audit provenance and exact runtime budgets:

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/lifecycle_readiness.py --dataset training/assembly_model/datasets/lifecycle-v3-20261018 --contract data/assembly-agent/executed-benchmark-v3-final-20261018/contract.json --output training/assembly_model/runtime/lifecycle-v3-20261018-readiness.json
training/assembly_model/.venv/Scripts/python.exe -u training/assembly_model/train.py --dataset training/assembly_model/datasets/lifecycle-v3-20261018/train.jsonl --validation-dataset training/assembly_model/datasets/lifecycle-v3-20261018/validation.jsonl --test-dataset training/assembly_model/datasets/lifecycle-v3-20261018/test.jsonl --protocol-hash 5b724c029102745b9d154c42ffabd72fbc8ad326db18a7b5c25642c604f1d9f1 --output training/assembly_model/runs/lifecycle-v3-20261018 --validate-only
```

The audit checks complete training length, prompt plus the full 256-token output
budget, and assistant length separately. It does not truncate or exclude
over-budget examples. On 2026-10-18 all examples fit, with maximum runtime
length 775/1024 tokens. The actual full-epoch command is the validation command
without `--validate-only` and without `--max-steps`. See STATUS-20261018.md for
the current process and logs rather than inferring training from this recipe.

After training completes, bind the adapter to the exact audit and frozen host:

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/release_readiness.py --run training/assembly_model/runs/lifecycle-v3-20261018 --readiness training/assembly_model/runtime/lifecycle-v3-20261018-readiness.json --output training/assembly_model/runtime/lifecycle-v3-20261018-release-readiness.json
```

This rechecks every adapter dependency hash, data hash, frozen implementation,
configuration, context, exclusions, optimizer count and actual epoch. A short
max-steps run cannot pass this full-epoch gate. Its successful status means ready
for independent comparison, with winner null and quality_accepted false. Four
negative/positive regression tests cover this publication audit; fixtures
never enter training.

## Local inference service

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/serve.py --port 8781
# Only after real training:
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/serve.py --port 8783 --adapter training/assembly_model/runs/lifecycle-v3-20261018/adapter
```

The historical 2026-10-17 adapter remains at
`runs/source-step-20261017/adapter`; do not overwrite or confuse it with the
new lifecycle-v3 adapter.

Configure the website with:

```text
ASSEMBLY_LOCAL_BASE_URL=http://127.0.0.1:8781
ASSEMBLY_LOCAL_BASE_MODEL=local_base
ASSEMBLY_LOCAL_FINETUNED_URL=http://127.0.0.1:8783
ASSEMBLY_LOCAL_FINETUNED_MODEL=local_finetuned
```

Loopback binding only. GET /health reports listening, not successful GPU
inference. It identifies the pinned base, adapter manifest/hash inventory,
maximum context and the unaccepted quality state. Adapters must include their
actual positive optimizer step count in the training manifest; readiness probes
with zero training steps cannot be served as fine-tuned models. Health exposes
the recorded optimizer steps and epochs without treating them as quality.
Adapters must include their
LoRA config and safetensors in the hash inventory, and are checked before every
inference. POST /v1/chat/completions accepts text requests with temperature0 and
the assembly JSON decision schema. The complete prompt is counted on CPU before
the GPU lock or weight loading. Context rejection is HTTP400 with actual input,
requested output and context limit; there is no truncation. The website requests
256 output tokens with assembly-context.v3 and compact wire_json serialization.
The service defaults to the measured context1024 configuration; --max-context
may be explicitly increased up to the pinned base's32768-token limit, but this
does not establish a larger adapter training budget or GPU memory acceptance.
A context1024 run therefore requires the complete chat-template input to fit
within768 tokens; changing a token budget requires the same comparison budget
for both planners. Explicit module/context pages replace full-scene dumps.
The shared website contract uses context pages of at most400 characters and
work notes of at most128 characters. Longer tasks and error details remain
available as complete queryable resources. Character counts do not guarantee
token fit for all text; the pinned tokenizer check remains authoritative.
Oversized pages fail explicitly rather than removing evidence from a prompt.
HTTP429 means the local service has another admitted request;
HTTP503 means the cooperative GPU lock timed out; HTTP422 means a failed
proposal, including invalid JSON or exhausted output budget.
The website validates and executes every proposal. evaluate.py replays held-out
proposal cases and records the service identity before/after; full structural
and visual evaluation belongs to the website. A missing or failed model is not
automatically replaced by the other planner during a comparison.

Replay an actual held-out JSONL with exact website messages and input hashes:

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/evaluate.py --cases data/assembly-agent/evaluation/held-out.jsonl --url http://127.0.0.1:8781 --model local_base --output training/assembly_model/runtime/base-proposals.json
# Repeat against port 8783 with --model local_finetuned and a separate output.
```

Each case requires id, one user message, its input_sha256 and decision_schema.
Only schema-valid complete responses count as returned proposals. This replay
does not execute assembly operations and cannot select the default planner.
Its --max-tokens option defaults to256, matching the website decision budget;
the chosen budget is stored in the replay report.

The frozen-v3 formal module comparison was run independently for all three
candidates on the same input. Codex (gpt-6.1-sol) passed 3/3 exact placements
with zero tool errors in 337.594 seconds; the untrained base failed 0/3 with
three planner errors in 24.219 seconds; the lifecycle-v3 adapter passed 3/3 with
three successful mutations and two recovered tool errors in 121.297 seconds.
`comparison.json` intentionally leaves `winner=null` and `quality_accepted=false`
because the suite covers supplied source placements, not autonomous image-to-car
creation, appearance, Studio acceptance or physical buildability. These results
are evidence for independent candidate comparison only; no planner fallback or
chaining was used.
HTTP rejection reports retain the server's complete structured error and
status, including actual context counts. Inputs are hash-verified and sent
unchanged; a failed proposal never becomes a synthesized tool action.

Measure exact saved website inputs using the pinned local tokenizer, without
loading weights or acquiring GPU memory:

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/context_budget.py --cases data/assembly-agent/evaluation/held-out.jsonl --output training/assembly_model/runtime/context-budget.json
# Or --payload path/to/input.json for one exact compact website payload.
```

Defaults are context1024 and output256. The report preserves input hashes and
per-case input/output totals; any oversized case exits with failure. A passing
budget check proves only context fit, not model or assembly quality.

For a reproducible contract-only stress sample, generate scenarios through
the actual website context and context-page code, then measure them:

```powershell
.venv/Scripts/python.exe training/assembly_model/context_scenarios.py --output training/assembly_model/runtime/context-scenarios-400.jsonl
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/context_budget.py --cases training/assembly_model/runtime/context-scenarios-400.jsonl --output training/assembly_model/runtime/context-budget-400.json
```

These scenario fixtures carry fixture_only=true, lack approval evidence, and
cannot qualify for training. They cover English/Chinese pages, work notes,
long errors and long task text with the current website implementation.

To record one bounded real untrained-base proposal against that exact saved
website input, without starting a persistent service:

```powershell
training/assembly_model/.venv/Scripts/python.exe training/assembly_model/base_probe.py --cases training/assembly_model/runtime/context-scenarios-400.jsonl --output training/assembly_model/runtime/base-inference.json
```

This acquires the same GPU lock through Runtime.infer, loads the pinned model
and unloads after the request. The report preserves success or rejection,
input identity and token budget. No assembly operations or training occur;
an inference proposal cannot establish a winning planner.

## GPU cooperation

Training, inference and Blender must hold gpu_lock.GPULock for the complete GPU
resource lifetime. Default path: training/assembly_model/runtime/gpu.lock;
override using ASSEMBLY_GPU_LOCK_PATH. Windows msvcrt or POSIX flock releases
on process exit. Blender must use the same lock path. Inference loads GPU weights
inside the lock and unloads after every request. One inference request is
admitted per process; overlapping requests receive HTTP429 for website retry.

Contract tests need no weights or GPU:

```powershell
training/assembly_model/.venv/Scripts/python.exe -m unittest discover -s training/assembly_model -p test_training.py -v
# Website integration contract: use the existing website environment (httpx/scipy).
.venv/Scripts/python.exe -m unittest discover -s training/assembly_model -p test_localplanner.py -v
```
