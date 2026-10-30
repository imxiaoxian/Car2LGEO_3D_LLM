"""Bind calibrated reconstruction geometry as appearance evidence for native planners."""
import json
from pathlib import Path

from .common import digest, write_json
from .lego import load_mesh
from .vehicle_assembly_trials import document_resource


def prepare(directory, output, specifications, references):
    directory, output = Path(directory).resolve(), Path(output)
    mesh_path = directory / "calibrated.glb"
    reconstruction = directory / "reconstruction.json"
    calibration = directory / "physical-calibration.json"
    record = json.loads(reconstruction.read_text(encoding="utf-8"))
    physical = json.loads(calibration.read_text(encoding="utf-8"))
    if record.get("status") != "completed" or not str(record.get("backend", "")).startswith("Hunyuan3D-2mv"):
        raise ValueError("Shape evidence requires a completed Hunyuan multiview reconstruction")
    outputs = record.get("outputs", {})
    for path in (mesh_path, calibration):
        if outputs.get(path.name) != digest(path):
            raise ValueError("Shape evidence output checksum mismatch: " + path.name)
    if physical.get("glb_units") != "metres":
        raise ValueError("Shape evidence must use calibrated metres")
    spec_hash = digest(specifications)
    if any(item.get("sha256") != spec_hash for item in (physical.get("vehicle_spec", {}), record.get("vehicle_spec", {}))):
        raise ValueError("Shape evidence specifications differ from planner inputs")
    if record.get("inputs", {}).get("manifest_sha256") != digest(references):
        raise ValueError("Shape evidence references differ from planner inputs")
    mesh = load_mesh(mesh_path)
    geometry = dict(schema="shape-evidence.v1", units="metres", source="Hunyuan3D-2mv",
        frame=record.get("frame"), bounds_m=mesh.bounds.tolist(),
        vertices=mesh.vertices.tolist(), triangles=mesh.faces.tolist(),
        provenance={path.name: digest(path) for path in (mesh_path, reconstruction, calibration)},
        limitations=["Synthetic image reconstruction; front orientation is unverified.",
            "Bounding-box calibration is not measured shape accuracy.",
            "Appearance evidence only; no native ports, joints, retention or load evidence."],
        appearance_accepted=False, physical_verified=False)
    destination = output / "shape-evidence.json"
    write_json(destination, geometry)
    if destination.stat().st_size > 128 * 2**20:
        raise ValueError("Shape evidence exceeds planner resource size limit")
    summary = dict(sha256=digest(destination), storage="verified_json_file", bytes=destination.stat().st_size,
        units="metres", vertices=len(mesh.vertices), triangles=len(mesh.faces), bounds_m=geometry["bounds_m"],
        frame=geometry["frame"], limitations=geometry["limitations"], resource="shape_evidence",
        provenance=geometry["provenance"])
    bound = {str(path.resolve()): digest(path) for path in (mesh_path, reconstruction, calibration, destination)}
    return summary, document_resource(destination, summary["sha256"]), bound
