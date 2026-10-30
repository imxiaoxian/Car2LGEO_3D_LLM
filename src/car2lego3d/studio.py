"""Export recursive LDraw assemblies without claiming unobserved Studio acceptance."""
import json
import math
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image
import numpy as np

from .common import digest, stage
from .ldraw_document import Document


def assembly_sequence(text):
    """Keep transforms, colors, part IDs and step placement for lossless checks."""
    sequence = []
    for line in text.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if tokens[:2] in (["0", "FILE"], ["0", "NOFILE"]):
            raise ValueError("Studio export requires a flat LDraw assembly, not an MPD.")
        if tokens == ["0", "STEP"]:
            sequence.append(tuple(tokens))
        elif tokens[0] == "1":
            if len(tokens) != 15 or not tokens[-1].lower().endswith(".dat"):
                raise ValueError("Expected a standard .dat part with a full LDraw transform.")
            int(tokens[1])
            if not all(math.isfinite(float(value)) for value in tokens[2:14]):
                raise ValueError("Non-finite LDraw transform.")
            rotation = np.array(tokens[5:14], dtype=float).reshape(3, 3)
            if (not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5, rtol=0)
                    or not np.isclose(np.linalg.det(rotation), 1, atol=1e-5, rtol=0)):
                raise ValueError("Studio parts must use rigid transforms, without scaling or reflection.")
            sequence.append(tuple(tokens))
        elif tokens[0] != "0":
            raise ValueError("Only comments, steps and standard part references are supported.")
    if not any(row[0] == "1" for row in sequence):
        raise ValueError("LDraw assembly has no parts.")
    return sequence


def verify_archive(path, source):
    expected = Document(Path(source).read_text(encoding="utf-8"))
    with ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Corrupt Studio archive.")
        for name in ("model.ldr", "model2.ldr"):
            actual = Document(archive.read(name).decode("utf-8"))
            if actual.signature() != expected.signature():
                raise ValueError(f"Studio {name} changed the assembly or step sequence.")
        info = json.loads(archive.read(".info"))
        count = len(expected.instances())
        if info["total_parts"] != count:
            raise ValueError("Studio metadata part count mismatch.")
    return {"part_count": count, "step_count": sum(line.strip() == "0 STEP" for line in expected.sections[expected.root]),
            "submodel_count": sum(n != expected.root and not n.endswith(".dat") for n in expected.sections),
            "custom_part_count": len({i['part'] for i in expected.instances() if i['custom']}),
            "archive_roundtrip_verified": True, "studio_open_verified": False,
            "buildability": "unverified", "physical_verified": False}


def export_studio(source, directory, thumbnail=None):
    source, directory = Path(source), Path(directory)
    with stage(directory, "studio", {"ldraw": source.name, "sha256": digest(source)}) as record:
        content = source.read_text(encoding="utf-8")
        document = Document(content)
        count = len(document.instances())
        from .bricklayer_audit import audit_ldraw
        audit_source = directory / "bricklayer-input.ldr"
        audit_source.write_text(document.flatten(), encoding="utf-8")
        independent = audit_ldraw(audit_source, directory / "bricklayer-audit.json")
        if independent["enforced"] and independent["status"] != "pass":
            raise ValueError("Independent BrickLayer audit blocked Studio export; inspect bricklayer-audit.json")
        info = {"total_parts": count, "application": "Car2LEGO_3D",
                "description": "Car2LEGO_3D assembly; physical assembly unverified."}
        destination = directory / "model.io"
        temporary = destination.with_suffix(".io.tmp")
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("model.ldr", content)
                archive.writestr("model2.ldr", content)
                archive.writestr(".info", json.dumps(info))
                if thumbnail is not None:
                    with Image.open(thumbnail) as preview:
                        if preview.format != "PNG":
                            raise ValueError("Studio thumbnail must be a PNG.")
                        preview.verify()
                    archive.write(thumbnail, "thumbnail.png")
            report = verify_archive(temporary, source)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        record.update(**report, bricklayer=independent, format="unencrypted ZIP with recursive LDraw references",
                      outputs={"model.io": digest(destination)})
    return destination
