# Closed-contact steering evidence (2026-10-30)

A deterministic 33-part native chassis fixture now passes closed-contact
steering and individual wheel-rolling sweeps. This establishes a bounded tool
capability. It does not extend the earlier live single-wheel planner result to
autonomous chassis creation, whole-car appearance or physical acceptance.

## Tool contract

`assembly-tools.v12` adds read-only `linkage_motion(driver, passive, ...)`.
One declared revolute driver controls one to eight distinct passive revolute
joints. Finite nondegenerate limits must include neutral. SciPy least squares
solves the passive angles while retaining every original native contact pair,
observed insertion offset, port direction and keyed orientation. Native
serialization roundoff is preserved in a part-local frame; the residual
tolerance remains 1e-5 LDU, with a 20 LDU direction-residual scale.

Each sampled pose requires solver convergence, full passive constraint rank,
native collision/connectivity checks and every original contact. The default
budget counts all residual calls, including finite-difference evaluations.
Optional one to four rolling joints are individually swept at each solved
driver pose, with at most 2000 combined poses. Other rolling joints stay
neutral. Missing evidence, free passive joints and exhausted budgets cannot
pass. Cancellation propagates to the caller.

`linkage-motion.json` remains separate from `motion.json`. It neither replaces
ordinary motion nor weakens module/vehicle completion. Reports bind a single
canonical snapshot of native inputs and execution sources before evaluation;
changed dependencies invalidate a pass. Context progress exposes the two
motion results independently. Solver sources are part of the execution hash.

## Native fixture

`scripts/probe_linkage.py` constructs the fixture with the actual assembly
tools, exports before motion evaluation, checks the IO graph roundtrip and
measures native tyre vertices and wheel-axis response. Chassis mode also
requires a four-wheel neutral layout. Preview is required when requested.
The output directory must be new; earlier evidence is never overwritten.

- Four-bar ground/tie beams: `32316.dat`; arms: `6632.dat`; vertical kingpins:
  `3705.dat`; pins: `32002.dat`.
- Four wheel groups: `6536.dat` perpendicular bearings, `3706.dat` shafts,
  `56145.dat` rims and `61481.dat` tyres.
- Two `32525.dat` longitudinal frame rails join the front/rear crossbeams via
  native vertical axle contacts. The frame is a real `frame.ldr` submodel.
- Left driver covers -30 to +30 degrees. Right and tie passive limits are
  -45 to +45 degrees. This is parallel steering, not an Ackermann result.

Reproduce with the configured native library and a new directory:

```powershell
.venv/Scripts/python.exe -m scripts.probe_linkage --output runs/linkage-chassis-new --chassis --preview
```

## Recorded result

Local artifacts: `runs/car-modules-20261030/linkage-chassis-native-v4/`.
They remain excluded from Git.

- `probe_pass=true`: 33 native parts, 38 original native contact pairs retained.
- Nine solved steering poses and 324 steering-plus-individual-rolling poses
  (four wheels, 81 each), all passing native checks.
- 156 residual evaluations; maximum residual 1.4641557480415092e-11 LDU.
- Left/right wheel axes respond by 30 degrees at the extremes. Front tyre
  steering displacement reaches 63.2471/63.3240 LDU; each tyre's isolated
  rolling displacement reaches 108.0046 LDU.
- Rear wheels stay unchanged during steering; the fixed frame is unchanged.
- Neutral layout passes: 200 LDU wheelbase, 220 LDU track, shared ground
  Y=84.00088091 LDU, chassis bottom Y=40 LDU (LDraw Y points down).
- IO graph roundtrip, linkage evidence freshness and unchanged execution/probe
  sources pass. The six-view contact sheet was visually inspected: geometry
  is visible and fully framed. Tyres render blue; color and appearance remain
  unverified. There is no car body.

| Artifact | SHA256 |
| --- | --- |
| Execution sources | `c90f005ab4dff6d84167afcebe01d56942d6b49fa003a641f61b34980c743ec6` |
| Probe source | `5825c8328b1d4edc16ddbc4f5435af2246a3e837c2901e88d6ca10ba363ad81b` |
| `probe-summary.json` | `e08932633e6c0b35d34934724e56ac2033b6393c6d22801cbfa9e7ef1707c5ea` |
| `linkage-motion.json` | `5d01f9def4a95be2849c112b1579eb03bc566e57f16963bbb784654dfad55038` |
| `model.io` | `e1ec2c5160eb0c646242338026092481973f7a8f6224d02f9a0b9a5c9c284d6b` |
| Six-view contact sheet | `267beaf44ba316cb0dc54daa2de2835aef7df11ee471fa63e198d330a098cc33` |

## Preserved failures

Earlier trials retain their original labels and artifacts:

- `linkage-native-v1`: eight-part native four-bar passed.
- `linkage-wheels-native-v1`: unverified because idealized residuals incorrectly
  demanded zero native lateral roundoff. Repaired by preserving measured
  offsets, without relaxing tolerance.
- `linkage-wheels-native-v2`: sampled poses passed, overall unverified because
  evidence ordering was not canonical. Repaired with one sorted/deduplicated
  snapshot.
- `linkage-chassis-native-v1`: actual steering shaft/frame rail collisions,
  plus an invalid part-root chassis binding. Repaired native rail placement
  and connections and bound the frame submodel.
- `linkage-chassis-native-v2`: 33-part pass before adding the final tyre/axis
  response and IO roundtrip requirements in probe v2.
- `linkage-chassis-native-v3`: full probe v2 pass under the earlier execution
  hash. The vehicle-context workflow repair changed execution sources, so v4
  reran the complete fixture and checks under the current source hash. Both
  versions remain preserved; v3 does not certify the changed sources.

## Verification and next step

Targeted linkage, context and agent tests passed: 75 tests in 8.59 seconds.
They cover analytical closure, contact loss despite connected graphs, limits,
rank deficiency, budgets, cancellation, native roundoff, combined collisions,
strict schemas, source freshness and independent motion lifecycles. The native
eight-part integration also confirms no assembly-state mutation.

The first complete Python regression recorded 1,273 passes and one failure:
inserting linkage guidance shifted the clone workflow index, leaving the full
clone entry duplicated in vehicle context and exceeding its existing 12,000
character bound. Both compact entries now match their guide resources. The
original size limit remains unchanged, and a regression assertion checks both
linkage and clone resource pointers. The failing run is preserved at
`runs/linkage-regression-20261030.log`; the complete rerun uses
`runs/linkage-regression-20261030-v2.log`.

The complete rerun passed: 1,274 tests, 14 warnings, 580.46 seconds. No failing
assertions remain. The 75-test targeted run, current-source native chassis v4
probe and complete regression are separate checks; none is an autonomous
linked-chassis planner trial.

The frontend production build passed in `.next-linkage-20261030`. Existing
frontend/API services remain on their previous build. All 106 recorded example
hashes and LDraw/BOM totals verified. The frontend log is
`runs/linkage-frontend-build-20261030.log`.

The next mechanical milestone is a live planner linked-steering/chassis trial
using these tools. Continuous intermediate poses, simultaneous multi-wheel
rolling, force transmission, axial retention, load capacity, physical assembly
order, Studio GUI acceptance, whole-car appearance and arbitrary-object
generation remain unverified. A fixture pass cannot be used as a training label
for successful autonomous planning.

The empty-scene live-planner entrypoint and its eight native validator
regressions are documented in
[autonomous-linkage-20261030.md](autonomous-linkage-20261030.md).
That validator's fixture tests remain separate from the measured planner
result and never change the whole-car completion gate.

## Context-v23 fixture rerun

`runs/car-modules-20261030/linkage-chassis-native-v5/` repeats the complete
deterministic chassis probe after the mechanism-context repair. It exits 0
with `probe_pass=true`, 33 native parts, 38 original contact pairs and zero
neutral collisions. All nine steering poses and all 324 individual-wheel
combined poses pass. Both front wheel axes change by 30 degrees; rear wheels
remain stationary during steering. The current linkage evidence, unchanged
execution/probe sources and IO archive graph roundtrip all pass.

The actual new six-view contact sheet was opened and inspected. The frame,
four tyres, rims and axles are visible and fully framed. The tyres are blue;
color fidelity and vehicle appearance remain unverified. There is no body.
This deterministic fixture is not the autonomous live-v2 result, does not
replace ordinary motion and does not establish physical acceptance.

| Preserved v5 evidence | SHA256 |
| --- | --- |
| Execution sources | `40a60c4b397fe5ece0cec76f671430dad1d3f61e950ab2ae09d4287810226090` |
| `probe-summary.json` | `9487ae9b4e500b7449c755dd6601aadac4e9683855d653427c7bcd9ea1812064` |
| `linkage-motion.json` | `f74a2b2e182f0da225e147091b342ba19b2579067b6bf75a2214295138b90234` |
| `checks.json` | `fe2700eedcd292dd98e2538d16491926c9e838e6f4142827dd6f2595316622cc` |
| `model.io` | `647c9e4d550bc979924e30d914fcd3b6ba22da9fb69d58d6e3a8c4fab63dba88` |
| Six-view contact sheet | `267beaf44ba316cb0dc54daa2de2835aef7df11ee471fa63e198d330a098cc33` |
