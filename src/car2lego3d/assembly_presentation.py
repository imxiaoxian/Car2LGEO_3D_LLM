"""Render the exported native-part GLB without Studio or desktop automation."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from dotenv import dotenv_values

from .common import ROOT, digest, write_json
from .gpt_reference import configuration
from .gpu_lock import GPULock


def blender_binary(blender=None):
    config = dotenv_values(ROOT / ".env")
    source, _ = configuration()
    binary = Path(blender or os.getenv("CAR2LEGO_BLENDER") or config.get("CAR2LEGO_BLENDER")
                  or source / "backend/storage/tool_research/blender-4.5.14-windows-x64/blender.exe")
    if not binary.is_file():
        raise ValueError("Configured Blender binary is missing")
    return binary


def preview(glb, directory, *, blender=None, timeout=600):
    """A reusable whole-object inspection sheet, independent of vehicle semantics."""
    from PIL import Image, ImageDraw
    from .assembly_evidence import files_current, snapshot_files
    from .assembly_requirements import identity
    glb, directory = Path(glb).resolve(), Path(directory).resolve()
    binary = blender_binary(blender)
    script = ROOT / "scripts/render_assembly_preview.py"
    inputs = dict(glb_sha256=digest(glb), renderer_sha256=digest(script),
        host_sha256=digest(Path(__file__)), blender_sha256=digest(binary))
    output = directory / identity(inputs)
    manifest = output / "preview.json"
    if manifest.is_file():
        record = json.loads(manifest.read_text(encoding="utf-8"))
        if record.get("inputs") == inputs and files_current(record.get("evidence_files")):
            return record
        raise ValueError("Preview evidence changed; preserve it and use a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(binary), "--background", "--factory-startup", "--python-exit-code", "1",
        "--python", str(script), "--", str(glb), str(output)]
    with GPULock(purpose="assembly-preview", timeout=600), (output / "blender.log").open("wb") as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
            check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    record = json.loads((output / "views.json").read_text(encoding="utf-8"))
    if [row.get("view") for row in record.get("images", [])] != ["front", "back", "left", "right", "top", "isometric"]:
        raise ValueError("Preview is missing required views")
    sheet = Image.new("RGB", (1440, 776), "white")
    draw = ImageDraw.Draw(sheet)
    for index, item in enumerate(record["images"]):
        with Image.open(output / item["file"]) as image:
            image.load()
            if image.size != (480, 360) or not item["camera"]["complete_geometry_in_frame"]:
                raise ValueError("Preview view has invalid size or clipped geometry")
            tile = image.convert("RGB")
        x, y = index % 3 * 480, index // 3 * 388
        draw.text((x + 8, y + 8), item["view"], fill="black")
        sheet.paste(tile, (x, y + 28))
    sheet.save(output / "contact-sheet.png")
    record.update(inputs=inputs, directory=str(output), appearance="unverified", physical="unverified",
        evidence_files=snapshot_files([output / "contact-sheet.png", output / "views.json", script,
            Path(__file__), binary, *[output / row["file"] for row in record["images"]]]))
    if digest(glb) != inputs["glb_sha256"]:
        raise ValueError("Assembly GLB changed while rendering preview")
    write_json(manifest, record)
    return record


def render(glb, output, *, blender=None, timeout=1800):
    glb, output = Path(glb).resolve(), Path(output).resolve()
    binary = blender_binary(blender)
    if not glb.is_file():
        raise ValueError("Assembly GLB or configured Blender binary is missing")
    script = ROOT / "scripts/render_assembly_delivery.py"
    inputs = dict(glb_sha256=digest(glb), renderer_sha256=digest(script), blender=str(binary))
    manifest = output / "presentation.json"
    if manifest.is_file():
        record = json.loads(manifest.read_text(encoding="utf-8"))
        if record.get("inputs") == inputs and all(
                (output / item["file"]).is_file() and digest(output / item["file"]) == item["sha256"]
                for item in record.get("images", [])) and len(record.get("images", [])) == 14:
            return record
        raise ValueError("Presentation output already contains different or incomplete evidence; use a new directory")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(binary), "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(script),
               "--", str(glb), str(output)]
    with GPULock(purpose="assembly-render", timeout=600), (output / "blender.log").open("wb") as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                       check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record.update(inputs=inputs, appearance="unverified", studio="unverified", physical="unverified")
    record["blend_sha256"] = digest(output / "assembly.blend")
    for item in record["images"]:
        item["sha256"] = digest(output / item["file"])
    write_json(manifest, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = render(args.glb, args.output)
    print(json.dumps(dict(images=len(result["images"]), appearance=result["appearance"])))


if __name__ == "__main__":
    main()
