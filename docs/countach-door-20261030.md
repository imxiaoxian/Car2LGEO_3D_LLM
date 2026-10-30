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

## Independent offline native-contact repair

`countach-door-live-v4-offline-repair` starts from the unchanged live
revision 12 and its original selection trajectory. It does not replace
the live attempt or its failed postcheck. Native port queries preceded
one strict atomic build containing two snaps: frame `17454.dat` port
`1:1:5` to support `32000.dat` port `0:1:3`, then glass `17457.dat` port
`0` to frame port `2`. The resulting revision 13 moves the frame and glass
together by -10 LDU on X. Their relative transform is unchanged within
1e-8; every other instance, the fixed bearing, role bindings and door
joint remain unchanged.

Current native static gates pass with one seven-part connected component,
zero collisions and no missing native evidence. All nine door poses from
0 to 90 degrees retain connectivity and pass collision checks. The panel
and glass rise with the door; their mean endpoint upward displacements
are 101.776 and 13.630 LDU respectively. Historical selection evidence
still passes. The full vehicle reports retain absent-wheel requirements;
the local diagnostic does not rewrite them as vehicle passes.

The IO archive roundtrips to the same graph. All original live files were
hashed before and after: `source_unchanged=true`. The actual six-view
contact sheet was opened and inspected. It shows a framed, nonblank white
Technic panel and white window frame; the clear insert remains difficult
to distinguish against the frame. This establishes a repaired digital
mechanism, not Countach appearance, physical retention or cabin fit.
The report explicitly keeps `live_planner=false`,
`planner_door_diagnostic_pass=false`, no planner pixel feedback and no
vehicle completion credit.

| Separate offline repair evidence | SHA256 |
| --- | --- |
| `report.json` | `c55ed1d4d981e9f35070767621c887d9cd7d8ca1608853096d2825fbeefe0d48` |
| `repair-operation.json` | `b7d07bd29b34823ca10b451f9f7a9499361c2dd72e027387944265aaa8f6fdd1` |
| `assembly/checks.json` | `9c87abd83403ee63dd5b6b51bf04daf7e7d47b5d07a2ff459e58dcc50fa361de` |
| `assembly/motion.json` | `70b4f797daa8927a18a4af5ef7e9eda3082b1f8bea2fc872e24eb40608d278ce` |
| `assembly/model.io` | `bf054bcb337100449d9342a95ff4e174abcb9dc7bf86bc3d865d67de4dec5bbd` |
| Six-view contact sheet | `4339ad782d4f24b8b0ba79aec3bc88eec927fbcb24d95b8e6959ff47708daf31` |

## Original live trace resumed

`countach-door-live-v4-resume-v1` copies the original failed v4 checkpoint
at revision 12 and continues its own planner trace. It does not import the
offline repair. The original execution, probe and mission hashes were
verified before resuming. The original decision bytes remain an exact
prefix, and hashes of every original v4 file are unchanged afterward.
The original failed v4, failed postcheck and successful offline repair
remain separate artifacts with their original acceptance labels.

The continuation finished in **529.234 seconds**, with cumulative agent
elapsed time **2,165.0 seconds**. The final agent has 70 decisions and
revision 14, `stop_reason=planner_finished` and
`planner_door_diagnostic_pass=true`. It briefly reintroduced an unqualified
support, then replaced it at step 63 with previously searched and queried
`32000.dat` and reseated the frame/glass. Native check, nine door poses,
module progress, preview, export and finish followed at steps 64 through 69.
The original connector and binding failures and their recoveries remain
in the trace; the transport failure at step 54 is separately recorded as
recovered when planner decisions resumed at step 55. No unrecovered errors
remain. This is one successful resumed trial, not a reliability estimate
or a fresh empty-scene trial.

The final seven-part BOM uses the supplied black `3700.dat`, black
`11214.dat` and `2780.dat`, white `11954.dat`, `17454.dat` and `32000.dat`,
and transparent `17457.dat` (color 47). Current native static gates and
all nine 0-90 degree door poses pass. The panel and glass rise by mean
101.776 and 33.630 LDU at the open endpoint; the protected fixed mount
remains unchanged. The final IO roundtrips to the same graph. Current
preview pixels reached planner decisions 68 and 69.

The actual final six-view contact sheet was opened and inspected. It is
nonblank and frames the panel, window/frame and hinge support. The clear
insert remains difficult to distinguish against the white frame; these
pixels do not establish Countach appearance. Native static and motion
reports retain their full-vehicle statuses for absent wheels. The local
door diagnostic does not certify candidate comparison, independent role
review, cabin fit, Studio compatibility, retention, load capacity, physical
assembly or a complete car. `vehicle_completion_ready=false`.

| Preserved live continuation evidence | SHA256 |
| --- | --- |
| `report.json` | `751a18b08d26ef07756e84efbcfb70f13fe8efc0f7bb8981c5dc105d121ccd39` |
| `assembly/agent-decisions.jsonl` | `5132741d2872356cfefbe20d61d7094516f985d37374e4eab39a753a4149370e` |
| `assembly/model.io` | `7ed72e040a82522d3ffb971fd419dc60a1f2b77fbae4f5049d5c29bc7d975d50` |
| Six-view contact sheet | `fd86988a47d41e2620af839fb9313ab053703d67c0c45b17b3d996b428ac0862` |

The same-source recovery regression log records 44 tests passing in
19.49 seconds (`runs/countach-door-recovery-regression-20261030.log`);
the separate frontend production build also passed
(`runs/countach-door-recovery-frontend-20261030.log`). These software
checks are separate from the measured native live result.
