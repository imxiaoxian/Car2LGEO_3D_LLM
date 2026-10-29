"""Read actual shader inputs, glass normals and interior occlusion without saving the scene."""
import argparse
import json
from pathlib import Path

from car2lego3d.blender_mcp import BlenderSession
from car2lego3d.common import digest, write_json


INSPECT = '''import bpy, json
from mathutils import Vector
bpy.context.view_layer.update()
depsgraph = bpy.context.evaluated_depsgraph_get()
records = []
for obj in bpy.context.scene.objects:
    if obj.get('car_role') not in {'windscreen', 'rear_window', 'side_window'} or obj.type != 'MESH':
        continue
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    point = sum((obj.matrix_world @ v.co for v in obj.data.vertices), Vector()) / len(obj.data.vertices)
    normal = (obj.matrix_world.to_3x3().inverted().transposed() @ obj.data.polygons[0].normal).normalized()
    shaders = []
    for material in obj.data.materials:
        if not material or not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                shaders.append(dict(material=material.name, inputs={socket.name: (
                    float(socket.default_value) if isinstance(socket.default_value, (float, int))
                    else list(socket.default_value)) for socket in node.inputs
                    if hasattr(socket, 'default_value') and isinstance(socket.default_value, (float, int))}))
    hits = []
    for direction in (normal, -normal):
        origin = point + direction * .01
        for _ in range(4):
            hit, location, _, _, target, _ = bpy.context.scene.ray_cast(depsgraph, origin, direction, distance=2)
            if not hit:
                break
            hits.append(dict(direction=list(direction), object=target.name,
                role=target.get('car_role'), distance_m=(location - point).length))
            origin = location + direction * .01
    records.append(dict(object=obj.name, normal=list(normal), shaders=shaders, rays=hits,
        material_indices=sorted({p.material_index for p in mesh.polygons}),
        modifiers=[dict(name=m.name, type=m.type, thickness=getattr(m, 'thickness', None)) for m in obj.modifiers]))
    evaluated.to_mesh_clear()
print('CAR2LEGO_GLAZING=' + json.dumps(records))
'''


def run(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new diagnostic directory')
    output.mkdir(parents=True, exist_ok=True)
    source_hash = digest(source)
    with BlenderSession(output) as blender:
        blender.execute('import bpy\nbpy.ops.wm.open_mainfile(filepath=' + repr(str(source)) + ')', 'load')
        result = blender.execute(INSPECT, 'inspect')
    texts = [row['text'] for row in result['result']['content'] if row['type'] == 'text']
    text = '\n'.join(texts)
    start = text.index('CAR2LEGO_GLAZING=') + len('CAR2LEGO_GLAZING=')
    records, _ = json.JSONDecoder().raw_decode(text[start:])
    report = dict(schema='glazing-diagnosis.v1', source=str(source), source_sha256=source_hash,
        source_unchanged=digest(source) == source_hash, records=records,
        quality_accepted=False, repair_export=False)
    write_json(output / 'diagnosis.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    run(**vars(parser.parse_args()))
