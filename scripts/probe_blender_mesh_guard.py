"""Exercise the mesh guard through a real Blender MCP session without a model API."""
import argparse
from pathlib import Path

from car2lego3d.blender_mcp import BlenderSession
from car2lego3d.common import ROOT, digest, write_json


PROBE = '''import bpy
assert getattr(bpy.types.Mesh.from_pydata, "_car2lego_mesh_guard", False)
mesh = bpy.data.meshes.new("valid_triangle")
mesh.from_pydata([(0,0,0), (1,0,0), (0,1,0)], [], [(0,1,2)])
assert len(mesh.vertices) == 3 and len(mesh.polygons) == 1
broken = bpy.data.meshes.new("invalid_face_probe")
try:
    broken.from_pydata([(0,0,0), (1,0,0), (0,1,0)], [], [(0,1,99)])
except ValueError as error:
    assert "invalid_face_probe" in str(error) and "outside 3 vertices" in str(error)
else:
    raise AssertionError("Invalid procedural topology reached Blender")
assert len(broken.vertices) == 0
print("CAR2LEGO_MESH_GUARD_PROBE_PASSED")
'''


def run(output):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Probe requires a new directory")
    output.mkdir(parents=True, exist_ok=True)
    report = dict(schema="blender-mesh-guard-probe.v1", status="running",
        mesh_guard_sha256=digest(ROOT / "scripts/blender_mesh_guard.py"),
        quality_accepted=False, fresh_creation_delivery=False)
    try:
        with BlenderSession(output) as blender:
            blender.execute(PROBE, "first-probe")
            blender.execute(PROBE, "repeat-probe")
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        write_json(output / "probe.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
