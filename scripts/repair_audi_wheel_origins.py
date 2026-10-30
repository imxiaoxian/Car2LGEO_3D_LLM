"""Migrate the verified 2023 Audi archive to current Studio wheel-part origins."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np

from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.official_library import ArchiveGeometry, decode
from car2lego3d.part_library import PartLibrary


MODEL_ID = "06fd9c4b71ffc41cb60545b0"
SOURCE_SHA = "4d5369c0a43fec811e6212b8b79a73385060a381b599d170506a73f6ba158722"
# Maps current local coordinates into the old embedded part's local frame.
REBASE = {
    "2491.dat": ("2491.dat", (0, -101.809, 0)),
    "3468c01.dat": ("100943p01.dat", (0, -48, 25.5)),
    "4192.dat": ("4192.dat", (0, -13.5, 22.35)),
}


def rebase_document(data):
    output, changed, skip = [], Counter(), False
    for line in decode(data).splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if body.upper().startswith("0 FILE "):
            skip = body[7:].strip().casefold() in REBASE
        if skip:
            if body.upper() == "0 NOFILE":
                skip = False
            continue
        record = body.split(maxsplit=1)[:1]
        start = 2 if record == ["1"] else 5 if record == ["11"] else None
        if start is not None:
            fields = body.split(maxsplit=start + 12)
            if len(fields) == start + 13 and fields[-1].casefold() in REBASE:
                name = fields[-1].casefold()
                replacement, offset = REBASE[name]
                position = np.asarray(fields[start:start+3], dtype=float)
                rotation = np.asarray(fields[start+3:start+12], dtype=float).reshape(3, 3)
                position += rotation @ offset
                fields[start:start+3] = [f"{value:.6f}" for value in position]
                fields[-1] = replacement
                line = " ".join(fields) + line[len(body):]
                changed[name] += 1
        output.append(line)
    return "".join(output).encode("utf-8"), dict(changed)


def obsolete_member(name):
    name = name.replace("\\", "/").casefold()
    if not name.startswith("customparts/"):
        return False
    return Path(name).stem + ".dat" in REBASE


def repair_archive(source, destination):
    if digest(source) != SOURCE_SHA:
        raise ValueError("This repair only applies to the verified original Audi archive")
    changes, removed = {}, []
    with ZipFile(source) as original, ZipFile(destination, "w", compression=ZIP_DEFLATED) as repaired:
        original.setpassword(b"soho0909")
        repaired.comment = original.comment
        for member in original.infolist():
            if obsolete_member(member.filename):
                removed.append(member.filename)
                continue
            content = original.read(member)
            if member.filename in {"model.ldr", "model2.ldr", "modelv2.ldr"}:
                content, changes[member.filename] = rebase_document(content)
            repaired.writestr(member, content)
    expected = dict.fromkeys(REBASE, 4)
    if changes != dict.fromkeys(("model.ldr", "model2.ldr", "modelv2.ldr"), expected):
        raise ValueError(f"Unexpected reference coverage: {changes}")
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("Repaired archive failed CRC verification")
    return dict(changed_references=changes, removed_obsolete_members=removed)


def validate_wheels(path, parts):
    geometry = ArchiveGeometry(path, parts)
    instances = geometry.instances()
    wheels = [m for n, _, m in instances if n == "56908.dat"]
    tyres = [m for n, _, m in instances if n == "2491.dat"]
    hubs = [m for n, _, m in instances if n == "100943p01.dat"]
    if len(instances) != 914 or [len(wheels), len(tyres), len(hubs)] != [4, 4, 4]:
        raise ValueError("Repair changed the Audi assembly inventory")
    remaining_tyres, remaining_hubs = list(tyres), list(hubs)
    results = []
    for wheel in wheels:
        center = wheel[:3, 3]
        tyre = min(remaining_tyres, key=lambda m: np.linalg.norm(center-m[:3, 3]))
        hub = min(remaining_hubs, key=lambda m: np.linalg.norm(center-(m @ [0, 0, -50, 1])[:3]))
        remaining_tyres = [m for m in remaining_tyres if m is not tyre]
        remaining_hubs = [m for m in remaining_hubs if m is not hub]
        tyre_error = float(np.linalg.norm(center-tyre[:3, 3]))
        hub_error = float(np.linalg.norm(center-(hub @ [0, 0, -50, 1])[:3]))
        axes = [m[:3, 2]/np.linalg.norm(m[:3, 2]) for m in (wheel, tyre, hub)]
        axis_error = max(float(np.linalg.norm(np.cross(axes[0], axis))) for axis in axes[1:])
        if tyre_error > .02 or hub_error > .25 or axis_error > 1e-4:
            raise ValueError(f"Wheel does not seat: {tyre_error}, {hub_error}, {axis_error}")
        results.append(dict(center_ldu=center.tolist(), tyre_center_error_mm=tyre_error*.4,
                            hub_seat_error_mm=hub_error*.4, axis_cross_error=axis_error))
    for name, _, _ in instances:
        geometry.triangles(name)
    if geometry.missing:
        raise ValueError(f"Missing geometry: {sorted(geometry.missing)}")
    library = PartLibrary(parts)
    dependencies = {}
    for name, _ in REBASE.values():
        library.triangles(name)
        for folder, suffix in (("connectivity", ".conn"), ("collider", ".col")):
            dependency = Path(parts) / folder / (Path(name).stem + suffix)
            if not dependency.is_file():
                raise ValueError(f"Missing native connection/collision data: {dependency}")
            dependencies[str(dependency)] = digest(dependency)
    dependencies.update(library.used)
    return dict(parts_count=len(instances), wheel_pairs=results, missing_geometry=[],
                native_dependencies=dependencies, studio_gui_verified=False)


def publish(result, catalog_path, candidate, report):
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    row = next(r for r in catalog["models"] if r["id"] == MODEL_ID)
    source = result / row["file"]
    if digest(source) != SOURCE_SHA or row["sha256"] != SOURCE_SHA:
        raise ValueError("Delivery no longer matches the verified original")
    report_dir = result / "_reports"
    backup = report_dir / "before-audi-wheel-origin-fix"
    backup.mkdir(exist_ok=True)
    metadata = [result / "车辆信息.json", report_dir / "manifest.json",
                report_dir / "final_integrity.json", report_dir / "chinese-sync-validation.json"]
    for path in [source, catalog_path, result / "models.csv", *metadata]:
        target = backup / path.name
        if target.exists() and digest(target) != digest(path):
            raise ValueError(f"Backup already exists with different contents: {target}")
        shutil.copy2(path, target)
    repair = dict(kind="studio_native_wheel_origin_migration", source_sha256=SOURCE_SHA,
                  report="_reports/audi-wheel-origin-fix.json")
    new_sha = digest(candidate)
    row.update(sha256=new_sha, bytes=candidate.stat().st_size, repair=repair)
    manifest_path = report_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(r for r in manifest if r["library_id"] == MODEL_ID)
    entry.update(sha256=new_sha, conversion=repair["kind"], repair=repair)
    entry["validation"]["embedded_custom_files"] -= sum(
        name.casefold().endswith(".dat") for name in report["removed_obsolete_members"])
    entry["validation"]["wheel_origin_check"] = report["validation"]["wheel_pairs"]
    integrity_path = report_dir / "final_integrity.json"
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    integrity_row = next(r for r in integrity["files"] if r["file"] == row["file"])
    integrity["total_io_bytes"] += candidate.stat().st_size - source.stat().st_size
    integrity_row.update(sha256=new_sha, bytes=candidate.stat().st_size, repair=repair)
    sync_path = report_dir / "chinese-sync-validation.json"
    sync = json.loads(sync_path.read_text(encoding="utf-8"))
    # The rename audit remains historical; record the subsequent content repair separately.
    sync.setdefault("subsequent_content_repairs", []).append({**repair, "id": MODEL_ID, "sha256": new_sha})
    with (result / "models.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.reader(stream))
    hash_column = csv_rows[0].index("SHA256")
    for csv_row in csv_rows[1:]:
        if csv_row[0] == row["file"]:
            csv_row[hash_column] = new_sha
    # Prepare and validate the replacement before replacing the public download.
    temporary = source.with_suffix(".repair.tmp")
    shutil.copy2(candidate, temporary)
    temporary.replace(source)
    write_json(catalog_path, catalog)
    write_json(result / "车辆信息.json", catalog)
    write_json(manifest_path, manifest)
    write_json(integrity_path, integrity)
    write_json(sync_path, sync)
    with (result / "models.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows(csv_rows)
    write_json(report_dir / "audi-wheel-origin-fix.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=Path(r"D:\lego\result"))
    parser.add_argument("--parts", type=Path, default=Path(r"D:\lego\Studio 2.0\ldraw"))
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/official-library/catalog.zh.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    row = next(r for r in catalog["models"] if r["id"] == MODEL_ID)
    source = args.result / row["file"]
    folder = ROOT / "runs/audi-wheel-origin-fix"
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / source.name
    report = repair_archive(source, candidate)
    report.update(schema="car2lego.audi-wheel-origin-fix.v1", source_sha256=SOURCE_SHA,
                  sha256=digest(candidate), file=source.name,
                  origin_mappings={old: dict(part=new, offset_ldu=offset) for old, (new, offset) in REBASE.items()},
                  validation=validate_wheels(candidate, args.parts))
    write_json(folder / "report.json", report)
    if args.apply:
        publish(args.result, args.catalog, candidate, report)
    print(json.dumps(dict(candidate=str(candidate), applied=args.apply,
                          wheels=report["validation"]["wheel_pairs"]), indent=2))


if __name__ == "__main__":
    main()
