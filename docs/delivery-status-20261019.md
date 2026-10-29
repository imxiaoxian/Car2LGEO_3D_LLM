# Delivery implementation evidence, 2026-10-19

## Scope

The active target order is Countach 5000 Quattrovalvole, Ferrari F40, Nissan
Skyline GT-R (R34), and Bugatti Chiron Pur Sport. No vehicle is delivered.
Source IO archives are reference baselines; they are not newly created output.
Implementation stays in this repository. `D:/lego` is read-only.

The latest user clarification sets each knowledge-base IO's detail level as
the delivery standard. See `delivery-detail-standard.md`. Earlier universal
full-real-car detail requirements and the three exact five-opening Countach
rim repairs are superseded for acceptance scope; raw image findings remain
historical evidence, not corrected images. Native fit and connectivity checks
remain required. Four source hash/inventory audits match the frozen baselines.

## Latest checkpoint, 2026-10-20, before carrier-first recovery

Live verification found no listeners on 8777/8778 and no assembly worker.
The Countach runtime is stopped/cancelled; old module-agent running fields are
stale. Services and website recovery are being restored under the existing
authorization. The cancelled revision remains immutable.

The new scheduler establishes steering and required suspension carriers before
wheel installation. Independent module subtrees can be installed into declared
protected carriers while preserving existing geometry, transforms and bindings.
Final motion checks require actual wheel steering and suspension response;
carrier-only module checks are provisional. Recovery retires incompatible old
wheel subtrees only in a new revision and retains source hashes and evidence.

Carrier regression: 89 passed. Full Python regression: 887 passed, one skipped,
recorded in `carrier-installation-regression-v1.xml` and
`python-regression-carriers-v1.xml` under
`runs/delivery-implementation-20261019-v1/`. Production frontend
`.next-delivery-20261020-v25` passed build, lint and type checks. A subsequent
compact planner-guide clarification is being verified before launch.

These checks do not certify Countach assembly, appearance or delivery. All
four targets remain undelivered; website preview/download and compatibility
checks for a new complete vehicle remain outstanding.

## Historical checkpoint, 23:26 +08:00

This checkpoint supersedes the process and module-progress statements below.
All four vehicles remain undelivered. The user-started v23 frontend and backend
are serving on 8778/8777; no further service switch was requested.

- Browser recovery created independent revision
  `assembly-recovery-35c8c8ed27d648f09ebf0fc4767521f1`. Its recovery record
  preserves source state/schedule hashes and all existing geometry. The prior
  cancelled worker is no longer running.
- The old right-front wheel binding incorrectly referenced the completed
  left-front wheel's actual tyre, rim, hub and module root. The new executor
  rejects duplicate ownership at binding, candidate and completeness checks.
  Recovery quarantined only that unfinished binding, leaving its source
  evidence intact. Its old candidate was rechecked and failed.
- Chassis and left-front wheel were rechecked successfully and carried into
  the new revision. Scheduling remains 2/17 provisionally assembled; the
  right-front wheel starts with an independent module task and explicit
  read-only ownership for both completed modules. This is not whole-vehicle
  acceptance.
- Execution resumed from native revision 23 into revision 24. No reference
  image or Blender generation was resubmitted by this assembly recovery.
  The corpus snapshot and held-out target exclusions remain unchanged.
- Python regression v33: 870 passed, one skipped. Focused ownership/recovery
  regression: 72 passed. The independent v24 production build passed compile,
  type, lint and static generation; v23 continues serving because these
  changes do not require a frontend switch.

Evidence: `module-ownership-v33.xml`, `regression-v33.xml`,
`runtime-recovered-v33.png` and the revision's `recovery.json`, under the
existing run directories. `scripts/audit_assembly_recovery.py` additionally
records original file hashes, completed module signatures and current binding
ownership without changing the running assembly. Complete preview, IO download,
vehicle appearance and Studio checks remain unverified.

The read-only R34/Chiron baseline review in `baseline-axis-review-v32/` also
corrects role interpretation: R34's highlighted rear wing/support is not a rear
cover; Chiron's highlighted roof/supports are not an opening rear cover. The
latter has 13 known boundary axis constraints with incompatible axes. These
records retain articulation `unverified` and motion `not_checked`; they do not
establish an opening-cover requirement. The 18-test axis-review regression is
recorded in `baseline-module-review-v32-fixed.xml`.

## Historical checkpoint, 22:34 +08:00

This checkpoint supersedes the live-state claims in the historical sections
below. Countach, F40, R34 and Chiron Pur Sport remain undelivered. Detail scope
follows each frozen knowledge-base IO, as recorded in `delivery-detail-standard.md`.

- Frontend 8778 now serves the user-started
  `.next-delivery-20261019-v23`, build ID `T9MUI-kXGHWhxaLzomjEt`.
  The vehicle page returns HTTP 200 and its script assets match that build.
  Backend 8777 is available; neither service requires another restart for these
  documentation updates.
- Countach job `9f4244f9ec5d4d23af3dc1ba6384427f` continues in runtime session
  `ad4d824bc1624d539705c231cd416072`. Backend PID 49380 owns launcher PID
  48296 and assembly worker PID 39680. Active assembly artifacts are under
  `revisions/assembly-recovery-2f8418a8dd6a4fc9a74a01e09927362a/artifacts/03-lego/agent`.
  Execution-hashed modules and skill remain frozen during this run.
- The scheduler has 1/17 modules provisionally assembled (chassis) and is
  executing `wheel_front_left`. Formal vehicle completeness remains 0/17;
  the website correctly records `not_accepted`. Workflow progress 68 is a
  stage indicator, not a vehicle-quality percentage.
- Wheel trials A (6594/6595 with hub 6587) and B (32019/32020 with hub 6587)
  have saved passing local connection and rolling checks. Trial C (41897/41896)
  has been constructed at revision 19 but is not yet verified. The latest
  saved structural check is revision 18: 15 parts, 18 connections, one
  component, zero collisions and zero missing native-data records. That
  earlier check does not certify the changed revision or a complete car.
- Context contracts are `assembly-context.v7` and `vehicle-scoped.v2`.
  Corpus snapshot remains
  `ab74fbb830b657d60633c210d5e795a93f4de336d15e2a76b41ce10cff21dd30`.
  Target exclusions and source baseline hashes remain frozen.
- Complete Python regression v27 reports 840 passed and one skipped.
  Read-only runtime diagnostics have a separate four-test pass in
  `runtime-diagnostics-v29.xml`. No new training, planner-default switch,
  whole-car acceptance or merge is claimed.

The v23 browser exposes pause/cancel controls and current module diagnostics.
The previous live pause/resume observation is now recorded in
`runs/delivery-implementation-20261019-v1/runtime-controls-v27.json`, alongside
`runtime-paused-v27.png` and `runtime-resumed-v27.png`. Assembly and trace
hashes stayed stable during the pause observation, and the same worker/session
continued after resume. This verifies cooperative checkpoint controls;
network-level request deduplication was not proven. Complete native preview,
exact IO download, vehicle appearance and Studio compatibility remain unverified.

The current performance diagnosis records slow planner decisions, repeated
context reads and one incorrect corpus-module lookup. Filesystem timing includes
pause and process overhead. `scripts/inspect_assembly_run.py` reads progress,
compressed port evidence and recorded token usage without mutating the worker.

## Historical checkpoint, 20:04 +08:00

This section supersedes the execution timestamps and verification counts below;
the older entries remain historical evidence. All four vehicles are undelivered.

- The user-started frontend on 8778 still serves
  `.next-delivery-20261019-v13`. Production build v19 passed build, lint and
  type checks but is not the serving build.
- The backend was restarted with the project virtual environment and current
  assembly runtime controls. The Countach worker is registered with a process
  identity, session, request hash and selected artifact revision.
- The stopped v3 assembly remains unchanged. Native state was migrated to
  `revisions/countach-context-recovery-20261019-v4/artifacts/03-lego/agent`.
  Candidate measurements and the chassis module were rechecked against the
  current executor. The scheduler has 1/17 modules provisionally assembled
  and is running the front-left wheel task. This is not vehicle acceptance.
- Corpus snapshot remains
  `ab74fbb830b657d60633c210d5e795a93f4de336d15e2a76b41ce10cff21dd30`.
  Vehicle input hashes and held-out exclusions are unchanged.
- Current context contracts are `assembly-context.v5` and `vehicle-scoped.v2`.
  Full skill, protocol, semantic data and observations remain available through
  navigable context resources. The module-ID/shorthand separator regression
  is fixed and covered by the complete Python regression.
- Python regression v20: 789 passed, 1 skipped. JUnit:
  `runs/delivery-implementation-20261019-v1/python-regression-v20.xml`.
- Historical local model adapters are not eligible for the current executor
  comparison. No new training or default-planner selection was performed.

Live browser pause/resume passed on the user-started v13 frontend. While paused,
the graph and trace hashes remained stable; decision 0001 had completed but was
not executed. Browser resume executed that decision and started 0002 in the same
worker/session, without resubmitting the completed request. Evidence:
`runs/delivery-implementation-20261019-v1/runtime-controls-v20.json` and its two
screenshots. This covers checkpoint control only. A complete new vehicle preview,
exact IO download, whole-car appearance and Studio compatibility remain unverified.

## Historical execution notes

The user-started frontend serves port 8778 from
`.next-delivery-20261019-v13`; backend port 8777 owns Countach job
`9f4244f9ec5d4d23af3dc1ba6384427f`. The newer v18 frontend build passes,
but is not the serving build. Job progress and final reference admission must
be read from the current task artifacts, not inferred from a successful build.

The obsolete worker was stopped after its pending request completed. Its red
anchor drift and earlier outputs remain diagnostic history. The original 31
white references were restored, with their exact generation provenance, into
`revisions/reference-recovery-20261019-v1/artifacts/01-image` under the same job.
Recovery verifies every pixel hash and does not inherit stage completion or
reference admission. The white anchor SHA256 remains
`71fb08fb39c7aab2d977642984a8d773476d4922a2866535a873bb1c835dd2e9`.

The restarted backend loads the current review logic. Resume was submitted
through the serving website. The executed reference plan contained 32 shots: the driver
seat camera looks rearwards into its own installed seat, and a separate
passenger-seat camera supplies the opposite side. Only changed/missing camera
inputs invalidate cached pixels. Generation retains the five-request batch
barrier. That 32-shot set subsequently completed graded review and was
admitted with mandatory local repair debt. New Countach reference plans now
contain 30 shots: the baseline detail policy omits hidden pedal hardware and
seat mounts without deleting historical images. Three exact rim-hole findings
are excluded by finding ID and pixel hash; their original failures remain.

As verified at 15:58 +08:00, actual gpt-6-astra staged Blender modelling has
completed and the design mesh passes its geometry checks (zero failed checks,
523,264 triangles with a performance advisory). Appearance remains unaccepted.
This is design geometry, not a native LEGO vehicle or assembly acceptance.

Local Codex assembly is running in revision
`revisions/countach-baseline-20261019-v2/artifacts/03-lego/agent`, with frozen
corpus snapshot `ab74fbb830b657d60633c210d5e795a93f4de336d15e2a76b41ce10cff21dd30`
and a 160-step module budget. As verified at 16:35 +08:00, chassis attempt 1
is provisionally assembled and front-left wheel attempt 1 is running, with
1/17 tasks assembled in the scheduler. The saved revision-8 structural check
has 12 native parts, 14 verified connections, one connected component, no
detected collisions and no unknown geometry/connectivity. That local check
passes; independent role review, final external interfaces and appearance are
still unverified, so the vehicle module acceptance count remains 0/17. The
latest wheel input is decision 0011. Source IOs have not been substituted as
generated assemblies. The live website API now reconciles this revision as
`processing`, stage `lego`, progress 68, with the baseline detail policy,
module tasks and runtime evidence. Progress 68 is a workflow indicator, not
an appearance or delivery score. The active worker was not restarted.

## Reference checker calibration

The original final review incorrectly attributed the rear close-up to
`front_cabin_join` and the front close-up to `rear_cabin_join`. Direct image
inspection and two actual focused vision calls on immutable copies confirmed
the attribution error. Original findings, requests, responses and pixel hashes
are retained in:

`runs/delivery-implementation-20261019-v1/reference-reinspection-v1/`

The focused result was `ready_with_repairs`: three wheel-pattern findings were
then mandatory modelling repairs. The user's later baseline-detail scope
excludes those exact real-car hole-count requirements. This diagnostic does
not overwrite the original review or certify appearance.

Review attachments now have their IDs printed in a separate pixel band;
original reference images are unchanged. Before regenerating a blocked image,
the checker performs one focused reinspection with its anchor. A passing
reinspection must explicitly explain misattribution; missing explanations,
uncertainty and confirmed actual defects cannot become passes. Both findings
are saved. Transferable local defects retain their repair debt and do not
trigger repetitive vision review.

New reviews require typed defects, including an empty array for clean passes.
Missing classifications and malformed JSON receive bounded schema repair,
preserving the original response and findings. Matching batch reviews can be
reused only with identical pixels, anchor, camera/state contracts, identity,
brief, prompts and model; copied evidence is hashed in the final report.
Current website and delivery gates expire when any inspected reference or
review evidence changes. Historical findings remain readable without granting
current admission.

The review parser also recognizes the exact `TARGET_<view>` attachment alias
without changing findings or raw replies. Duplicate, unknown, nested and
approximate aliases still fail. This fixes redundant schema-repair requests
observed in the resumed run; already-running workers retain their loaded code.

The first recovery worker stopped on this alias error after generating the
driver seat, passenger seat, pedal and seat-mount cameras and three bounded
hinge-pose corrections. These outputs and all original findings remain saved.
An independent immutable-pixel diagnosis in `hinge-pose-diagnosis-v1/` found
intermediate longitudinal door-edge travel, but could not establish a precise
3D hinge angle. It is diagnostic evidence, not reference admission.

Open-door and hinge reviews now include the target's actual same-side closed
parent when declared. Parent hashes and comparison instructions are bound to
review caches. Pose inspection distinguishes the long lower door edge from
the short front edge and window pillars. A reinspection may explain a concrete
misinterpretation while retaining the initial finding; unresolved geometry or
ownership still blocks. Review exceptions now mark the batch failed and retain
its generated pixels. Resume was confirmed by both the browser and API after
the review changes; this is tool recovery, not acceptance of the hinge image.

The resumed review then accepted the unchanged hinge pixels using the verified
closed-door context and concrete longitudinal-edge landmarks. All eight camera
batches completed, followed by a final review with copied batch evidence. The
gate at that stage was `ready_with_repairs`: modelling admission, not image
perfection or final appearance approval. The website/API then reported
reconstruction at 45%; the current external revision state is recorded above.

Transport auditing in `reference-transport.jsonl` records request IDs, timing,
view, model, image and prompt hashes, and failure classes. It contains neither
API credentials nor prompt text. Existing active workers may predate it.

## Geometry and assembly diagnostics

The old glazing scene remains unchanged. Same-camera A/B renders under
`glazing-ab-probe-v4/` showed that broad studio softbox reflections caused much
of the white windshield appearance. Increasing transmission depth or replacing
the glass shader alone did not fix it. The recorded inspection rig reduces
world reflection and removes softboxes from glossy rays while preserving
diffuse lighting and material shaders. It is an inspection configuration, not
an optical measurement or a geometry repair. New vehicle geometry still needs
its own checks and appearance review.

`wheel-prototypes-v1/prototypes.json` records three native engineering
prototypes with rim, tyre, axle and bearing: one connected component, no
detected collisions or missing evidence, and nine passing rolling poses each.
They are not model-generated Countach modules or approved training examples.
Vehicle fit, steering/suspension integration, period rim appearance, Studio
compatibility and physical buildability remain unverified.

## Planner and training boundary

The context contract is now `assembly-context.v4`. Production Codex can receive
rich schemas and image pixels; local planners receive compact text resources.
Those inputs intentionally have different contract hashes. The explicit
`--context-profile shared-text` comparison mode supplies identical compact
payloads and no Codex-only pixel enrichment. Scheduling hashes bind this
profile as well as implementation and resource hashes. No default planner was
changed and no new training was launched.

Historical adapters and benchmarks remain historical. They are not evidence
for the changed contract. Formal retraining requires newly replayed eligible
trajectories and current held-out family/module audits.

## Verification and remaining gates

- Full Python regression including snapshot recovery, attachment aliases,
  closed-parent pose comparison and failed-review dispatch: 703 passed,
  1 skipped. JUnit:
  `runs/delivery-implementation-20261019-v1/python-regression-v11.xml`.
- Focused reference/evidence/delivery regression: 81 passed. Snapshot recovery:
  3 passed, covering pixel preservation, stale-admission removal, unrelated
  cache rejection and changed-source rejection.
- Production frontend v18: build, lint and type checks passed; serving build
  remains v13.
- Live browser: Countach detail page, reference controls and task resume work
  against serving v13 and the restarted backend. That earlier browser check
  verified reference recovery/resume, not the current external assembly run.
- Whole-vehicle preview, IO download and download/check hash equivalence:
  not yet verified for a new Countach output.
- Countach G1 reference admission passed with explicit repair debt. G2 key
  modules, G3 full vehicle, G4 website/appearance and the other three
  new vehicle runs remain outstanding. No merge or delivery claim is made.

## Baseline detail calibration verification

Python regression v12: 716 passed, 1 skipped. Subsequent evidence-reader and
scope integration: 121 focused tests passed, recorded in
`baseline-detail-focused-v1.xml`. Production frontend
v17 builds, lints and type-checks successfully; serving frontend remains v13.
The current worker was not restarted or reissued during this scope correction.
R34 and Chiron cover articulation remains a baseline-review item, not an assumed
real-car mechanism or an approved omission. All four vehicles remain undelivered.

Subsequent baseline/reference/module regression: 82 passed, recorded in
`baseline-reference-scope-v2.xml`. It covers Chinese and combined identities,
model-only resolution, wrong-brand exclusions, per-baseline mechanism and
camera requirements, exact finding exclusions and unchanged-camera cache
reuse. No active assembly was restarted or duplicated for these scope changes.

Latest focused scope, module, reference and vehicle-context regression: 92
passed with zero failures, errors or skips in 14.12 seconds, recorded in
`baseline-scope-regression-v4.xml`. This confirms the baseline detail policy
propagates through the software; it does not certify vehicle output.
