"""Measure a curated set of LDraw parts and cache their real footprints.

Hand-written dimension tables drift from reality (the first version of this
project mislabelled 3001/3002/3003, which produced colliding models). This
script reads actual LDraw geometry once and writes ``data/parts/measured.json``
so the expander can tile with parts that really have the size it assumes.

Usage:
    python scripts/measure_parts.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from car2lego3d.ldraw_catalogue import load_catalogue  # noqa: E402
from car2lego3d.part_library import PartLibrary  # noqa: E402


# Part families worth measuring: the shapes a structural model is built from.
# Each prefix is expanded against the installed library.
PREFIXES = [
    # bricks 1xN and 2xN
    "3001", "3002", "3003", "3004", "3005", "3006", "3007", "3008", "3009",
    "3010", "3622", "3623", "6111", "6112", "2352", "2462",
    # plates
    "3020", "3021", "3022", "3023", "3024", "3031", "3032", "3034", "3035",
    "3036", "3460", "3666", "3710", "4477", "91405",
    # tiles (smooth, no studs)
    "3069", "2412", "2431", "3070", "6636",
    # slopes and curved slopes
    "3037", "3038", "3039", "3040", "3041", "3044", "3298", "3665", "4286",
    "85984", "11477", "15068", "50950", "61678", "93606", "41766",
    # round / cylindrical
    "3062", "3941", "4032", "11213", "4073", "20482", "98138",
    # Technic structural
    "3700", "3701", "3702", "3703", "3894", "3895", "32000", "32017",
    "32140", "40490", "41239", "32523", "32524",
]

# Footprints we actually tile with; anything much larger is not a wall piece.
MAX_SPAN_STUDS = 8.0
MIN_SPAN_STUDS = 0.8


def measure(library: PartLibrary, part_id: str) -> dict | None:
    try:
        triangles, _ = library.triangles(part_id + ".dat")
    except Exception:
        return None
    if triangles is None or len(triangles) == 0:
        return None
    points = triangles.reshape(-1, 3)
    lo = points.min(axis=0)
    hi = points.max(axis=0)
    studs = (hi - lo) / 20.0
    if not (MIN_SPAN_STUDS <= studs[0] <= MAX_SPAN_STUDS):
        return None
    if not (MIN_SPAN_STUDS <= studs[2] <= MAX_SPAN_STUDS):
        return None
    return {
        "width": round(float(studs[0]), 2),
        "height": round(float(studs[1]) * 20.0 / 24.0, 2),
        "depth": round(float(studs[2]), 2),
    }


def classify(dims: dict) -> str:
    height = dims["height"]
    if height <= 0.6:
        return "plate"
    if height >= 0.85:
        return "brick"
    return "other"


def build(catalogue, limit_per_prefix: int = 4) -> dict:
    root = next((r for r in catalogue.roots if (r / "parts").is_dir()), None)
    if root is None:
        raise SystemExit("No LDraw 'parts' directory found in the configured roots")
    library = PartLibrary(root)

    parts: dict[str, dict] = {}
    for prefix in PREFIXES:
        # Exact part first, then lettered or numbered variants.
        candidates = [prefix] if catalogue.has(prefix) else []
        candidates.extend(catalogue.sample(prefix, limit=limit_per_prefix))
        seen = set()
        for part_id in candidates[:limit_per_prefix + 1]:
            if part_id in seen or part_id in parts:
                continue
            seen.add(part_id)
            dims = measure(library, part_id)
            if dims is None:
                continue
            # Snap to the stud grid so tiling never overlaps: a part measured at
            # 3.94 studs is a 4-stud part with tolerance in its geometry.
            dims["width"] = round(dims["width"])
            dims["depth"] = round(dims["depth"])
            if dims["width"] < 1 or dims["depth"] < 1:
                continue
            parts[part_id] = {
                **dims,
                "kind": classify(dims),
                "name": f"LDraw {part_id.upper()}",
            }
    return {"source": str(root), "parts": parts}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "data" / "parts" / "measured.json"))
    parser.add_argument("--per-prefix", type=int, default=4)
    args = parser.parse_args(argv)

    catalogue = load_catalogue()
    if len(catalogue) == 0:
        print("No LDraw library found; set CAR2LEGO_LDRAW_ROOTS.")
        return 2

    result = build(catalogue, args.per_prefix)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    kinds: dict[str, int] = {}
    for info in result["parts"].values():
        kinds[info["kind"]] = kinds.get(info["kind"], 0) + 1
    footprints = {(i["width"], i["depth"]) for i in result["parts"].values()}
    print(f"Measured {len(result['parts'])} parts -> {output}")
    print(f"  kinds: {kinds}")
    print(f"  distinct footprints: {len(footprints)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
