# Cabin integration diagnostic (2026-10-30)

This probe installs one newly selected cabin on the successful autonomous
linked chassis from [autonomous-linkage-20261030.md](autonomous-linkage-20261030.md).
It is the first cabin-to-chassis integration trial, not a whole-car trial
or a new empty-scene chassis trial. Doors and bodywork are absent.

```powershell
.venv\Scripts\python.exe -m scripts.probe_cabin_integration `
  --source runs/car-modules-20261030/autonomous-linkage-live-v2 `
  --output runs/car-modules-20261030/cabin-integration-live-v1 `
  --timeout 1800 --max-steps 100
```

Use a new output directory. `D:/lego/Studio 2.0/ldraw` stays read-only;
the source IO is copied into the new assembly's sources. The source
report must record a passed autonomous linked chassis and the matching
archive hash. The archive graph must agree with the source checkpoint.
Recorded input file hashes are verified after the run.

The seed contains 20 native parts, 28 total instances and seven joints,
with no vehicle module bindings. Every existing instance, transform,
color and joint is protected. The imported source has its original
wheelbase of 120 LDU and track of 180 LDU. This is a compact mechanism
integration scale, not a claim of 1:12 Countach proportions.

The planner must select every new part type by description search and
successful native query before placement, even if that type exists in
the seed. All new geometry must belong to independent fixed cabin roots;
new joints and edits to the seed are rejected. Two actual native seat
parts straddle the centreline. A native steering wheel aligns with one
seat, and it and the dashboard lie ahead of both seats. The dashboard's
native slope/tile/panel surfaces span at least 40 LDU across the chassis.
Role surfaces remain between the axle planes, inside the wheel-centre
track and above ground. These are bounded placement checks, not semantic
or appearance acceptance. Native collision checks still apply to all
parts, including unlabelled supports and adapters. The task permits at
most 40 new parts.

`planner_cabin_integration_pass` also requires current native static and
ordinary motion evidence, a passing single-candidate cabin module check,
the original linkage driver/passive/rolling set, nine distinct steering
poses and all 324 individually rolling combinations. Every pose retains
native connectivity and collision clearance with the interior present.
Original native contacts remain in the new linkage profile, and every
new cabin part remains fixed throughout the solved poses. Current preview
pixels must reach the live planner before its explicit finish, and the
exported IO must roundtrip to the same graph. Execution, probe and input
hashes must remain unchanged, with no unrecovered planner errors.

The original wheel bindings are absent. Full vehicle reports retain
their actual absent-role statuses; the local gate can accept only the
missing-wheel requirement exclusions used by the existing door probe.
Native failures and unknown evidence are never excluded. Ordinary motion
coverage must still cover every seed joint at nine values across its
original limits; linked motion is checked separately.

Three structural candidates, independent role review, steering-wheel
mechanical linkage, vehicle-specific detail/proportions, appearance,
Studio GUI compatibility, physical retention/load, continuous motion,
simultaneous multi-wheel rolling and whole-vehicle completion remain
unverified. The diagnostic always records `vehicle_completion_ready=false`.

The initial 21 synthetic validator tests passed in 2.88 seconds. They
exercise preserved geometry/limits, original contacts and sampled motion
coverage, independent fixed cabin ownership, role descriptions and
placement, freshness and seeded-type selection boundaries. Their mocked
native reports do not establish successful physical or digital assembly.
The live run's own `report.json` is the authority for its measured result.

## Initial live result

`cabin-integration-live-v1` stopped at its 1,800-second agent budget after
39 decisions; final reporting took **1,802.969 seconds** in total.
`planner_cabin_integration_pass=false`. Its revision 17 preserves all
20 original native parts and seven joints and adds six cabin parts:
`2476b.dat`, `3032.dat`, two `4079.dat` seats, `3037.dat` dashboard and
`30663.dat` steering wheel. All final new types have prior description
search and native-query evidence. The initial `3829c01.dat` steering
stand was rejected by the role-description gate and replaced by the
previously searched native steering wheel. The measured dashboard spans
80 LDU across the chassis; the bounded role-placement gates pass.

Current native static gates pass with one 26-part connected component,
zero collisions and no missing native evidence. The cabin's
single-candidate mechanical module measurement passes. All nine linked
steering poses and 324 individual-wheel rolling combinations pass, retain
the original contact set and keep the cabin fixed. These results do not
override the failed ordinary-motion requirement: the steering tie bar
`32316.dat` intersects the new floor `3032.dat` at three independent
driver samples (26.25, 28.125 and 30 degrees) and all nine independent
tie-bar samples (-60 through -30 degrees). The protected seed's limits
were not reduced. The initial linked profile contains 46 contacts with
the cabin installed, including all 20 original contacts.

Two context reads used out-of-range pose indices at steps 32 and 33.
The successful read at step 34 recovered both tool errors. The agent
subsequently identified the floor collision and searched and queried a
native 2 x 2 riser brick, but exhausted its budget before installing it.
No current preview, planner pixel feedback, export, archive roundtrip or
successful finish was produced. Execution, probe and seed hashes remain
unchanged. No whole-car or appearance credit is assigned.

| Preserved initial evidence | SHA256 |
| --- | --- |
| `report.json` | `9c6e35199b138caa9cb15233bca0edeb09619bd1e5143aab4c45064fdea7eb8e` |
| `assembly/agent-decisions.jsonl` | `47689ff86484870015a11f7e0319bca80068fe1523e6a357ad6e97173f7b1536` |
| `assembly/state.json` | `53c04d2fe653c371f34571a9eb6e57f3ffe832028fa9b00e1660397b11109784` |
| `assembly/motion.json` | `e5bf0bded274e293f82f25aaa901c22f30b56394b2b5b18843d05ce6c2bff6b4` |
| `assembly/linkage-motion.json` | `8d6d4477b4edaa10745a1cf97a99eb7b8274fad1229f50398a9a1eef98eae699` |

The complete local Python regression passed **1,349 tests**, with 14
dependency deprecation warnings, in 869.60 seconds. The focused regression
passed 56 tests in 275.60 seconds. Frontend production build and historical
verification of 106 file hashes and LDraw/BOM totals also passed. Logs are
`runs/cabin-integration-full-regression-20261030.log`,
`runs/cabin-integration-regression-20261030.log` and
`runs/cabin-integration-frontend-20261030.log`. These software checks do not
change the failed live integration result.

## Preserved-trace continuation

`cabin-integration-live-v1-resume-v1` continued the initial checkpoint in
a separate directory with the same mission, execution/probe hashes and
protected seed. No offline graph repair was imported. Its cumulative
budget was 3,000 seconds and 100 decisions. It stopped at
`time_budget_exhausted` after **1,210.610 additional seconds**, with
**3,000.641 cumulative agent seconds**, 60 decisions and revision 20.
`planner_cabin_integration_pass=false` remains the measured result.

The planner raised the floor and cabin surfaces 24 LDU using a native
`3003.dat` riser. At revision 18, all 63 ordinary single-joint samples
and 162 ordinary paired samples pass their native pose checks. The full
host motion status remains `fail` because the vehicle wheel-role
requirements are absent. The separate linkage report passes nine
steering poses and 324 individual-wheel rolling combinations, with 50
native contacts including all 20 original contacts. These are intermediate
measurements, not final-revision acceptance.

The riser's first placement lacked a qualifying prior description
selection. Later searches and reinstalling the same type did not erase
that first-placement evidence. The planner eventually replaced it with
two description-selected and successfully queried `2877.dat` grille
bricks. The final revision has 28 native parts, including eight new cabin
parts. Its selection evidence, bounded role placement, single-candidate
mechanical measurement and current native static gates pass: one
connected component, zero collisions, no unknown geometry and no invalid
transforms. All protected seed instances and joints are preserved.

The ordinary and linkage reports still refer to revision 18. The final
acceptance issues are `motion_missing_or_stale` and
`linkage_missing_or_stale`; their intermediate passes cannot certify the
new support geometry at revision 20. There is no current preview, planner
pixel feedback, IO export/roundtrip or explicit successful finish. The
original source files remain unchanged, their complete decision bytes
are retained as a prefix, execution/probe/input hashes were unchanged
through termination, and no planner tool errors remain unrecovered.

| Preserved continuation evidence | SHA256 |
| --- | --- |
| `report.json` | `9f5b669bdb6767acccaadc340e64e8d78a4fcc6d69b02d1d778f8c4cbba31efc` |
| `assembly/agent-decisions.jsonl` | `009ca707be064266c10f9d5593bb2a970cef5c77b295f8c2d7f26b3f0139191f` |
| `assembly/state.json` | `b383663fed8918f19fa5079a0b5a51f9398ad45d111d6c9f51c48357d2d8990a` |
| `assembly/checks.json` | `e4bda6203b884f288e03de309122199f74c9c6a6bc24ff909feff0b8431044a8` |
| `assembly/motion.json` | `0963defd5d3992686732e12898c30f3efec86faf3955966e8b5181b136a399ae` |
| `assembly/linkage-motion.json` | `6d5e8dcd489380bf1cdf399b82e169a8fdc34b70141ea8a42be604bd6ec7bcb6` |

## Subsequent diagnostic feedback

After the continuation terminated, context v24 added bounded ordinary
motion failure details. Individual and paired sweeps retain real pose
indices, values/positions, native collision pairs, connectivity,
component counts, unknown/invalid evidence and exact observation JSON
pointers. At most 12 failures per sweep and 12 collision/invalid entries
per failure are included, with omission counts; the existing failed-angle
list and full report stay intact. The module-task summary also retains
bounded missing-selection records and their original search, query and
first-placement indices. It does not turn a late query into prior evidence.

Three new regression tests cover interleaved pose indices and report
paths, context reads, freshness, source immutability and bounded omission
counts. The focused agent/context/cabin regression passed **117 tests**
in 9.84 seconds. Frontend production build and the 106 historical file
hashes and LDraw/BOM totals passed. Logs are
`runs/motion-feedback-regression-20261030.log` and
`runs/motion-feedback-frontend-20261030.log`. Applying the new summary
to the preserved initial motion report also identified the actual
tie-bar/floor collision pairs. The earlier 1,349-test full local suite
predates this feedback change. The new execution fingerprint has not
been used for a new live cabin run; historical traces retain their
original contracts and acceptance labels.
