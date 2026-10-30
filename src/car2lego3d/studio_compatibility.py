"""Explicit coverage of Studio samples, separate from per-file automated checks."""
from hashlib import sha256
from pathlib import Path
from zipfile import BadZipFile

from .assembly_evidence import files_current, snapshot_files
from .assembly_graph import IOArchive
from .assembly_requirements import identity
from .common import digest


def exporter_identity():
    root = Path(__file__).parent
    return identity({name: digest(root / name) for name in
        ("assembly_graph.py", "ldraw_document.py", "part_library.py")})


def features(archive):
    graph = archive.assembly.graph()
    result = {"native_parts"}
    if any(row["kind"] == "module" for row in graph["instances"]):
        result.add("nested_submodels")
    if any(row["local_color"] == 16 for row in graph["instances"]):
        result.add("inherited_color")
    for joint in graph["joints"].values():
        result.add("joint:" + joint.get("type", "revolute"))
    for info, data in archive.members:
        if info.filename.startswith("CustomParts/"):
            result.add("dependency:" + sha256(data).hexdigest())
    return result


def coverage(io, manifest):
    """Missing/changed evidence never becomes a compatibility pass."""
    required = features(IOArchive(io))
    covered, evidence_paths, accepted = set(), [], []
    for sample in manifest.get("samples", []):
        try:
            original, saved = Path(sample["original"]), Path(sample["saved"])
            screenshots = sample["evidence_files"]
            if (sample.get("reviewer_kind") != "human" or not sample.get("reviewer")
                    or sample.get("exporter_sha256") != exporter_identity()
                    or sample.get("operations") != dict(open=True, edit=True, save=True, reopen=True)
                    or not original.is_absolute() or not saved.is_absolute() or original == saved
                    or digest(original) != sample["original_sha256"] or digest(saved) != sample["saved_sha256"]
                    or not screenshots or not files_current(screenshots)
                    or any(not row.get("sha256") for row in screenshots)):
                continue
            source, reopened = IOArchive(original), IOArchive(saved)
            if not source.assembly.bom() or not reopened.assembly.bom():
                continue
            original_features = features(source)
            # Studio need not preserve the host's motion metadata, but must retain its submodels/dependencies.
            saved_required = {item for item in original_features if not item.startswith("joint:")}
            if not saved_required.issubset(features(reopened)):
                continue
            covered.update(original_features)
            accepted.append(str(original))
            evidence_paths.extend([original, saved, *(row["file"] for row in screenshots)])
        except (KeyError, ValueError, OSError, TypeError, RuntimeError, BadZipFile):
            continue
    return dict(status="pass" if required <= covered else "unverified",
        scope="representative_sample", file_opened_in_studio=False,
        required=sorted(required), covered=sorted(required & covered),
        missing=sorted(required - covered), samples=accepted, evidence_files=snapshot_files(evidence_paths))
