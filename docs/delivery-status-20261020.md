# Delivery continuation, 2026-10-20

## Scope and acceptance

The delivery order remains Countach 5000 Quattrovalvole, Ferrari F40,
Nissan Skyline GT-R R34 and Bugatti Chiron Pur Sport. The corresponding
knowledge-base IO defines the required detail. All four fresh creations
remain undelivered. Archive, module, appearance, website, Studio and physical
acceptance remain separate.

Implementation stays in this checkout. `D:/lego` remains read-only. Existing
dirty files, failed revisions, baselines and source evidence are preserved.

## Runtime recovery

The user started frontend v25 on port 8778. Its process was confirmed to be
PID 59628. The backend on port 8777 was restarted with the planner failure
classification changes (listener PID 24460). These are observed process IDs,
not permanent service configuration.

The independent `countach-acceptance` browser session opened job
`9f4244f9ec5d4d23af3dc1ba6384427f`. Initial offscreen clicks generated no
request. After scrolling the resume button into view, an actual browser
click sent `POST /api/v1/designs/<id>/control/resume` and received HTTP 200.
No application change was needed for this browser interaction.

The supported recovery path created
`assembly-recovery-ce569f7cf83745adb37d682ecd8814d0` from the preserved
`assembly-recovery-fc11525eff344d8499bc4703a2adb0dd` revision. The new
worker rechecked chassis candidates and resumed the steering task. The
scheduler has one provisionally assembled module out of seventeen; this
does not establish independent semantic review or complete-vehicle acceptance.

Evidence lives in `runs/delivery-implementation-20261020/`:

- `resume-before.png` and `resume-running.png`: real browser states.
- `recovery-v26-audit.json`: source hashes, carried module signatures,
  candidate rechecks and ownership checks.
- `backend-v26.stdout.log` and `backend-v26.stderr.log`: backend startup logs.

## Planner service failure handling

Codex startup, timeout, nonzero exit and missing decision output now raise
`PlannerUnavailable`. Local-model transport errors and service HTTP errors
are classified separately from invalid decisions. HTTP 400/422 and invalid
decision JSON remain decision/request failures.

A planner service failure saves an interrupted checkpoint and diagnostic
trace without consuming another structure repair attempt or proceeding to
render/export. Recovery continues that attempt. Actual assembly failures
retain the three-attempt limit. A test executes a real edit before a service
interruption and verifies that recovery does not replay it.

## Verification

- Relevant agent, schedule, recovery, evidence and runtime regression:
  105 passed in 14.79 seconds.
- Frontend production build `.next-delivery-20261020-v26-check`: compilation,
  lint, type checks and page generation passed. The user's v25 server was
  kept running.
- Browser resume and real backend worker launch: verified as above.
- Complete fresh Countach model, final IO download/hash, appearance and Studio
  acceptance: outstanding. Other three fresh vehicle deliveries: outstanding.
- No planner winner, new formal training result, main merge or vehicle
  delivery is claimed from these checks.

## Module-scoped reference recovery (v27)

The v25 frontend remains on port 8778 (PID 59628). The backend was reloaded
on port 8777 (listener PID 47960) for module-scoped planner context. The
previous recovery was cancelled through job controls; its checkpoints and
artifacts remain intact. A real browser resume created
`assembly-recovery-21705f27c185475188ce66eaf529f2dc`.

The new run uses `assembly-context.v8` / `vehicle-scoped.v3`, with execution
hash `aa915e41e9afdf74c97d97d97cc372fde32620b83285d229aebd25648a79fe1d`.
The recovery audit passed: carried chassis signature unchanged, chassis
candidates rechecked, and no module ownership conflicts. Steering remains
in progress after the one provisionally assembled chassis module (1/17).

The 32 original Countach references produced 17 module contact sheets.
Each sheet selects at most eight views, is at most 1280 by 704 pixels, and
records source hashes and selection reasons. Right-door selection does not
default to opposite-door detail images. Every full-resolution original
remains addressable using `view_reference`. The steering sheet was inspected
visually and the live planner input attaches its exact file/hash.

The first new steering decision used 29,881 input tokens and reached a
decision in 44.4 seconds (filesystem interval). Earlier full-reference
inputs recorded roughly 53,000 to 61,000 tokens. This first observation
does not establish assembly throughput, API-only latency or model quality.

Evidence in `runs/delivery-implementation-20261020/`:

- `module-reference-audit.json` and `module-reference-audit/*.jpg`.
- `recovery-v27-audit.json`.
- `resume-module-scoped.png`.
- `backend-v27.stdout.log` and `backend-v27.stderr.log`.

Additional verification before recovery:

- Reference context, vehicle context, recovery, agent and website: 84 tests
  passed in 14.07 seconds.
- Schedule, mounts, requirements, evidence, runtime and references: 92 tests
  passed in 32.59 seconds.
- Frontend production build `.next-delivery-20261020-v27-check`: compilation,
  lint, type checks and page generation passed. The running v25 frontend
  was preserved.

The integration fixture was corrected to include the real reference schema
(expected shots, legacy projections and parent-image hashes); production
validation was not relaxed. These checks do not change vehicle acceptance.

## Live steering diagnosis after v25 confirmation

The frontend v25 (PID 59628), backend (PID 47960) and assembly worker
(Python PID 43252, launcher PID 47440) were verified alive on 2026-10-20.
The existing `countach-acceptance` browser session showed the same 1/17
module state as the API: chassis provisionally checked, steering running,
and vehicle, appearance and website acceptance unverified. No service was
restarted and no assembly input or execution code was changed during this
diagnosis.

Steering step 6 searched native filenames/descriptions for `steering`.
Process-stack samples showed `description_records` opening files in the
official and unofficial parts directories. Later samples progressed through
different filenames, and the worker continued reading from disk. The two
directories contained 12,132 and 24,212 DAT files respectively. This was
an active cold catalogue scan, not evidence of a deadlock or planner timeout.
The interval from decision 6 to input 7 was 538.0 seconds, including tool
execution and checkpoint/context overhead. The search completed successfully;
steps 7 and 8 then queried native geometry and ports for `2790a.dat` and
`2737.dat`. No steering placement had completed at this observation.

Later decision usage does not establish a stable token or latency reduction:

| Step | Tool | Reported input tokens | Request-to-decision seconds |
| --- | --- | ---: | ---: |
| 0 | remember | 29,881 | 44.4 |
| 1 | retrieve | 116,215 | 110.2 |
| 2 | query_module | 159,810 | 257.5 |
| 3 | query_module | 31,703 | 54.8 |
| 4 | query_part | 32,031 | 49.7 |
| 5 | remember | 66,527 | 49.4 |
| 6 | search_parts | 66,602 | 106.1 |
| 7 | query_part | 38,613 | 125.4 |
| 8 | query_part | 40,677 | 57.0 |

These are CLI-reported usage totals and filesystem intervals, not pure API
latency. CLI logs include internal stream-retry errors, so usage totals alone
do not prove monotonically growing prompt size. Hooks and configuration
warnings were also observed, but valid decisions still returned; no causal
claim or global CLI configuration change was made.

The read-only snapshot is
`runs/delivery-implementation-20261020/steering-timing-v27.json`.
All four complete fresh vehicle deliveries remain outstanding. Continued
module execution is not final digital, appearance or Studio acceptance.

## Safe-boundary recovery (v28)

The user-restored v25 frontend was verified on port 8778 (PID 61656).
The backend was reloaded on port 8777 (listener PID 51684) after checking
that no assembly worker was alive. Backend startup succeeded; no current
service-start policy block was observed. The existing frontend was preserved.

A real browser resume returned HTTP 200 and created
`assembly-recovery-1199a6b713f54af88808c57b1fc76d19` from preserved revision
`assembly-recovery-ee3bbf99f727477e958f0f7c09e6a609`. The recovery audit
passed: source files remained unchanged, the carried chassis signature
remained identical and passed the current checker, all three chassis
candidates were rechecked, and no binding ownership conflicts were found.

Recovery follows hash-validated completion history and selects a safe
snapshot that preserves every completed module signature and retained
geometry. It withdrew 22 unfinished steering instance paths from the new
revision; their geometry remains in the source revision. Two alternative
snapshots were rejected because they would change completed or retained
geometry. No failed steering assembly was carried as a pass.

The new worker uses `assembly-context.v9`. Planner context includes compact
collision pairs and motion failures for targeted repair. At the 15:13 local
audit, revision 39 had one provisionally assembled module out of seventeen
and was executing steering. A later browser read confirmed the same module
state, with digital vehicle, appearance and website acceptance unverified.

Verification before this recovery:

- Assembly inspection, agent, context, recovery, candidates, prompt codec,
  vehicle context, website, schedule, mounts, requirements, evidence and
  motion: 185 tests passed in 24.53 seconds.
- Frontend production build `.next-delivery-20261020-v28-check`: compilation,
  lint, type checks and page generation passed. It did not replace v25.
- Actual website resume and worker launch: verified. Complete fresh IO
  download/hash, vehicle appearance and Studio acceptance remain outstanding.

Evidence in `runs/delivery-implementation-20261020/` includes
`recovery-v28-audit.json`, `backend-v28.stdout.log` and
`backend-v28.stderr.log`. These results do not establish a planner winner,
new formal training result, main merge or completed vehicle delivery.

## Desktop task-list layout correction (v29)

The live v25 detail page had document width 8,550 pixels at a 1,258-pixel
viewport. The global `.quality-issues > div` flex rule also applied to
`.module-progress-list`, putting all seventeen module rows side by side.
The selector now excludes that list. A focused Playwright regression covers
all seventeen tasks, row stacking, viewport containment and document width.
The unchanged v25 page reproduced the failure; evidence is retained in
`runs/delivery-implementation-20261020/layout-before/`.

Production build `.next-delivery-20261020-v29` passed compilation, lint,
type checks and page generation. Its built stylesheet was loaded into
isolated test browsers against the real Countach detail page. At widths
1,258, 1,440 and 1,920, document width equalled viewport width, all seventeen
rows stacked vertically and every row remained within the viewport.
`built-css-1258.png`, `built-css-1440.png` and `built-css-1920.png` preserve
these screenshots; the 1,440-pixel image was visually inspected. This is
stylesheet validation, not proof that the live service has deployed v29.

The attempted validation-server startup was rejected by automatic approval
with `blocked by policy` and no further reason. The existing v25 listener
(PID 61656) was preserved. Live v29 validation remains pending the requested
manual frontend switch; the backend and assembly worker must stay running.
Only the generated v29 type-directory addition was removed from tsconfig;
all prior modifications remain intact.

## Steering collision repair in the live worker

The current recovery worker (PID 65356) continued its own frozen v9 input.
Revision 40 had thirteen parts in one connected component and one native
collider conflict between the chassis `3703.dat` and steering rack
`2790a.dat`. Decision 18 returned after about 445 seconds, within the
1,200-second planner deadline. It moved the rack and added two `3895.dat`
supports in a strict transaction, which passed validation and committed
revision 41. The earlier rejected carrier edit remains in the trace.

No live graph, execution source, skill or planner input was changed during
this diagnosis. Steering still needs its own candidates, articulation and
module acceptance; the complete Countach and the other three deliveries
remain outstanding.

## Managed frontend startup and live v29 verification

The current session successfully started and restarted v29 directly,
without an automatic-review rejection or a global permission change.
Historical `blocked by policy` responses still have no established cause;
they are not a current website-acceptance blocker. The service now uses
`scripts/manage-frontend.ps1`, with process identity checks, loopback binding,
build/PID records and separate stdout/stderr logs. See `frontend-service.md`.

After the launching command exited, status confirmed the managed frontend
was still running and listening. A managed restart replaced PID 61832 with
PID 34812 and returned HTTP 200. The API PID 51684 and assembly worker PID
65356 retained their original start times; neither was restarted.

The actual Countach page at viewport width 1258 now has document width
1243, with all seventeen module rows stacked inside the viewport. Both
1440/1920 cases in `frontend/e2e/creation-evidence.spec.cjs` passed against
the live v29 service (2 tests, 8.1 seconds); evidence is in
`runs/delivery-implementation-20261020/layout-after/`.

The Countach worker continues steering assembly in its frozen v9 context.
At revision 45, it was repairing steering pivot placement after a failed
motion sweep. No complete vehicle, final download, appearance acceptance,
Studio acceptance, planner comparison or main merge is established.

## Frontend persistence and steering diagnosis (v33)

After a further managed restart, v29 is running under PID 63136, created
at 16:23:30 local time. A subsequent independent status command confirmed
that it remained listening after its launcher exited. The real Countach
detail page returned HTTP 200 and contained build ID
`xxFHpe7KamVSJyF1z54K7`. API PID 51684 and assembly worker PID 65356
retained their original creation times. No manual frontend window is needed.

The read-only `scripts/inspect_assembly_run.py` now distinguishes current
and stale structural checks and includes motion mechanism-response reasons,
pose failure totals, bounded pose details, joint range issues and combined
steering/suspension status. It does not modify the planner, graph or any
execution-hashed source. The saved observation is
`runs/delivery-implementation-20261020/steering-motion-diagnosis-v33.json`.

At that observation the worker was alive at steering step 64, graph
revision 48, with one of seventeen modules provisionally assembled. Both
steering carriers had 21 passing poses over -25 to +25 degrees. Whole-car
motion remained failed because both front wheels were not yet installed.
The structural pass on disk belonged to revision 46 and is now explicitly
shown as stale. These results do not establish installed-wheel response.

Inspection also identified an actionable planner-context omission:
`assembly_inspection.planning_evidence` retained joint failures but omitted
top-level `mechanism_response`. This hid the missing-wheel reason behind
an aggregate failure. The live worker continued querying and comparing
steering alternatives; its frozen execution implementation was preserved.
Repairing that context requires a stopped checkpoint and a new checked
executor revision, not a live edit or a relaxed final motion gate.

## Independent baseline articulation probes

`scripts/probe_baseline_module_motion.py` executes audited source-module
axis hypotheses in memory. It converts axes through the actual parent
transform, retains every port-level boundary contact and checks all known
moving/fixed native collider pairs. Each sweep starts with nine poses over
-60 to +60 degrees and adds up to two collision-neighborhood refinements.
Source IO, baseline assembly state and presentation/dependency hashes are
verified before and after; no source geometry or joint is saved back.

Reports are in
`runs/delivery-implementation-20261020/baseline-motion-v1/`:

| Source | Audited module | Sampled poses | Poses losing original boundary contacts | Poses with new known overlaps |
| --- | --- | ---: | ---: | ---: |
| R34 | Rear wing | 21 | 0 | 16 |
| R34 | Front bonnet | 21 | 0 | 16 |
| R34 | Positive source-Z door | 20 | 0 | 14 |
| R34 | Negative source-Z door | 20 | 0 | 14 |
| Chiron | Front bonnet | 21 | 0 | 16 |
| Chiron | Positive source-Z door | 15 | 0 | 8 |
| Chiron | Negative source-Z door | 15 | 0 | 8 |

Both zero poses have no known cross-boundary overlap. The broad symmetric
sweep includes inward directions and is not official mechanism travel;
overlaps cannot be interpreted as failure of the official vehicle design.
R34 still has 216 unknown collider instances and one imported-transform
warning; Chiron has 158 unknown collider instances. Read-only native-file
inspection confirmed empty collider files for `32054`, `3713` and `u9053`,
and no direct collider file for `89678` or `4369`; the probe also follows
the catalog's package and alias resolution. Missing data remains unknown.

These are source-scope and diagnostic results, with `fresh_creation=false`.
They establish neither accepted travel, internal module rigidity, linked
mechanisms, physical behavior nor fresh vehicle delivery. Both reports
retain digital assembly, motion and appearance as unverified.

Regression for the motion probe, baseline axes/articulation presentation
and live-run inspector: 33 tests passed in 5.51 seconds. No website or
runtime executor changes were published in this diagnostic increment.

## Recovery after planner evidence and binding fixes

The planner motion summary now preserves installed-wheel mechanism failures
separately from passing carrier poses. Rebinding an identical module at the
current revision no longer increments the graph revision. Stale requests,
role validation, duplicate-module rejection and failed-save rollback remain
covered. The focused requirements, inspection, candidates and scheduler
regression passed 47 tests; production build v30 also completed.

Website recovery created revision
`assembly-recovery-451a6431033c4f8aad08f2a0e922475e`, using the preserved
revision-52 checkpoint. The host rechecked three chassis candidates and one
steering candidate as passing, and retained the chassis as provisionally
assembled. The new planner explicitly identified missing installed wheels
and proceeded to retrieve alternative steering structures. This is not a
complete steering module or vehicle acceptance.

The read-only four-target audit at
`runs/delivery-implementation-20261020/target-training-audit-v35` found no
target-family/module conflicts in the three lifecycle-v3 dataset splits,
but all 1,034 samples carry an outdated tool protocol. The existing adapter
is therefore ineligible for the current independent comparison; no default
planner was changed and no samples were silently relabelled.

## Training source qualification rebuild

Attempted current-executor lifecycle regeneration in the separate
`training/assembly_model/datasets/lifecycle-v4-20261020` directory. The
frozen production snapshot contains 24,131 modules with unverified review
and training eligibility disabled. The regeneration correctly rejected the
old sources and published no training dataset (`source_failures_not_training_ready`).
The failed report and execution logs remain preserved.

Audited sources in the mutable corpus without changing the frozen running
Countach input. Batch v36 checked 100 modules: 34 approved, 66 unverified.
Snapshot `02a6e9fcbd57be512f9d2e8b351bc4f924a2cbb713c80bb7834cc29c4a773de1`
contained 91 eligible modules total, 90 after four-target family/duplicate
exclusions; 88 of those had only three parts, one four and one six.

Added a bounded `--min-parts` option to the audit script to target larger
modules. Batch v37 checked 40 modules in the 12..40-part range: nine approved,
31 unverified. Final frozen snapshot
`3939b2cd746652a2fd2c127228341001f0633fd867280e76fa2b4c1a3355e6fd`
has 100 eligible modules before target exclusion. Reports are under
`runs/delivery-implementation-20261020/source-audit-v36.json` and
`source-audit-v37.json`. Qualification covers source identity, declared
license and digital checks, not semantic role, appearance, physical build
or trained model quality. No adapter training or planner switch occurred.

## Current-protocol demonstration and tokenizer validation

The v38 demonstrations used snapshot
`3939b2cd746652a2fd2c127228341001f0633fd867280e76fa2b4c1a3355e6fd`
and excluded all four delivery target families and duplicate-module groups.
Nine eligible 12-part source modules each completed 27 executed operations.
These are scripted reconstruction demonstrations, not autonomous planner
successes. The dataset contains 51 training and 24 validation samples, zero
test samples and zero rejected samples. Target isolation and current-protocol
audit are preserved in `reviewed-demonstrations-audit-v39`.

Tokenizer validation was rerun successfully on 2026-10-20: all 51 training
and 24 validation samples fit the 1024-token context. Seven training and two
validation families/modules/models are disjoint. The protocol hash is
`71f973dc988967e2f8ca52e62c87c61e31b9e7d4db5780fdc745e1ad66c35714`.
The training partition SHA256 is
`363db3c646d098912d936859fefb3c6a6f063315525de7090d170ffcb193cca0`;
the validation partition SHA256 is
`fdf68a08df6a1ed34dbe8c636792e90cd1d07af1f6be8cb0d2134163fdc6a091`.
Validation only: training_started=false and optimizer_steps=0. Independent
test coverage and successful snap/replacement/graft/repair trajectories
remain required before judging or switching the production planner.

## Larger source batch and isolated candidate training

Batch v40 reviewed 60 real modules in the 16..60-part range: 18 approved,
42 unverified. Frozen snapshot
`2e855e14f5a770d88173f2bf444f83ebab7d079167aa6528be79e42e85cc8dce`
contains 118 eligible modules. The live Countach retains its original snapshot.

The v41 source demonstrations completed 27 module trajectories and deterministic
replay, producing 210 train, 32 validation and 22 test samples without rejection
or duplicate weighting. All samples fit context 1024. Family/module/model/
trajectory/input splits are disjoint: train covers 20 families, validation three,
and test only one. Four-target isolation and protocol audit passed; evidence is
`reviewed-demonstrations-v41/target-isolation.json`. These remain source-placement
demonstrations, not autonomous selection, snapping or repair benchmarks.

BF16 rank-16 LoRA candidate training was started in the isolated environment
with the shared GPU lock and exact v41 configuration. Completion and tool-loop
quality require separate evidence; no production default was changed.

### v41 training completion and held-out execution suite

Training exited successfully with 13 optimizer steps, context 1024 and
127.07 seconds of training runtime. The manifest records baseline validation
loss 0.9191919565 and adapter validation loss 0.0045418921. This is supervised
source-placement loss, not evidence of autonomous assembly improvement.
Status remains `trained_not_accepted`; test metrics are absent and the
production planner is unchanged. Adapter and immutable manifest are under
`runs/delivery-implementation-20261020/reviewed-demonstrations-v41/training`.

The current executor revalidated and froze two held-out source-module tasks
under `reviewed-demonstrations-v41/benchmark/suite.json`, with identical
40-decision/900-second budgets for subsequent independent planners. These
tasks cover only one held-out family and supplied native placements. No
planner execution or winner is claimed from suite creation.

Live Countach recovery was rechecked at revision 54: 23 parts, 27 connections,
one component, no reported collisions or unknown geometry. Its worker remains
alive and is planning steering step 21. Carrier poses pass, but final steering
response still fails because front wheel modules are not installed. Only one
of 17 required modules is complete; this remains an experimental draft.

### v41 executed fine-tuned evaluation: failed, not promoted

Both frozen held-out tasks executed against the v41 adapter through the real
loopback planner and deterministic executor. Each expected 16 native parts;
each produced zero parts, zero successful mutations and three rejected tool
calls, stopping at `repair_budget_exhausted`. Execution pass is false for both.
Evidence is in `reviewed-demonstrations-v41/evaluation/finetuned-task-{0,1}`.
The first task invented unsupported edit operation types rather than reading
the required guide or using the actual operation schema.

A direct audit of the training JSONL found all 210 assistant targets are
`edit`. This narrow source-placement dataset does not cover navigation,
queries, checks, exports or error recovery. Lower validation loss on that
distribution did not translate into valid held-out tool execution. The adapter
is not eligible for production promotion. Future training needs executed,
successful lifecycle and repair trajectories with the same protocol; do not
replace this failure with scripted completion or relax the executor schema.
Base and Codex runs remain outstanding, so this is not a three-way comparison.

The untrained pinned base has now executed both identical held-out tasks.
Both stopped after three planner errors: the service could not decode a complete
JSON assembly decision. Each created zero parts and passed no execution check.
Evidence: `reviewed-demonstrations-v41/evaluation/base-task-{0,1}`. The adapter
returned JSON but invalid operations; neither path demonstrated module success.
Codex evaluation remains outstanding and no winner or default change is made.

The data coverage cause was traced to `assembly_training_data.replay_run`:
its mutation-only sample filter intentionally omits successful context/query/
check/export decisions. Existing lifecycle data tooling must be used with
currently approved sources and current protocol; the older lifecycle-v4 audit
has zero sources and source-approval rejections, so it is not reusable training
evidence. Preserve that rejected audit rather than relabelling it as qualified.

### Lifecycle v42: replay, isolation and context audit

The lifecycle builder now replays 27 approved source demonstrations under the
current protocol. Evidence-path relocation compares equivalent local artifact
paths while preserving hashes and external paths; original failed replays are
retained. These are scripted source demonstrations, not planner successes.
The dataset contains 912 training, 116 validation and 88 test samples, with
context/inspect/remember/edit/check/export/finish actions. Snap, replacement,
graft and successful repair coverage remain outstanding.

`train.py --validate-only` verified all execution evidence and found every
sample fits the pinned tokenizer at context 1024, without truncation or exclusion.
Partition family/module/model/input groups are disjoint. Test coverage remains
only one family and two modules. `lifecycle-v42/isolation/training-isolation.json`
audits all four current targets: zero conflicts, unknown provenance or stale
protocol samples; comparison eligibility is true. This is data eligibility,
not assembly quality acceptance. A new independent LoRA training run has started
in `lifecycle-v42/training`; the default planner is unchanged.

The managed v29 frontend was exercised through an isolated agent-browser session.
Countach details, reference image, module diagnostics and running task controls
rendered; document width 1249 was within viewport width 1264. Screenshot:
`lifecycle-v42/website-detail.png`. The page correctly retains unverified module
roles and failed final steering response rather than certifying carrier-only
checks. This is an existing-job UI check, not fresh website-to-IO acceptance.
The live worker progressed to left-front-wheel candidate construction after
chassis and steering scheduling; no complete vehicle is delivered.

### Lifecycle v42 training completed; independent execution underway

The pinned BF16 rank-16 LoRA run completed 57 optimizer steps and one epoch
over 912 eligible samples at context 1024. Elapsed time was 1665.51 seconds;
training loss was 0.13303. Validation loss over 116 samples changed from
2.36331 for the base to 0.00356738 for the adapter. This is distribution-fit
evidence, not autonomous assembly acceptance. The test partition was not scored
by training (`test_metrics=null`).

Manifest SHA256: `37e4ab6b15b5b86e411a83a68f1a88ddc4f19ec88d1f49f6b8023fef2cb12144`.
Adapter safetensors SHA256:
`73081f2d62bb21189d676d83526901afe936fce77a50b8a482e3ac949d0e5f65`.
The loopback inference service verified the adapter inventory and hashes.
Training finished successfully despite a nonfatal PEFT remote config lookup
timeout during saving; no model-version or dataset substitution was made.

Independent Codex and lifecycle-v42 adapter execution runs use the unchanged
v41 frozen suite and executor. Their new evidence directories are
`lifecycle-v42/evaluation/codex-task-0` and
`lifecycle-v42/evaluation/finetuned-task-0`. Results remain pending; no production
default is changed. These supplied-placement module tasks do not establish
image-to-car ability, appearance or whole-vehicle delivery.

The live Countach left wheel trial reached revision 59 with 26 parts,
31 connections, one component and no reported collisions or unknown geometry.
Motion and candidate comparisons remain required; this static pass is not
wheel-module or whole-vehicle acceptance.

The first v42 adapter execution finished its 40-decision budget with all 16
expected native parts and exact transforms. Digital checking and archive
roundtrip passed, but `planner_finished=false`, so `execution_pass=false`.
The final model action requested `/operations/16` from a 16-element list and
received an IndexError rather than transitioning to check/export/finish.
This demonstrates valid placement execution while exposing a lifecycle
termination defect. Host-generated draft export/check evidence does not turn
the model's unfinished run into a successful trajectory.

The independent Codex task-0 process terminated at planner decision 14 after
the frozen 180-second decision deadline. Its incomplete trace is retained;
there is no successful benchmark-result report. The second task for each
planner is running independently. Do not change the frozen deadline or
overwrite either failed trace when investigating recovery.

### Independent lifecycle results and diagnosed next changes

Both v42 adapter tasks ended as drafts at 40 decisions. Each placed all 16
expected parts with exact transforms; digital and archive roundtrip checks
passed, but neither planner issued check/export/finish. Both requested the
out-of-range `/operations/16` at decision 39. Task 1 took 798.468 seconds.
These are two placement successes and zero autonomous lifecycle successes.

Codex task 1 ended at its time budget after 931.703 seconds and 21 decisions:
zero installed parts, one tool error, and no accepted export. It issued 20
context reads, including repeated identical semantic pages (offset 0 four
times, offsets 400/800/1200 twice each). Pagination itself is valid; restarting
the same pages without retaining their contents consumed the task budget.
Codex task 0 remains an independent planner-timeout failure with its original
trace. Neither planner is promoted to the website default.

`scripts/diagnose_assembly_lifecycle.py` now reads preserved runs and reports
tool counts, exact repeated page requests, out-of-range placement requests,
and original benchmark metrics. It does not modify or resume evaluations.
The executed LifecycleDemonstrator uses seven setup actions, two per placement,
and five closing actions: 44 actions for 16 parts even before pagination.
The frozen suite allows 40. Therefore the demonstrated strategy cannot finish
that suite unchanged. Separately, the observed wrong index transition remains
a model defect; a larger budget alone does not establish its correction.
The next comparison must freeze a new jointly identical contract and either
demonstrate a more efficient valid lifecycle or explicitly budget the full
demonstrated lifecycle. Original v42 failures must remain intact.

Countach revision 61 had a passing static check (26 parts, 31 connections,
one component, no collisions or unknown geometry). A new rolling candidate
failed connectivity in 32 of 37 poses. Left steering response passed nine
samples, while the right wheel remained uninstalled. The worker is continuing
its candidate repair; none of this certifies a complete wheel module or car.

The frontend manager was independently restarted on v29. Windows PowerShell's
Invoke-WebRequest parser raised a NullReferenceException during readiness
checking; adding UseBasicParsing fixed that check. A fresh managed restart
returned HTTP 200 with the expected v29 build. No manual console is required.

### v43 lifecycle diagnostic and live website follow-up

The unchanged adapter failed again with the enlarged 64-decision/1500-second
budget: all 16 placements were exact, but three consecutive out-of-range
operation reads caused repair_budget_exhausted at 42 decisions (915.063 s).
Its input explicitly supplied both placement count and current instance count
as 16 and instructed check/export/finish at count. This rules out insufficient
decision budget as the explanation for this observed termination failure.
The independent Codex run exited at decision 24 after five upstream stream
reconnection failures. Preserve its interrupted trace separately from model
logic failures. Neither run qualifies as autonomous lifecycle success.
See docs/lifecycle-budget-v43.md for exact evidence and experiment limits.

The v29 homepage and Countach detail page loaded in an isolated browser.
The detail page reported unverified delivery and the active wheel task;
document-level horizontal overflow was absent at width 1264. This is status
and layout evidence, not a full website creation/download acceptance.

The Countach worker (PID 36400) remained live and advanced to decision 60.
Trial C had two native-collider collisions at revision 65; its motion check
finished and the worker continued planning. Trial A/B passes remain
provisional, with semantic roles unverified. All four vehicles remain
undelivered; no default planner or main-branch promotion is justified.
