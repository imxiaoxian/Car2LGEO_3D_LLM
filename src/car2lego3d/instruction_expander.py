"""Expand a module-level plan into concrete, positioned LEGO parts.

An LLM cannot reliably emit thousands of positioned parts in one response, but
the reference models in ``D:/lego/result`` run 1127-4122 parts. The workable
split is for the model to decide *structure* (modules, sizes, colours) and for
this module to deterministically tile each module with real bricks and plates,
staggering seams between layers the way a human builder would.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .web3d_prototype import (
    BrickLibrary, BrickCategory, LEGOBrick, PlacedPart, Vector3,
)


# Footprint spans as (x, z) in studs, largest first so a module needs fewer
# pieces. These must agree with the real LDraw dimensions below, otherwise a
# tile claims more space than the part occupies and layers overlap.
BRICK_SPANS = ((4, 2), (3, 2), (2, 2), (4, 1), (3, 1), (2, 1), (1, 1))
PLATE_SPANS = ((4, 2), (3, 2), (2, 2), (4, 1), (3, 1), (2, 1), (1, 1))

# LDraw numbers, verified against measured geometry:
#   3001 Brick 2x4 (4x2)   3002 Brick 2x3 (3x2)   3003 Brick 2x2 (2x2)
#   3010 Brick 1x4         3622 Brick 1x3         3004 Brick 1x2  3005 Brick 1x1
# Plates are the same footprints at 0.4 stud pitch.
BRICK_PARTS = {
    (4, 2): "3001", (3, 2): "3002", (2, 2): "3003",
    (4, 1): "3010", (3, 1): "3622", (2, 1): "3004", (1, 1): "3005",
}
PLATE_PARTS = {
    (4, 2): "3020", (3, 2): "3021", (2, 2): "3022",
    (4, 1): "3710", (3, 1): "3623", (2, 1): "3023", (1, 1): "3024",
}

# Vertical pitch: a brick course is 1.2 studs, a plate course 0.4.
BRICK_PITCH = 1.2
PLATE_PITCH = 0.4

# Measured real-part footprints, written by scripts/measure_parts.py. When
# present this replaces the hand-written tables above, because those tables
# only cover a dozen shapes and were the reason models topped out at 14 part
# types against 207-524 in the reference sets.
MEASURED_PATH = Path(__file__).resolve().parents[2] / "data" / "parts" / "measured.json"

_MEASURED: dict[str, dict] | None = None

# Structural parts are the ones worth tiling a body with. Large plates, domes,
# columns and wheel arches measure big but are not wall material: using them
# first fills a car body with a handful of pieces (145 parts against 800+ for
# the same volume tiled properly).
MAX_STRUCTURAL_AREA = 8        # studs² per part; covers 2x4 and 1x8
MAX_BRICK_HEIGHT = 1.6         # taller than this is a curved/dome/column piece


def _is_structural(info: dict) -> bool:
    kind = info.get("kind")
    area = int(info.get("width", 0)) * int(info.get("depth", 0))
    if area < 1 or area > MAX_STRUCTURAL_AREA:
        return False
    if kind == "brick":
        return float(info.get("height", 0)) <= MAX_BRICK_HEIGHT
    return True


def measured_parts() -> dict[str, dict]:
    """Load the measured part table once, or an empty mapping if absent."""
    global _MEASURED
    if _MEASURED is None:
        try:
            payload = json.loads(MEASURED_PATH.read_text(encoding="utf-8"))
            _MEASURED = payload.get("parts", {})
        except (OSError, ValueError):
            _MEASURED = {}
    return _MEASURED


def _spans_for(kind: str) -> tuple[tuple[int, int], ...]:
    """Footprints available for a course, largest first.

    Derived from measured parts when the table exists so the tiler can only
    choose sizes that a real part actually has.
    """
    table = measured_parts()
    if not table:
        return BRICK_SPANS if kind == "brick" else PLATE_SPANS
    footprints = {
        (int(info["width"]), int(info["depth"]))
        for info in table.values()
        if info.get("kind") == kind and _is_structural(info)
    }
    if not footprints:
        return BRICK_SPANS if kind == "brick" else PLATE_SPANS
    # Sort by area then by the longer edge so a big block is offered first.
    return tuple(sorted(footprints, key=lambda s: (-s[0] * s[1], -max(s))))


def _part_for(kind: str, span: tuple[int, int], offset: int = 0) -> str | None:
    """Choose a real part of the requested footprint and kind."""
    table = measured_parts()
    if not table:
        fallback = BRICK_PARTS if kind == "brick" else PLATE_PARTS
        return fallback.get(span)
    matches = sorted(
        part for part, info in table.items()
        if info.get("kind") == kind and _is_structural(info)
        and (int(info["width"]), int(info["depth"])) == span
    )
    if not matches:
        return None
    # Rotate through variants (3001 vs 3003b and so on) for visual variety.
    return matches[offset % len(matches)]


def _derived_brick(part_id: str, kind: str) -> LEGOBrick:
    """Build a definition straight from measured data (no geometry walk)."""
    info = measured_parts().get(part_id, {})
    pitch = PLATE_PITCH if kind == "plate" else BRICK_PITCH
    category = BrickCategory.PLATE if kind == "plate" else BrickCategory.BRICK
    return LEGOBrick(
        lego_id=part_id,
        name=info.get("name", f"LDraw {part_id.upper()}"),
        category=category,
        dimensions=Vector3(float(info.get("width", 1)), pitch,
                           float(info.get("depth", 1))),
        mass_grams=round(max(0.4, float(info.get("width", 1))
                             * float(info.get("depth", 1)) * pitch * 0.9), 2),
    )


@dataclass
class ModulePlan:
    """One structural block the model chose to build."""

    name: str
    origin: tuple[float, float, float]      # stud units, x/y/z
    size: tuple[int, int, int]              # width(x), height(y), depth(z) in studs
    color: str = "Black"
    fill: str = "solid"                     # solid | plate-only | shell
    step: int = 1
    accent_color: str | None = None


@dataclass
class BuildPlan:
    """Whole-model plan emitted by the LLM and expanded into parts."""

    name: str
    modules: list[ModulePlan] = field(default_factory=list)
    notes: str = ""
    # Explicit specialised pieces (wheels, windscreens) that cannot be produced
    # by tiling a box. Each entry is {part, position, rotation, color}.
    details: list[dict] = field(default_factory=list)

    @staticmethod
    def from_llm(data: dict) -> "BuildPlan":
        modules = []
        for index, raw in enumerate(data.get("modules", []), start=1):
            size = raw.get("size") or [2, 1, 2]
            origin = raw.get("origin") or [0, 0, 0]
            modules.append(ModulePlan(
                name=str(raw.get("name") or f"module {index}"),
                origin=(float(origin[0]), float(origin[1]), float(origin[2])),
                size=(max(1, int(size[0])), max(1, int(size[1])), max(1, int(size[2]))),
                color=str(raw.get("color") or "Black"),
                fill=str(raw.get("fill") or "solid"),
                step=int(raw.get("step") or index),
                accent_color=raw.get("accent_color"),
            ))
        details = []
        for raw in data.get("details", []) or []:
            part = str(raw.get("part") or "").strip().lower()
            if not part:
                continue
            position = raw.get("position") or [0, 0, 0]
            rotation = raw.get("rotation") or [0, 0, 0]
            details.append({
                "part": part,
                "position": [float(v) for v in list(position)[:3]],
                "rotation": [float(v) for v in list(rotation)[:3]],
                "color": str(raw.get("color") or "Black"),
            })
        return BuildPlan(name=str(data.get("name") or "Untitled"), modules=modules,
                         notes=str(data.get("notes") or ""), details=details)


def _span_for(remaining_x: int, remaining_z: int, spans, prefer: int = 0) -> tuple[int, int]:
    """Pick a span that fits the remaining footprint.

    ``prefer`` rotates through the candidate list so a module is built from a
    mixture of real parts instead of being tiled entirely in one size, which is
    both more realistic and closer to how official sets are built.
    """
    fitting = [(sx, sz) for sx, sz in spans if sx <= remaining_x and sz <= remaining_z]
    if not fitting:
        return 1, 1
    return fitting[prefer % len(fitting)]


def tile_layer(width: int, depth: int, spans, offset_x: int = 0, offset_z: int = 0):
    """Yield (x, z, span_x, span_z) tiles covering a width/depth footprint.

    ``offset`` staggers alternate layers so vertical seams do not line up,
    which is what keeps a real build from splitting apart.
    """
    z = 0
    row = 0
    while z < depth:
        span_z = min(_span_for(width, depth - z, spans)[1], depth - z)
        x = 0
        if row % 2 and offset_x:
            # Staggered row: a short tile leads, then the normal sequence starts
            # after it. Starting again at 0 would lay a second brick over the
            # first one.
            short = min(offset_x, width)
            yield 0, z, short, span_z
            x = short
        while x < width:
            span_x = min(_span_for(width - x, span_z, spans)[0], width - x)
            yield x, z, span_x, span_z
            x += span_x
        z += span_z
        row += 1


class _Occupancy:
    """Rejects parts whose bounding boxes would intersect an accepted part.

    Course-number comparison is not enough: a plate at y=0.2 and a 1.2-stud
    brick beside it overlap while occupying different courses. Rounding to
    integer stud cells is also not enough: a scaled module has fractional
    origins, so a part can straddle a cell boundary and slip through the grid
    check while its real box still intersects its neighbour.

    Boxes are bucketed on a coarse float grid and compared exactly on lookup.
    """

    BUCKET = 4.0

    def __init__(self):
        self._buckets: dict[tuple[int, int], list[tuple[float, ...]]] = {}
        self.dropped = 0

    @classmethod
    def _box(cls, y, x, z, sx, sz, height):
        return (x - sx / 2.0, x + sx / 2.0, y, y + height, z - sz / 2.0, z + sz / 2.0)

    @classmethod
    def _buckets_for(cls, box, span: float = 0.5):
        min_x, max_x, _, _, min_z, max_z = box
        start_x = int((min_x - 0.001) // cls.BUCKET)
        end_x = int((max_x + 0.001) // cls.BUCKET)
        start_z = int((min_z - 0.001) // cls.BUCKET)
        end_z = int((max_z + 0.001) // cls.BUCKET)
        for bx in range(start_x, end_x + 1):
            for bz in range(start_z, end_z + 1):
                yield bx, bz

    @staticmethod
    def _intersects(a, b, epsilon: float = 0.01) -> bool:
        # Touching is fine; only real penetration on every axis counts.
        return (a[0] + epsilon < b[1] and b[0] + epsilon < a[1]
                and a[2] + epsilon < b[3] and b[2] + epsilon < a[3]
                and a[4] + epsilon < b[5] and b[4] + epsilon < a[5])

    def covers(self, y, x, z, sx, sz, height) -> bool:
        box = self._box(y, x, z, sx, sz, height)
        for bucket in self._buckets_for(box):
            for other in self._buckets.get(bucket, ()):
                if self._intersects(box, other):
                    return True
        return False

    def claim(self, y, x, z, sx, sz, height) -> None:
        box = self._box(y, x, z, sx, sz, height)
        for bucket in self._buckets_for(box):
            self._buckets.setdefault(bucket, []).append(box)


def _brick_for(part_id: str | None, kind: str, catalogue=None) -> LEGOBrick | None:
    """Resolve a part ID to a brick definition.

    Measured parts get their definition straight from the measured table, which
    avoids a costly LDraw geometry walk during expansion. Anything else falls
    back to the shared resolver.
    """
    if not part_id:
        return None
    if part_id in measured_parts():
        return _derived_brick(part_id, kind)
    brick = BrickLibrary.resolve_brick(part_id, catalogue) if catalogue else None
    return brick or BrickLibrary.get_brick("3003")


def expand_module(module: ModulePlan, catalogue=None, start_id: int = 0,
                  occupied: "_Occupancy | None" = None) -> list[PlacedPart]:
    """Turn one module into positioned parts.

    Courses stack from the module origin upward, alternating brick and plate
    work, with each course's seams staggered against the one below.
    """
    parts: list[PlacedPart] = []
    width, height, depth = module.size
    ox, oy, oz = module.origin
    y = oy
    if occupied is None:
        occupied = _Occupancy()

    for layer in range(height):
        # Cap with a plate course so the build has a flat, studded top; plates
        # also make the body's contrasting bands read as trim. A one-stud-tall
        # module is a brick, not a slab: forcing a plate there collapses whole
        # models to a single part type.
        use_plate = (module.fill == "plate-only"
                     or (height > 1 and (layer == height - 1 or layer % 3 == 2)))
        kind = "plate" if use_plate else "brick"
        spans = _spans_for(kind)
        pitch = PLATE_PITCH if use_plate else BRICK_PITCH

        # Stagger only brick courses; a plate course is thin enough to bridge.
        stagger = (layer % 2) if not use_plate else 0

        for index, (tx, tz, sx, sz) in enumerate(
                tile_layer(width, depth, spans, stagger, stagger)):
            # The tiler's span is authoritative: the part must occupy exactly
            # the cells that were reserved for it. Re-picking a different span
            # here desynchronises position from footprint and overlaps parts.
            span = (sx, sz)
            part_id = _part_for(kind, span, offset=index + layer)
            brick = _brick_for(part_id, kind, catalogue)
            if brick is None:
                continue

            color = module.color
            if module.accent_color and layer % 2 == 1 and (tx + tz) % 3 == 0:
                color = module.accent_color

            x = ox + tx + sx / 2.0
            z = oz + tz + sz / 2.0
            # Guard against a plan whose modules overlap: drop a piece rather
            # than emit a model that fails validation.
            if occupied.covers(y, x, z, sx, sz, pitch):
                occupied.dropped += 1
                continue
            occupied.claim(y, x, z, sx, sz, pitch)
            part_color = color

            parts.append(PlacedPart(
                instance_id=start_id + len(parts),
                brick_definition=brick,
                position=Vector3(x, y, z),
                rotation=Vector3(0, 0, 0),
                color=part_color,
            ))
        y += pitch
    return parts


def scale_plan(plan: BuildPlan, factor: float) -> BuildPlan:
    """Return a copy of the plan grown or shrunk about the origin.

    Part count tracks volume, so a plan landing at a third of the requested
    budget needs its envelope scaled by the cube root of three. Doing this here
    rather than in the prompt keeps the result predictable: the model's sense
    of scale does not decide how large the build is.
    """
    modules = [
        ModulePlan(
            name=module.name,
            origin=tuple(v * factor for v in module.origin),
            size=tuple(max(1, int(round(v * factor))) for v in module.size),
            color=module.color,
            fill=module.fill,
            step=module.step,
            accent_color=module.accent_color,
        )
        for module in plan.modules
    ]
    details = []
    for detail in plan.details:
        scaled = dict(detail)
        scaled["position"] = [v * factor for v in detail["position"]]
        details.append(scaled)
    return BuildPlan(name=plan.name, modules=modules, notes=plan.notes,
                     details=details)


def expand_plan(plan: BuildPlan, catalogue=None, max_parts: int = 1500,
                target_ratio: float = 0.75, attempts: int = 3):
    """Expand every module, growing the plan until it fills the budget.

    The model decides proportions and structure; this decides physical size. A
    car plan returned at 10 x 6 x 24 studs would only ever reach a few hundred
    parts, so the envelope is scaled until the count approaches the budget.
    Returns ``(parts, spans)`` where spans maps module name to its
    ``(start, end)`` slice so instruction steps match what was built.
    """
    parts, spans = _expand_once(plan, catalogue, max_parts)
    if not plan.modules:
        return parts, spans

    for _ in range(attempts):
        if len(parts) >= max_parts * target_ratio:
            break
        factor = (max_parts * target_ratio / max(len(parts), 1)) ** (1 / 3)
        # Cap growth so an undersized plan cannot balloon into a shape nothing
        # like what was asked for.
        factor = min(factor, 3.0)
        if factor <= 1.05:
            break
        plan = scale_plan(plan, factor)
        parts, spans = _expand_once(plan, catalogue, max_parts)
    return parts, spans


def _expand_once(plan: BuildPlan, catalogue, max_parts: int):
    """Expand modules and then details, returning parts and their spans."""
    parts: list[PlacedPart] = []
    spans: list[tuple[str, int, int]] = []
    occupied = _Occupancy()
    for module in plan.modules:
        if len(parts) >= max_parts:
            break
        remaining = max_parts - len(parts)
        start = len(parts)
        module_parts = expand_module(module, catalogue, start_id=start,
                                     occupied=occupied)
        parts.extend(module_parts[:remaining])
        if len(parts) > start:
            spans.append((module.name, start, len(parts)))

    # Specialised pieces last: wheels and glazing sit on top of the bodywork,
    # and a model without them is missing the features that make it readable.
    if plan.details and len(parts) < max_parts:
        start = len(parts)
        for detail in plan.details:
            if len(parts) >= max_parts:
                break
            requested = detail["part"]
            if catalogue is not None:
                if not catalogue.has(requested):
                    resolved = catalogue.resolve(requested)
                    if not resolved:
                        continue
                    requested = resolved
            brick = (BrickLibrary.resolve_brick(requested, catalogue)
                     if requested else None)
            if brick is None:
                continue
            parts.append(PlacedPart(
                instance_id=len(parts),
                brick_definition=brick,
                position=Vector3.from_list(detail["position"]),
                rotation=Vector3.from_list(detail["rotation"]),
                color=detail["color"],
            ))
        if len(parts) > start:
            spans.append(("details", start, len(parts)))
    return parts, spans


def describe(parts: Iterable[PlacedPart]) -> dict:
    """Summarise an expanded build for reporting and acceptance checks.

    Extent comes from each part's bounding box, not its centre: positions are
    centres, so centre-only maths under-reports a model by one part.
    """
    parts = list(parts)
    if not parts:
        return {"parts": 0, "unique_types": 0, "colors": 0, "extent_studs": (0, 0, 0)}
    boxes = [p.get_bounding_box() for p in parts]
    lo_x = min(b.min.x for b in boxes)
    hi_x = max(b.max.x for b in boxes)
    lo_y = min(b.min.y for b in boxes)
    hi_y = max(b.max.y for b in boxes)
    lo_z = min(b.min.z for b in boxes)
    hi_z = max(b.max.z for b in boxes)
    return {
        "parts": len(parts),
        "unique_types": len({p.brick_definition.lego_id for p in parts}),
        "colors": len({p.color for p in parts}),
        "extent_studs": (
            round(hi_x - lo_x, 1),
            round(hi_y - lo_y, 1),
            round(hi_z - lo_z, 1),
        ),
    }
