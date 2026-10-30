"""Exercise bounded boundary fitting and rollback in a real isolated Blender session."""
import argparse
from pathlib import Path

from car2lego3d.blender_mcp import BlenderSession
from car2lego3d.common import ROOT, digest, write_json


PROBE = '''import bpy
from mathutils import Vector

def mesh_object(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj

def panel(name, endpoints):
    a, b = [Vector(v) for v in endpoints]
    obj = mesh_object(name, [a, b, b + Vector((0,-.1,0)), a + Vector((0,-.1,0))], [(0,1,2,3)])
    group = obj.vertex_groups.new(name='edge')
    group.add([0,1], 1, 'REPLACE')
    return obj

target = mesh_object('planar_target', [(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [(0,1,2,3)])
obj = panel('planar_source', [(0,0,.02),(1,0,.02)])
bpy.context.view_layer.update()
before = [tuple(v.co) for v in obj.data.vertices]
dry = car2lego_fit_boundary(obj.name, 'edge', [target.name], commit=False)
assert dry['committed'] is False and before == [tuple(v.co) for v in obj.data.vertices]
result = car2lego_fit_boundary(obj.name, 'edge', [target.name])
assert result['max_gap_m'] < 1e-6

v_target = mesh_object('creased_target', [(0,0,.037),(.37,0,0),(1,0,.063),
    (0,1,.037),(.37,1,0),(1,1,.063)], [(0,1,4,3),(1,2,5,4)])
obj = panel('creased_source', [(0,0,.037),(1,0,.063)])
bpy.context.view_layer.update()
result = car2lego_fit_boundary(obj.name, 'edge', [v_target.name], max_distance=.1, tolerance=.003)
assert result['refinements'] > 0 and result['added_vertices'] > 0
assert result['max_gap_m'] <= .003
assert not obj.data.attributes.get('car2lego_boundary_original')

gap_target = mesh_object('disjoint_target', [(0,0,0),(.2,0,0),(.2,1,0),(0,1,0),
    (.8,0,0),(1,0,0),(1,1,0),(.8,1,0)], [(0,1,2,3),(4,5,6,7)])
obj = panel('disjoint_source', [(0,0,0),(1,0,0)])
bpy.context.view_layer.update()
before = ([tuple(v.co) for v in obj.data.vertices], [tuple(p.vertices) for p in obj.data.polygons])
try:
    car2lego_fit_boundary(obj.name, 'edge', [gap_target.name], max_distance=.1)
except ValueError as error:
    assert 'source mesh preserved' in str(error)
else:
    raise AssertionError('Disconnected surfaces were bridged')
assert before == ([tuple(v.co) for v in obj.data.vertices], [tuple(p.vertices) for p in obj.data.polygons])
print('CAR2LEGO_BOUNDARY_FIT_PROBE_PASSED')
'''


def run(output):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new probe directory")
    output.mkdir(parents=True, exist_ok=True)
    report = dict(schema="blender-boundary-fit-probe.v1", status="running",
        helper_sha256=digest(ROOT / "scripts/blender_boundary_fit.py"),
        vehicle_accepted=False, training_approved=False)
    try:
        with BlenderSession(output) as blender:
            blender.execute(PROBE, "boundary-probe")
        report["status"] = "pass"
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        write_json(output / "probe.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
