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

The independent empty-scene rerun uses `autonomous-linkage-live-v2`, the same
2,400-second/140-step budget and no injected fixture plan. Its execution hash
is `40a60c4b397fe5ece0cec76f671430dad1d3f61e950ab2ae09d4287810226090`;
its input hash is `27281d1f848f0d1994f861d18df52200c7fa7b7c5ceb89853048576125eec76d`.
Execution sources are frozen until the run ends. Its measured result will be
recorded separately; v1 and deterministic-fixture labels are preserved.
