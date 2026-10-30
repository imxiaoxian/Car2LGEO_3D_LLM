"""Read-only IO detail inventory. This is scope evidence, not delivery acceptance."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_materials import palette, rgba
from car2lego3d.baseline_detail import baseline_observations, detail_standard
from car2lego3d.common import digest, write_json
from car2lego3d.delivery_targets import TARGETS


def audit(source, case, library):
    source, library = Path(source), Path(library)
    target = TARGETS[case]
    source_hash = digest(source)
    if source_hash != target["source_sha256"]:
        raise ValueError("Baseline changed; review the detail standard before reuse: " + case)
    assembly = IOArchive(source).assembly
    graph = assembly.graph()
    colors = palette(library)
    parts = [node for node in graph["instances"] if node["kind"] == "part"]
    bom = Counter(node["part"] for node in parts)
    descriptions, unknown, springs = {}, [], []
    for part, count in sorted(bom.items()):
        path = library / "parts" / part
        if not path.is_file():
            unknown.append(part)
            continue
        with path.open(encoding="utf-8-sig", errors="replace") as stream:
            description = stream.readline().strip()
        descriptions[part] = description
        if re.search(r"\b(spring|shock absorber)\b", description, re.I):
            springs.append(dict(part=part, count=count, description=description))
    modules = []
    for name in assembly.sections:
        if not assembly.is_module(name):
            continue
        members = [node for node in parts if node["section"] == name]
        transparent, unknown_colors = [], []
        for node in members:
            color = rgba(node["color"], colors)
            if color is None:
                unknown_colors.append(node["id"])
            elif color[3] < 255:
                transparent.append(node["id"])
        modules.append(dict(name=name, direct_part_instances=len(members),
            transparent_instances=transparent, unknown_material_instances=unknown_colors,
            semantic_role="unverified", articulation="unverified"))
    observed = baseline_observations(case)
    door_rows = [row for row in modules if row["name"] in observed["door_sections"]]
    agrees = (len(parts) == observed["parts"]
        and [(r["part"], r["count"]) for r in springs] == [(r["part"], r["count"]) for r in observed["spring_parts"]]
        and len(door_rows) == len(observed["door_sections"])
        and all(not r["transparent_instances"] and not r["unknown_material_instances"] for r in door_rows))
    return dict(schema="baseline-detail-inventory.v1", case=case, source=str(source.resolve()),
        source_sha256=source_hash, source_unchanged=digest(source) == source_hash,
        standard=detail_standard(target), part_instances=len(parts), modules=modules,
        identified_spring_parts=springs, unresolved_part_descriptions=unknown,
        recorded_observations_match=agrees,
        limits="Part descriptions and submodel grouping do not verify geometry, joints or absent hidden mechanisms.",
        fresh_creation=False, digital_assembly="unverified", appearance="unverified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("D:/lego/result"))
    parser.add_argument("--library", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory to preserve previous evidence")
    results = []
    for case, target in TARGETS.items():
        sources = [p for p in args.source.glob("*.io") if "[" + target["set_num"] + "]" in p.name]
        if len(sources) != 1:
            raise ValueError("Baseline source is missing or ambiguous: " + case)
        result = audit(sources[0], case, args.library)
        write_json(args.output / (case + ".json"), result)
        results.append({key: result[key] for key in ("case", "source_sha256", "source_unchanged",
            "part_instances", "recorded_observations_match")})
    write_json(args.output / "summary.json", dict(schema="baseline-detail-inventory.v1", results=results))
    print(json.dumps(results))
    if not all(row["recorded_observations_match"] and row["source_unchanged"] for row in results):
        raise SystemExit("Baseline observations require review")


if __name__ == "__main__":
    main()
