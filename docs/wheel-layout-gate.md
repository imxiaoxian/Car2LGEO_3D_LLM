# Native wheel layout admission

`assembly_layout.wheel_layout` measures the neutral vehicle pose from actual
transformed tyre triangles. It never uses a part origin as the tyre center.
Coordinates follow the current vehicle assembly frame: X front to rear,
Y down, Z left to right, in LDraw units (1 LDU = 0.4 mm).

Checks cover common tyre contact height, same-axle longitudinal alignment,
left/right separation, front/rear axle order and clearance, and a shared axle
centerline. Different front and rear tyre diameters are permitted when their
contact planes agree. Bound chassis geometry must remain above the tyre
contact plane. Chassis-owned surfaces exclude separately bound module
subtrees, so nested wheel surfaces do not become chassis surfaces.

The fixed geometric allowance is 2 LDU (0.8 mm), for native mesh surface
variation. It is smaller than a plate height and must not be enlarged to
admit a failed model. Unknown or missing geometry remains unverified.
Wheel contacts are not inferred for unbound modules. Scoped candidates can
pass provisionally with installed wheels only; final vehicle completion
requires every contract wheel. No-wheel contracts remain unaffected.

Admission runs in the scoped module/candidate check, global `check` and
export, and independently in final module evaluation. Thus an old passing
checks file cannot bypass current layout measurement. Reports bind graph
and module-binding hashes, native frames, measured bounds and policy.
Existing native dependency hashing records the resolved geometry files.
Layout does not replace role review, connector/collision checks, motion
sampling, appearance assessment or Studio compatibility checks.

Execution hashing includes the layout implementation. Recovery uses a new
revision, saves the source layout diagnosis, remeasures old candidates and
retains failed module measurements for planner feedback. It does not
silently transform completed source modules or choose a correct axle from
a relative mismatch alone.

When the source native layout fails, safe-boundary recovery cannot select a
tyre-free snapshot to make a completed carrier pass. The recovery records
`source_vehicle_layout_requires_structural_repair`, preserves diagnostic
geometry and leaves the affected modules available for structural repair.
The regression uses native tyre surfaces with an 82 LDU contact-height
disagreement and verifies that the source checkpoint remains unchanged.

## Countach reproduction

The immutable revision-79 audit is stored at
`runs/delivery-implementation-20261022/layout-gate-revision79-v2/layout.json`.
The original state hash remains
`42ca38cdd0512db2b378981af8fca448eb722343b9b2f10dcf58d66d20f5e316`.

Native tyre contact heights differ by 82 LDU (32.8 mm). The front tyre
contact plane also sits 42 LDU (16.8 mm) above the chassis bottom. The new
checker rejects that state; the right-rear wheel is still missing. This is
reproduction of a layout defect, not a repaired vehicle or delivery pass.
