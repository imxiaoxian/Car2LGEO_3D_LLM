# Car module evidence (2026-10-30)

The single-wheel sections below record the earlier live planner milestone.
The later deterministic 33-part closed-linkage chassis passes sampled native
kinematics, four-wheel layout and IO roundtrip checks, as documented in
[closed-linkage evidence](closed-linkage-20261030.md). That fixture is not an
autonomous planner-selected chassis or whole-car acceptance.

At the single-wheel milestone, the live Codex planner completed one
description-selected steerable wheel on a supplied fixed support. This
establishes a bounded digital module loop. Linked
two-wheel steering, chassis integration, axle retention, load capacity, physical
assembly order and whole-car appearance remain unverified.

## Implementation

`assembly-motion.v4` checks ancestor-related steering/rolling and
suspension/rolling pairs, in addition to individual joints and the existing
steering/suspension pairs. Ancestry follows actual parent links. Every sampled
pose requires native clearance and connectivity; missing geometry remains
unverified. Three-joint combinations and intermediate poses are not covered.

`scripts/probe_steerable_wheel.py` supplies only a fixed vertical bearing. The
planner must search descriptions and query native geometry/ports before choosing
moving parts, build nested steering and rolling submodels, inspect the preview,
export and explicitly finish. The acceptance check requires current native
checks, an unchanged support, two distinct axles, actual tyre geometry response,
81 combined poses, pixel feedback, IO graph roundtrip and unchanged execution
sources. Synthetic acceptance tests isolate these rules; they are not native
mechanical evidence.

## Live result

Local artifacts: `runs/car-modules-20261030/steerable-wheel-live-v1/`.
They remain excluded from Git.

- `module_loop_pass=true`, `stop_reason=planner_finished`, 1378.59 seconds.
- Six physical parts: supplied `3700.dat`, two `3706.dat` axles, `53586.dat`
  perpendicular joiner, `56145.dat` rim and `61481.dat` tyre.
- The planner repaired two rim/tyre-to-kingpin collisions by moving the rim and
  tyre along the rolling axle. The final graph is revision 8, with native static
  parts, transforms, connections and collision gates passing.
- Vertical steering covers -30 to +30 degrees; transverse rolling covers 0 to
  360 degrees in a nested submodel. Both nine-position sweeps and all 81 combined
  states pass. Maximum tyre-vertex displacement is 67.74 LDU during steering and
  108.00 LDU during rolling; the supplied support stays fixed.
- The planner received the actual six-view contact sheet before export and
  finish. Manual image inspection confirmed a visible, fully framed module.
  This does not certify color accuracy or vehicle appearance.
- IO export and graph roundtrip pass. There are no failed tool invocations;
  the earlier failing static check is preserved as a successful diagnostic
  invocation. `first_pass_module_loop_pass` therefore does not mean no repair.
- Execution and probe source hashes remained unchanged throughout the run.

| Artifact | SHA256 |
| --- | --- |
| `report.json` | `d41be5c5db1418fef7051b7ba0444be771d0de31ee2ac2eee52b22b12b8850f8` |
| `assembly/model.io` | `c1e5da48fdfc46d5aea353072e22fcd488dba8947ec222c94c03c5ac684607ac` |
| Planner input | `a9b9a730db74b08083e41a67f9ed81bbcf25f8d6e9878ee1adaa055040902b49` |
| Execution contract | `8e29abff0d84dac54c1d29a3640f0d046c0f8da0f5377bf2170b6f1721a8b2c9` |

Reproduction requires the configured Codex CLI and the native part library:

```powershell
.venv/Scripts/python.exe -m scripts.probe_steerable_wheel --output runs/steerable-wheel-new --timeout 1500
```

Use a new output directory. The script refuses to overwrite prior evidence.

## Validation and remaining work

The complete local Python suite passed: 1,251 tests, 14 warnings, 398.61 seconds.
The frontend production build passed in `.next-steering-20261030`; the existing
frontend service was not replaced. Logs are
`runs/car-modules-regression-20261030.log` and
`runs/car-modules-frontend-build-20261030.log`.

The next milestone after this historical single-wheel run was linked left/right
steering installed in a connected chassis, with actual wheel-axis response and
persistent native contacts. The later deterministic fixture establishes that
tool capability; a live planner trial remains pending. Existing
axial-clearance diagnostics do not establish physical retention. This single
seeded wheel is not an autonomous whole car, an unseen-part generalization
result, a Studio GUI acceptance result or a physically accepted assembly.
