# Autonomous linked-chassis experiment (2026-10-30)

This experiment follows the deterministic native linkage fixture documented in
[closed-linkage-20261030.md](closed-linkage-20261030.md). It measures whether the
live local Codex planner can select and assemble a four-wheel linked-steering
chassis from an empty scene. The fixture graph, part numbers, placements and
operation sequence are not supplied to this planner.

## Input and reproduction

```powershell
.venv\Scripts\python.exe -m scripts.probe_autonomous_linkage `
  --output runs/car-modules-20261030/autonomous-linkage-live-v1 `
  --timeout 2400 --max-steps 140
```

Use a new output directory for each attempt. The probe rejects an existing
directory to preserve prior failures. It uses the configured Codex model and
the native catalog at `D:/lego/Studio 2.0/ldraw`; the native provider remains
read-only. No module corpus or deterministic construction plan is injected.

The mission requests 20--64 unscaled native parts, a fixed frame, two front
steering carriers, a physical closed-contact linkage and four independent
rolling submodels. Native English-description search and successful part
query must precede each part type's first successful placement.

## Diagnostic acceptance

`planner_linkage_diagnostic_pass` requires all of the following:

- A current passing static native check and current passing linkage report.
  Ordinary `motion(samples=9)` must also run and retain its actual status.
- Four disjoint rim/tyre/axle submodels, each contacting one distinct native
  round bearing outside the rolling subtree. Rolling axes follow the actual
  transverse bearings, and all three moving parts show geometric rotation.
- Both front wheels descend from distinct steering carriers with vertical
  kingpin axes. The driver is one of these carriers, spans at least -30 to
  +30 degrees and moves both actual front wheel axes by at least 20 degrees.
- Every nonrolling joint belongs to the linkage solve. All nine driver poses
  and 81 individually rolling combinations per wheel pass, totalling 324
  combinations. The existing host still requires native contact closure,
  full passive constraint rank, collision clearance and connectivity.
- The native wheel layout passes with a fixed frame above the common ground
  plane. Fixed parts and rear wheels remain stationary during steering.
- The planner sees the current preview pixels, exports the current revision
  and explicitly finishes. The IO archive roundtrips to the same graph.
- Execution and probe sources remain unchanged, with no unrecovered planner
  errors. The report records the trajectory, source identities and native
  dependency hashes.

This flag is a scoped planner diagnostic. It never changes
`vehicle_completion_ready=False`, and a failed ordinary motion report remains
failed. Appearance, Studio GUI compatibility, physical retention, force
transmission and load capacity remain unverified. The existing whole-car gate
still applies to a complete vehicle.

## Regression boundaries

`tests/test_autonomous_linkage_probe.py` builds a deterministic native fixture
only to test the validator. It cannot establish autonomous selection. Its
positive selection path is explicitly monkeypatched, and its failed ordinary
motion report is a synthetic test input that isolates the diagnostic boundary.
Neither is a measured live planner result or whole-car pass.

The regressions reject empty scenes, unobserved selection, incomplete driver
or combined poses, omitted passive joints, stale evidence, absent actual
geometry response and a horizontal steering-carrier axis. Native-dependent
tests skip when the catalog is not installed.

## Verification

The complete local Python regression passed: **1,282 tests**, 14 warnings,
764.41 seconds. This includes all eight new native-dependent validator cases.
The log is `runs/autonomous-linkage-regression-20261030.log`.

The frontend production build passed in `.next-autonomous-linkage-20261030`;
its log is `runs/autonomous-linkage-frontend-build-20261030.log`. Existing
frontend/API services were not switched to this build. All 106 recorded
example file hashes and the LDraw/BOM part totals also verified.

The initial seven-case targeted run exposed a validator error: a fixed frame
submodel may legitimately contain nested rear rolling submodels. Requiring
that the frame root contain no joint descendants incorrectly rejected the
existing native fixture. The rule now requires actual nonmoving frame parts,
keeps the native layout check and checks those parts remain fixed throughout
the linkage poses. The next seven-case run passed in 225.71 seconds; the full
regression additionally covers the vertical-kingpin rule.

## Measured live trial v1

The empty-scene run in `autonomous-linkage-live-v1` stopped after
2,400.656 seconds with `time_budget_exhausted`. It executed 66 tool actions,
selected 27 native parts with the required search/query ordering and reported
no planner execution errors. `planner_linkage_diagnostic_pass` is **false**.
Execution sources remained unchanged throughout this run.

At revision 12, eight nonneutral linkage poses lost contact while the passive
angles remained zero (`driver_coupling_not_established`). The planner then
rebuilt the tie bar under a moving carrier and articulated it again. Both
subsequent linkage checks at revision 14 retained all original contacts and
solved the passive angles, but four of nine steering poses still collided.
The negative-limit pose collided between a front tyre and a fixed frame
member. Thus the initial coupling issue is not the final failure diagnosis.

The final ordinary motion report is `fail` at revision 11 and is stale for
revision 14. The final static report is also stale. No combined rolling
scan, preview pixels, current IO export or archive roundtrip completed.
No autonomous, vehicle, appearance or physical success is claimed.

| Preserved v1 evidence | SHA256 |
| --- | --- |
| Execution sources | `c90f005ab4dff6d84167afcebe01d56942d6b49fa003a641f61b34980c743ec6` |
| Planner input | `122e64a8ccc257586a4c9d8553dac07b51e7d6476607fc436c038a9d430ff5e9` |
| `report.json` | `00953db9349760cba1786bf8d104a715944b1d0a12dddb23399af93666702947` |
| Final `assembly/linkage-motion.json` | `9dd2ae1731b842a4450ad52465b1af312df71232d9cbdd5eb80cac80f23bb567` |
| `assembly/agent-decisions.jsonl` | `35b41a666c6e8617df201345c1bb1fd03a7fa6a668c73ed2bc305cd4d3851b91` |

## Context repair and independent rerun

Context version `assembly-context.v23` retains the full current joint table
even when instances are truncated. Compact persistent evidence now includes
host module-task failures, linkage contact losses, failed poses and collision
pairs, with pointers to full observations and explicit omission counts.
Revision matches do not certify native-dependency freshness. Ordinary motion,
closed-contact linkage and whole-car acceptance remain separate.

Eleven new mechanism-context regressions failed before this repair and passed
afterward. The focused agent, inspection, vehicle-context and codec suite
passed 79 tests in 4.86 seconds, retaining the existing 12,000-character
vehicle-context limit. This software regression is not a live assembly pass.

The complete regression for these repaired sources passed **1,293 tests**,
with 14 warnings in 631.29 seconds. The preserved log is
`runs/mechanism-context-regression-20261030.log`. The frontend production
build also passed in `.next-mechanism-context-20261030`, with its log at
`runs/mechanism-context-frontend-build-20261030.log`; existing services were
not switched. All 106 historical file hashes and LDraw/BOM totals verified.

Implementation commit `2a755f9e5532dbfae47c86b263d55a346e96e1fa` also
passed both GitHub CI runs: PR run `41001625305` and branch run
`41001620695`. Their Python regression, historical-example verification and
frontend build jobs completed successfully. Native-library-dependent cases
still require the separate local run documented above.

The independent empty-scene rerun uses `autonomous-linkage-live-v2`, the same
2,400-second/140-step budget and no injected fixture plan. Its execution hash
is `40a60c4b397fe5ece0cec76f671430dad1d3f61e950ab2ae09d4287810226090`;
its input hash is `27281d1f848f0d1994f861d18df52200c7fa7b7c5ceb89853048576125eec76d`.
Execution sources stayed frozen until the run ended. The measured result is
recorded below; v1 and deterministic-fixture labels are preserved.

## Measured live trial v2

The independent empty-scene run completed in **2,068.890 seconds**, within the
same 2,400-second/140-step budget. It executed 59 planner actions and explicitly
finished at revision 13. `planner_linkage_diagnostic_pass=true`; the report has
no planner execution errors, no unrecovered errors and unchanged execution and
probe sources. This is one measured successful trial, not a reliability estimate.

The planner selected six native part types through description searches and
queries before first placement, then assembled 20 parts and seven revolute
joints. It chose an open-centre frame, steering arms and a tie beam, rather than
receiving the deterministic fixture's graph, placements or operation sequence.
The observations establish selection ordering, not unseen-part generalization.

- Final static native check: pass, zero neutral collisions, 20 original native
  contact pairs. All nine solved steering poses and all 324 individually rolling
  combinations pass, retaining every original contact and insertion offset.
- The solve uses 249 residual evaluations. The actual front wheel axes turn
  approximately 30 degrees; rear wheels and the fixed frame stay still during
  steering. All four rims, tyres and axles show geometric rolling response.
- Neutral layout passes with a 120 LDU wheelbase, 180 LDU track and common ground
  Y=9.912 LDU. The fixed frame bottom is Y=4 LDU, above ground in LDraw coordinates.
- Ordinary `motion(samples=9)` independently passes at the same revision 13.
  It remains a separate report from the closed-contact linkage scan.
- The current six-view preview pixels reached planner steps 57 and 58. The
  planner described the visible chassis and tie beam, exported revision 13 and
  explicitly finished. The resulting IO archive roundtrips to the same graph.

The intermediate failures remain in the original trajectory. Revision 11's
linkage failed; revision 12 repaired it and passed linkage, but ordinary motion
still collided when the passive front carrier moved independently to 45 degrees.
The planner changed that carrier's limits from [-45,45] to [-30,30], then reran
the static, linkage and ordinary-motion checks at revision 13. The required
driver span of -30 to +30 degrees was retained; no 45-degree pass is claimed.

The actual final contact sheet was also opened and inspected outside the
planner: four tyres, rims, frame and tie beam are visible and fully framed.
The BOM encodes black tyres (0) and light-bluish-gray structures/rims (71), but
the preview shows blue tyres and pale structures. Color fidelity and appearance
remain unverified; there is no body. `vehicle_completion_ready=false`, and
Studio GUI, axial retention, force transmission, load capacity, continuous
motion, simultaneous multi-wheel rolling and physical acceptance remain
unverified. This diagnostic is not whole-car completion or an accepted whole-car
training example.

| Preserved v2 evidence | SHA256 |
| --- | --- |
| `report.json` | `70dd74b2ac0060439ae2d03469eac862b7cf3adef6f9f1f326034d4fb64a972e` |
| `assembly/checks.json` | `a883745245765c9081dbaa9fc26ee72cc9574d952b15bdf8fbd7ba1961a9dbdc` |
| `assembly/motion.json` | `8a654dc70bebee4bd75411b12a1de0337617f165b92ca127b97c4afc74b86d86` |
| `assembly/linkage-motion.json` | `8e5938ede8535e1b2bcb4acdfbecd26cc5607295d27fadb90bb5b56a7d1a3dbd` |
| `assembly/model.io` | `119943ad43187b0c47d1ef89dc651660e35d690cdb951e3e03d0d1b8c00dedbd` |
| `assembly/agent-decisions.jsonl` | `38734263bb5d2b048626898d798a383115d0b153cbf96b7a4d10a8a7c54892c8` |
| `assembly/trajectory.jsonl` | `e0b5be62eeb3cf3f8c6f36fdafde3b248f51ea0198b43ad3b8276406edbffd3c` |
| Six-view contact sheet | `ede0135bbbfda08a2efd5251a39da4b6f2bd9277894d8e355b9bf731dc876169` |
