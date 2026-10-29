# Actual readiness evidence, 2026-10-18

The local Codex planner and fine-tuned planner are independent candidates. No
default winner is selected by this directory. An untrained base is a control.

## Frozen-v3 lifecycle data and current training

The parent froze context assembly-context.v3, execution
79fc81f82b18dfe384386ab970c9731f0c2d39771246136a9a77e4437b098a9d,
protocol 5b724c029102745b9d154c42ffabd72fbc8ad326db18a7b5c25642c604f1d9f1
and skill 8ee1d76e970c069f0a551eb26a9569f44d72ad1de49dd854a8bd0f785a2874c9.
All 57 reviewed source modules were actually executed and independently
replayed against that contract. No source was rejected. The new dataset is
datasets/lifecycle-v3-20261018: 992 train, 24 validation and 18 test decisions.
Family, module, source model, trajectory and exact-input audits are disjoint.
The generator checked all frozen implementation file hashes before every
source and before publishing. Codex test trajectories were not training data.

The strict readiness report at runtime/lifecycle-v3-20261018-readiness.json
verified all content-bound execution evidence and the fixed cached tokenizer.
Maximum train length is 670 tokens; maximum prompt plus the full 256 output
budget is 775; maximum assistant answer is 151. Validation and test maximum
runtime lengths are 753. All 1034 decisions fit without truncation or exclusions.
The separate training --validate-only command confirmed 992/992 eligible
train, 24/24 validation and 18/18 test. Six lifecycle/recovery/frozen-contract
tests, thirteen training/service/evidence/locking tests and two website
LocalPlanner HTTP contract tests passed under frozen v3.

Dataset SHA256 values:

- Train: 9158b88cbc6b21aa1bc528bfcc6938276f60467f7a267dfd151a47427f215552.
- Validation: bd7da61b5b472038a29d9580431ec2dabae84c52da9e627e7ef033e2f59101c1.
- Test: 29daa421a606671976b380b0bc190b090526d8b5d510a2c59fb043233824c784.

The full configured one-epoch BF16 rank-16 run started at 2026-10-18 00:58:26
Asia/Shanghai, launcher PID 40060 and actual Python child PID 7908. Output is
runs/lifecycle-v3-20261018; logs are runtime/lifecycle-v3-training.stdout.log and
runtime/lifecycle-v3-training.stderr.log. No max-steps limit is used. Training
completed all 62 optimizer steps and exactly 1.0 epoch, with 992 train and 24
validation examples, zero exclusions and no CUDA OOM/context fallback. Wall
time was 3844.2797 seconds including the resource contention described below.
Teacher-forced validation loss changed from 2.6027756 to 0.0112984; this is
supervised fit rather than assembly execution or appearance quality.

Release audit passed and bound the actual adapter to the data audit, fixed
runtime, every dependency hash and full-epoch count. Its report is
runtime/lifecycle-v3-20261018-release-readiness.json. Adapter manifest SHA256 is
6e1939de5aafb28c4ccdaf6b4bb9f9b2220545b005163cf0fdfb4b7a7e34ff66;
adapter_model.safetensors SHA256 is
cfa7d8bfd485d125f8a4c540ae56e004bb86b6e9f9024fd6d8a3aac7829f4643.
The new base listener 8781 (launcher 38224 / child 39828) and adapter listener
8783 (launcher 27052 / child 11120) both returned verified stable identities.
Listening is not inference acceptance; the formal frozen-suite executions run
serially without planner fallback. Base completed with zero of three parts,
zero mutations, three proposal errors and no digital/IO/finish in 24.219 seconds.
The new adapter execution completed on the same frozen task: 3/3 exact parts and
transforms, digital pass, archive roundtrip and planner finish, three successful
mutations, zero planner errors, two recoverable tool errors, 20 decisions and
121.297 seconds. The base remained 0/3 with three proposal errors. This is a
module-placement comparison only; it does not select a production planner or
accept vehicle appearance, Studio compatibility or physical buildability.

At 01:47 the run had completed 56/62 optimizer steps. Windows GPU process
counters showed 6.32 GB dedicated plus 2.57 GB shared memory for the trainer,
while a separate user game consumed 1.46 GB dedicated plus 0.27 GB shared.
The resulting paging pressure slowed optimization, without an OOM or restart.
The process and full-epoch configuration were preserved. After the user
released game resources, the remaining steps and final validation/save
completed. The authoritative current progress remains the log and
STATUS-20261018.md.

Read-only target isolation audit at runtime/lifecycle-v3-vehicle-isolation.json
found Countach family 10337, F40 families 10248/30192/76934, and BMW M4 family
42226 in the frozen corpus. None shares a source family, model or canonical
module hash with the 55 training trajectories. For Me has no name-matching
source; this cannot prove alias-complete source isolation. Known set families
76908/75890/76922 had no model records in the frozen snapshot. The audit does
not alter data, training or benchmark exclusions; name-based matches require
manual identity confirmation for later whole-car comparisons.

## Completed

- Isolated virtual environment installed and imported: torch 2.5.1+cu124,
  transformers 4.46.3, peft 0.13.2, accelerate 1.1.1, hub 0.26.2, jsonschema 4.26.0.
- Pinned Qwen revision 2e1fd397ee46e1388853d2af2c993145b0f1098a downloaded.
  model.safetensors: 3,087,467,144 bytes; SHA256
  c1b9b30e907950516ba3c646bdf570d8084c25a6410a0cdca80cf04b11bc13a8.
  runtime/model-cache.json records all nine dependency hashes.
- Real BF16 LoRA forward/backward succeeded on RTX 4060 Laptop GPU (8GB):
  context1024, rank16, checkpointing, 392 parameter tensors with finite gradients,
  elapsed10.75s, allocated peak5,441,988,608 bytes, reserved peak5,859,442,688 bytes.
  This was synthetic kernel validation with zero optimizer steps and no adapter.
  runtime/capability-probe.json contains the machine-readable result.
- Twelve stdlib training/HTTP/integrity/locking contract tests and two separate
  actual website LocalPlanner/replay HTTP contract tests passed. These cover
  default256 and explicit128 output budgets plus preserved HTTP400 context
  details. Fixture responses are not model or assembly quality evidence.
- Actual fixed tokenizer and HTTP service rejected a full over-limit request:
  input1645 + requested output4096 > configured context1024, returning HTTP400
  with counts and no silent truncation. runtime/service-contract.json records it.
  The test server was then stopped.
- Website decision output and proposal replay now share a256-token default.
  The service defaults to the actually memory-probed context1024 configuration.
  The prior1645+4096 rejection above remains historical evidence, not a test of
  the current compact protocol.
- After measuring an800-character page plus256-character work note at
  857 input+256 output=1113 tokens, the website contract was reduced to
  400-character pages and128-character work notes. Long task/error text remains
  explicitly queryable; it is not silently truncated.
- Fixed cached tokenizer measured seven scenarios generated through actual
  AssemblyAgent.context and AssemblyTools.tool_context, with full page wrappers
  and all four mission resources. Input+256 output totals were429,514,712,808,
  535,535 and791; all fit1024. runtime/context-budget-400.json preserves exact
  input hashes. These are contract fixtures, not training or quality evidence;
  other text can still exceed the budget and must be explicitly rejected.
- Real untrained-base inference on the saved initial website context completed
  on GPU:173 input tokens,88 generated tokens within the256-token budget,9.125s.
  It returned a Markdown-fenced decision with unsupported tool `read` and an
  object instead of the required `arguments_json` string. The service rejected
  the output; no assembly operation executed. runtime/base-inference-diagnostic.json
  preserves the exact input hash, full raw output and generation evidence.
  This is one control-case protocol failure, not a fine-tuned comparison.
  The GPU process exited after the bounded probe.
- Training verifies disjoint family, module, model, trajectory and exact-input
  identities across supplied partitions and records their file hashes. The
  regression fixtures cover leakage rejection and frozen snapshot/protocol
  requirements. Training manifests record the actual optimizer steps, epochs,
  included sample provenance and optional teacher-forced validation losses;
  test data is audit-only.
- The source-approved, structure-passing dataset at
  data/assembly-agent/source-demonstrations-20261017/dataset contains153 training,
  6 validation and3 test samples. Its cross-partition family/module/model/
  trajectory/exact-input audit passed. Training covers28 families and55 modules;
  validation and test each contain only1 family and1 module, so held-out
  coverage remains narrow. runtime/source-step-20261017-readiness.json records
  the exact data hashes and pinned-tokenizer fit: all samples fit context1024
  without exclusions or truncation. Maximum training input/answer total is463
  tokens; maximum held-out input plus256 output is544 tokens.
- A real bounded BF16 LoRA run completed10 optimizer steps,1.0457516339869282
  recorded epochs and153 samples, with zero context exclusions. Training
  runtime was67.8898s. Teacher-forced validation loss changed from
  0.9681475162506104 to0.029464326798915863. This measures supervised fit, not
  executed assembly quality. The actual adapter is
  runs/source-step-20261017/adapter; training-manifest.json in that run records
  exact hyperparameters, partition audit and adapter file hashes. Manifest
  SHA256:6bfc9a50a351e13827d619e0794b781b7321986b88ac319d59f6a8c241e002a5.
  adapter_model.safetensors SHA256:
  3e4ec50c5de2a47d1ef006f67f16d9f81b668f3eb978d6caf7d80ed61b2b2b7a.
  serve.validate_adapter checked its complete hash inventory successfully.
  Status remains trained_not_accepted.
- Exact held-out single-step replay completed on the same3 inputs and256-token
  output budget. The untrained base returned0/3 valid proposals (HTTP422); the
  trained adapter returned3/3, all matching the target tool and parsed arguments.
  These inputs already contain exact native placements, so this measures
  operation wrapping and protocol adherence, not autonomous part selection or
  spatial planning. No assembly tools executed. Service identities were stable.
  runtime/source-step-20261017-proposal-comparison.json links both complete
  reports and their hashes; winner remains null and quality_accepted is false.

## Not completed or accepted

- Equal-input whole-assembly comparison, appearance review and full-car IO
  acceptance remain outstanding. No measured assembly-quality gain is claimed.
- The 1024-token training budget can exclude complete website inputs. The
  validator reports this explicitly; a compact website contract must remain
  identical for both candidate planners. Inputs are never silently truncated.

Use README.md for preparation, validated-data training, serving and replay
commands. Real structural/visual evaluation and default selection belong to
the website and require identical missions, tool contracts and held-out data.

## Closed-loop failure and second-round preparation

The first adapter failed the actual empty-scene tool loop on held-out module
f32f810663dff8014a3ac291. The unchanged executor recorded zero of three expected
parts, zero successful mutations, three tool errors, one planner error, no IO
roundtrip and no planner finish in 27.64 seconds. Its first action guessed
JSON-patch-style `replace/path/value` operations, which the actual assembly
schema rejected. The record is
data/assembly-agent/executed-benchmark-20261017/local_finetuned/benchmark-result.json.
The earlier three exact single-step wraps remain valid historical observations,
but did not predict autonomous loop success.

Inspection of the 57 completed source demonstrations found 175 successful
context calls, 175 edits, 57 checks, 57 exports and 57 finishes. The original
dataset builder emitted mutation samples only, and the training loader also
rejected every finish decision. The loader now accepts finish only with
content-bound evidence of a current-revision successful export, verified IO
roundtrip and successful planner completion. Thirteen training contract tests,
two website LocalPlanner tests and two lifecycle paging/action tests passed.
All test fixtures remain excluded from training.

The independent old-context replay audit completed its first six source modules
with exact inputs, decisions, observations, structural checks and IO roundtrips.
The seventh reached a passing check, then was interrupted during an expensive
export before the executor context contract was revised. Its partial evidence
is preserved under datasets/source-lifecycle-audit-20261017-v1. That interrupted
v1 audit produced no complete training dataset. The new frozen-v3 run described
at the top actually executed and replayed every source from scratch; it did not
relabel historical contexts.
