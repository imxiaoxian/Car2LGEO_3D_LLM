"""Replay a saved phase script to diagnose host export without another model request."""
import argparse
import json
from pathlib import Path
import shutil

from car2lego3d.blender_mcp import BlenderSession, render_code, validate_code
from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.model_quality import assess_model
from car2lego3d.semantic import load_scene
from car2lego3d.staged_model import PHASES, phase_quality_issues
from car2lego3d.vehicle import load_spec


def run(code, specifications, output, phase="running-gear", base_blend=None):
    code, specifications, output = [Path(p).resolve() for p in (code, specifications, output)]
    if output.exists() and any(output.iterdir()):
        raise ValueError("Replay requires a new directory")
    output.mkdir(parents=True, exist_ok=True)
    sources = {str(p): digest(p) for p in (code, specifications)}
    base_blend = Path(base_blend).resolve() if base_blend else None
    if base_blend:
        sources[str(base_blend)] = digest(base_blend)
    shutil.copyfile(code, output / "input-phase.py")
    shutil.copyfile(specifications, output / "input-specifications.json")
    result = dict(schema="blender-phase-replay.v1", phase=phase, sources=sources,
        exporter_sha256=digest(ROOT / "scripts/blender_export_render.py"),
        mesh_guard_sha256=digest(ROOT / "scripts/blender_mesh_guard.py"), status="running",
        boundary_fit_sha256=digest(ROOT / "scripts/blender_boundary_fit.py"),
        quality_accepted=False, website_acceptance=False, fresh_creation_delivery=False)
    write_json(output / "replay.json", result)
    try:
        with BlenderSession(output) as blender:
            if base_blend:
                blender.execute("import bpy\nbpy.ops.wm.open_mainfile(filepath=" + repr(str(base_blend)) + ")",
                                "load-base-scene")
            blender.execute(validate_code(code.read_text(encoding="utf-8")), "replay-phase")
            blender.execute(render_code(output, provenance=result), "export-replay")
        semantic = load_scene(output / "semantic-scene.json")
        quality = assess_model(output, load_spec(specifications))
        issues = phase_quality_issues(quality, phase)
        result.update(status="exported", phase_geometry_passed=not issues, phase_issues=issues,
            semantic_nodes=len(semantic["nodes"]),
            nodes_with_ports=sum(bool(n.get("ports")) for n in semantic["nodes"]),
            nodes_with_joints=sum(bool(n.get("joint")) for n in semantic["nodes"]))
    except BaseException as error:
        result.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        result["sources_unchanged"] = all(digest(Path(p)) == h for p, h in sources.items())
        write_json(output / "replay.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("code", "specifications", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--phase", choices=[phase[0] for phase in PHASES], default="running-gear")
    parser.add_argument("--base-blend", type=Path)
    print(json.dumps(run(**vars(parser.parse_args())), ensure_ascii=False))
