# Web 3D LEGO Generation — Acceptance Report

**Date:** 2026-10-31
**Model:** gpt-6-astra (`CAR2LEGO_BLENDER_MODEL`)
**Part library:** 13419 LDraw parts indexed, 233 measured into `data/parts/measured.json`
**Benchmark:** complex car models in `D:/lego/result`

## What is being measured

Generated models are compared against the complex car models in `D:\lego\result`,
as requested. Those are official sets converted to LDraw, measured here by
counting instance lines in each `.io` archive's `model.ldr`:

| Reference model | Parts | Unique part types | Colours |
|---|---|---|---|
| 超级街头跑车 (8448-1) | 1127 | 207 | 12 |
| Lamborghini Countach (10337-1) | 1512 | 252 | 21 |
| Ferrari Daytona SP3 (42143-1) | 4122 | 524 | 21 |

The in-repo pipeline examples are smaller: F40 at 1:24 is 239 parts, BMW M4 at
1:24 is 328 parts.

Acceptance thresholds used by `web3d_acceptance.py`:

- at least 800 parts (`min_parts`)
- at least 40 unique part types (`min_unique_types`)
- at least 4 colours (`min_colors`)
- assembly validates with no overlap errors

## Result

**All three cases pass, and both vehicles now carry wheels.**

| Metric | Sports car | Pickup truck | Town house | Benchmark |
|---|---|---|---|---|
| Parts | 1478 | 1192 | 3000 | 1127–4122 |
| Unique part types | 98 | 57 | 68 | 207–524 |
| Colours | 5 | 5 | 6 | 12–21 |
| Structural score | 1.0 | 1.0 | 1.0 | — |
| Overlap errors | 0 | 0 | 0 | — |
| Instruction steps | 24 | 41 | 39 | — |
| Generation time | 104 s | 164 s | 468 s | — |
| Tokens | 11493 | 12689 | 15593 | — |

Commands:

```
python -m car2lego3d.web3d_acceptance --output runs/acc16 --case sports-car
python -m car2lego3d.web3d_acceptance --output runs/acc17 --case pickup-truck
python -m car2lego3d.web3d_acceptance --output runs/acc18 --case town-house
```

Records: `runs/acc16/sports-car/`, `runs/acc17/pickup-truck/` and
`runs/acc18/town-house/`, each holding `report.json`, `assembly.json` and
`model.ldr`.

The sports car came out at 38 x 14 x 70 studs. The Lamborghini Countach in the
reference set is 20 x 12 x 42 studs at 1512 parts, so the generated envelope is
in the right range rather than merely numerically similar.

### Wheels and glazing

Tiling only produces rectangular bodywork, so a filled box has no wheels and no
windscreen — earlier runs passed every numeric check while being a decorated
brick. The plan now carries an explicit `details` list for parts the tiler
cannot make, and vehicles are checked for at least one wheel or tyre part.

Details are placed **before** the bodywork claims space, so the tiler builds
around a wheel rather than the wheel being rejected as a collision. That
ordering was a real defect: placed last, every wheel inside the body volume was
dropped and the model still reported a clean structural score.

## Not verified

These are open, not passed:

- **Part variety is below the reference.** 57–98 types against 207–524. The
  expander tiles boxes with structural bricks and plates; the reference models
  also use slopes, wedges, Technic links and curved panels to shape a car body
  that no box tiling reproduces. Scale and wheels are matched; sculpted form is
  not. This is the largest remaining gap.
- **No collision check against real LDraw geometry.** Overlap is checked with
  stud-grid bounding boxes, so two parts that do not interpenetrate on the grid
  can still clash where their real geometry flares (slopes, arches, studs).
- **No clutch, physics or buildability verification.** A model that passes is
  free of grid overlaps and uses parts that exist; whether it holds together
  when lifted is not tested.
- **No physical or appearance acceptance.** Consistent with the project rule
  that generated exports and unit tests do not certify model appearance.

## Defects found and fixed during acceptance

Each of these was caught by a real run, not by review:

| Symptom | Cause | Fix |
|---|---|---|
| 966 parts, **1** part type | one-stud modules forced into a plate course, so everything became a plate | a one-stud module is now a brick course; span choice rotates through the part list |
| 1606 overlaps in a 650-part body | hand-written table mislabelled 3001/3002/3003 (real: 2x4, 2x3, 2x2), and tiles were placed at a span the part did not occupy | corrected against measured geometry; the tiler's span is authoritative |
| 145 parts for a car body | tiling preferred the largest footprint, and the measured set included 6x6 domes and 12-stud columns as "bricks" | restricted tiling to structural parts (≤8 stud² area, ≤1.6 stud height) |
| 98 parts and **structural score 0.0** | a plate at y=0.2 and a 1.2-stud brick overlap vertically while their course numbers differ | occupancy now tests real y intervals per stud cell and drops a piece that would clash |
| 122 parts for a two-storey house | the prompt never said how much volume the part budget fills | the prompt now states the target envelope in studs |
| Vehicles passed every check but had **no wheels** | tiling only makes rectangular bodywork, and the plan had no way to place a wheel; the checks never looked for one | the plan carries a `details` list, vehicles must place four wheels, and acceptance fails a vehicle without any wheel part |
| A wheel placed inside the body was dropped | details were appended after tiling, so the wheel collided with bodywork and was rejected | details are placed first and claim their space, so the tiler builds around them |

## Reproducing

```
# Index and measure the parts available in the installed LDraw library
python scripts/measure_parts.py

# Run one acceptance case against the live model
python -m car2lego3d.web3d_acceptance --output runs/acceptance --case town-house

# Run every case (each takes several minutes)
python -m car2lego3d.web3d_acceptance --output runs/acceptance
```

`CAR2LEGO_LDRAW_ROOTS` must point at an LDraw library; the default is
`D:/lego/Studio 2.0/ldraw`.

## Recommendation

Scale is reached and assemblies validate clean. To close the gap to the
reference models the next work is shape, not size: add slope, wedge and curved
panel families to the tiler so a module's silhouette can be shaped rather than
filled, and re-run the car cases until they pass. That is a scoped follow-up
rather than a change to this design.
