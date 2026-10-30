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
