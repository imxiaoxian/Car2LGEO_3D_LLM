# Countach scissor-door diagnostic (2026-10-30)

This diagnostic follows the autonomous linked chassis experiment in
[autonomous-linkage-20261030.md](autonomous-linkage-20261030.md). It measures
one left-door mechanism on a supplied fixed native bearing. It does not
install the door in a cabin or establish whole-vehicle acceptance.

## Input and reproduction

```powershell
.venv\Scripts\python.exe -m scripts.probe_countach_door `
  --output runs/car-modules-20261030/countach-door-live-v4 `
  --timeout 1800 --max-steps 100
```

Use a new output directory for each attempt. Existing directories are
rejected so failed evidence remains intact. The default native library is
`D:/lego/Studio 2.0/ldraw`; it is read-only. The input request and Blender
semantic scene are read from
`runs/four-vehicle-acceptance-20261018/countach-v4/shared/inputs`.

The planner receives the request, 30 semantic door nodes, the vehicle
contract and one protected `3700.dat` bearing in a fixed submodel. No moving
part identifiers, placements or assembly sequence are supplied. The request
requires white bodywork and glass moving with the scissor door. About 1:12
is an advisory detail budget; native parts cannot be scaled. The 90-degree
opening is the existing semantic target, not a measured manufacturer value.
Reference images are not supplied in this diagnostic, so it cannot establish
image or appearance agreement.

## Diagnostic acceptance

`planner_door_diagnostic_pass` requires:

- English-description search and successful native queries before placement
  of every selected moving part type.
- A protected fixed mount outside the moving door submodel, with unchanged
  instances, transforms and geometry identity.
- Distinct panel, glass and hinge role parts in one moving subtree. The
  panel is white and the glass uses visible transparent native material.
- A native transverse revolute contact and at least a 90-degree opening
  from neutral. Actual native panel and glass vertices move upward while
  the fixed mount remains stationary.
- Current passing native static gates and ordinary motion poses, including
  nine distinct passing poses covering the declared limits. The existing
  host checks connectivity and collisions at those poses. Full vehicle
  reports retain their actual statuses: missing wheels can leave the static
  report unverified and the steering-response requirement failed. The
  diagnostic accepts only these absent-wheel requirements as out of scope;
  native collision, connectivity, range and independent-check failures
  still block the door.
- A passing `module_check(..., compare=False)` for this candidate, current
  preview pixels in the planner input, export and explicit planner finish.
- An IO archive that reads back to the same graph, unchanged execution,
  probe and input sources, and no unrecovered tool errors.

The full vehicle contract remains present. Only this task's door roles
require panel, glass and hinge because its request explicitly includes
moving glazing. The probe does not weaken the host's full vehicle gate.
`compare=False` measures this candidate; it does not certify the required
three distinct passing structural candidates. Semantic role review,
appearance, cabin integration, Studio GUI compatibility, physical retention
and load capacity remain unverified. `vehicle_completion_ready` is always
false in this diagnostic.

## Regression boundaries

`tests/test_countach_door_probe.py` contains 27 synthetic validator cases.
They check actual panel/glass displacement, fixed-mount preservation, nine
distinct passing poses, transverse axis and opening range, upward motion,
white panel material, ownership, visible transparency and stale native
evidence. Additional cases reject accidental selection credit from the
seeded mount, selection after a new moving instance was introduced, and
native failures hidden behind missing whole-vehicle wheels. The actual
selection validator is used for the seeded-part cases. Native checks are
monkeypatched to isolate these rules. The tests do not establish successful
native assembly.

The initial corrected focused regression passed: **23 tests in 0.99 seconds**.
Before the scope repair, the complete regression passed 1,306 tests with
14 warnings in 620.59 seconds. That result does not certify the later
scope repair. After that repair, the complete regression passed 1,316 tests
with 14 warnings in 794.15 seconds. After connector-feedback changes and
the combined-motion validator correction, the complete regression passed
**1,328 tests with 14 warnings in 1,025.54 seconds**. The log is
`runs/countach-door-regression-20261030-v4.log`. The focused regression
passed 44 tests in 28.80 seconds; all 106 historical example hashes and
LDraw/BOM totals passed. The frontend production build passed using
`.next-countach-door-20261030-v4`; its log is
`runs/countach-door-frontend-build-20261030-v4.log`.

## Live attempts

The initial `countach-door-live-v1` stopped before the first planner action
with `Candidate requires a host-required module`. The task identifier had
used a diagnostic name instead of the contract's `door_left` key. The probe
now uses `id="door_left"` and a separate diagnostic label. The initial
artifacts and log remain preserved. This was a harness preflight failure,
not evidence of successful or failed planner assembly.

The corrected task identifier allowed `countach-door-live-v2` to reach
32 planner actions. The run exposed two harness problems: the prebuilt
mount's `3700.dat` introduction incorrectly failed selection for a later
moving instance of the same type, and full vehicle wheel requirements
prevented a single-door diagnostic from passing. The native door motion
itself had nine passing poses at revision 8. The run was deliberately
interrupted before changing sources. Its original raw reports, trajectory
and running checkpoint remain intact; there is no final diagnostic report,
pixel feedback or accepted door result from this attempt.

The probe now records the trajectory boundary immediately after seeding and
requires search/query evidence before each new moving part type is first
placed after that boundary. Full vehicle check and motion statuses are
retained, while the local gate evaluates their native door evidence. No
shared production executor or full vehicle acceptance rule was changed.

`countach-door-live-v3` terminated with `repair_budget_exhausted` after
22 planner actions and 770.172 seconds. Three incompatible glass attachments
exhausted its repair budget. The failed endpoints included edge grid ports
with area 2; these are not valid full stud/hole contacts. Its final report
has `planner_door_diagnostic_pass=false` and `execution_unchanged=true`.
It has no IO roundtrip or pixel feedback. The original failed artifacts
remain intact; this is not an accepted door mechanism.

The shared snap/attach executor now reports the actual selected part, port,
kind, subtype, altitude and area, plus up to eight compatible alternatives
per endpoint while holding the opposite selected port fixed. Empty lists
do not establish that the two parts have no other compatible pair. These
are connector-type suggestions only: seating, keys, collisions,
connectivity and motion still require native checks. Rejected snap,
attach and build calls preserve the graph, revision and saved checkpoint.
The diagnostic is delivered directly in the planner's next context as
well as retained in the full error. Synthetic and native regressions cover
bounded/empty alternatives and atomic rejection. The local motion
validator also checks poses inside combined-motion groups.

## Measured live trial v4

`countach-door-live-v4` stopped with `planner_unavailable` after
**1,640.625 seconds**: 54 tool decisions completed and decision 54 recorded
a planner availability error. The local Codex subprocess exhausted its
five retries after reporting the ChatGPT Subscription Sharing usage limit.
The final report has `planner_door_diagnostic_pass=false` and
`execution_unchanged=true`. No new authorization is required for this
failure; it is unrelated to the completed GitHub repository deletion.

The planner assembled a white panel, white window frame and transparent
glass on a native transverse door joint. Revision 11 passed the native
static gates with no collisions and all nine ordinary door poses from
0 to 90 degrees. The original full-vehicle reports remain unverified or
failed for absent wheels; they are not rewritten as whole-vehicle passes.
The fixed bearing stayed outside the moving module.

At step 33, an incompatible frame attachment received the new connector
diagnostic. Step 34 used central native contacts successfully; the raw
failure and recovery remain recorded. This single observed recovery does
not establish a causal improvement or a success-rate estimate. A role
binding schema error was also corrected in the trace.

The first moving `3700.dat` query preceded its description search, which
failed the diagnostic's selection ordering rule. Re-querying and replacing
the same part did not rewrite its first-introduction history. At step 53,
the planner instead replaced it with previously searched and queried
`32000.dat`; final selection evidence passes for all six moving part types.
The final BOM contains seven parts including the supplied fixed bearing.
The next planner invocation failed before checking this revision 12, so
revision 11 checks and motion are stale for the final graph. There are no
live preview pixels, export, IO roundtrip or explicit successful finish.
The failed report and checkpoint are preserved without modification.

The frozen execution hash is
`f28172b852d3c4791b7491ccdb914f93cd4b18b2df8150b12610934fd38a3205`;
the input hash is
`aa36ac907a9b404fb210dd6744e3f20255e19eb2554d927a827a95e2e27468c9`.

| Preserved v4 evidence | SHA256 |
| --- | --- |
| `report.json` | `a0a1569da4d3e8e2354b0c3b00f007fe0ee002a8d149976d53c869ca1af07def` |
| `assembly/checks.json` (revision 11) | `0302613ac344bd056e3572321000729697c2ab437c2858f2a5acf7c52d10d5c1` |
| `assembly/motion.json` (revision 11) | `967b87391633aab4e4d9f5065270a6c767c4ad51abbbe0682204696c1182892f` |
| `assembly/agent-decisions.jsonl` | `b3cec31c733e5b58a10ad455178f01faed8d0b736fc9fd7b3a09547157e7503f` |
| `assembly/trajectory.jsonl` | `c3a9b2da932266c47d67f3840e4134ae06cbca3675e98310585fc0b82c1d665b` |

## Independent offline postcheck

After the planner stopped, its final state and selection trajectory were
copied to `countach-door-live-v4-postcheck/assembly`. Offline calls ran
`check`, `motion(samples=9)`, `preview` and `export` on revision 12 without
editing the graph. Every file in the original live attempt was hashed
before and after; `source_unchanged=true`.

This postcheck found a real assembly regression: the replacement brick
left the window frame and glass in a separate two-part connected component
from the five-part bearing/panel/hinge group. Native connections and digital
assembly fail despite zero collisions. All nine door poses fail their
native checks. The final live failure therefore cannot be attributed only
to the external planner limit; final geometry also needs repair.

The offline IO archive roundtrips to the same graph. The six-view contact
sheet was opened and inspected: the native white panel, frame and dark
support are visible and framed. The clear insert is not visually distinct
against the white frame in these views, and the simple assembly does not
establish Countach appearance. These pixels were never sent to the live
planner. The offline report retains `planner_door_diagnostic_pass=false`,
no planner pixel feedback and no vehicle completion. Export integrity is
not assembly or appearance acceptance.

| Offline evidence, separate from live v4 | SHA256 |
| --- | --- |
| `report.json` | `d483a840bcf7a7aadc30173357db582d3384759d998f24f207e8a4bf870c6928` |
| `assembly/checks.json` | `73f68ab4f13b94e4507afc983015f362330b8b330c33219f77ee6fb412d84694` |
| `assembly/motion.json` | `56b7a96a47964a81b417356e0e9c16e53fc73a39e032c1c57cc423a7c8b63c98` |
| `assembly/model.io` | `d409926551f9ae6e6549aa0ee9074ef10f96a281b7b0ed861fef6d316aae58be` |
| Six-view contact sheet | `5eb852cb64fb62f429e1d9ebb05ea56c48eb59a13a7c3674d9c71cfe643f93a2` |

The next door trial must retain valid selection evidence and reseat the
frame/glass on the replacement support, then repeat current static and
motion checks before planner pixel feedback, export and finish. Three
structural candidates, independent role review and cabin integration
remain separate outstanding work.
