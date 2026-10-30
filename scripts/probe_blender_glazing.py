"""Isolate saved glazing shader/lighting behavior with same-camera renders."""
import argparse
import json
from pathlib import Path

from car2lego3d.blender_mcp import BlenderSession
from car2lego3d.common import digest, write_json


PROBE = '''import bpy, json
from mathutils import Vector
scene = bpy.context.scene
glass = [obj for obj in scene.objects if obj.type == 'MESH' and obj.get('car_role') in
    {'windscreen', 'rear_window', 'side_window', 'quarter_window'}]
materials = {mat.name: mat for obj in glass for mat in obj.data.materials if mat and mat.use_nodes}
def socket_value(socket):
    value = getattr(socket, 'default_value', None)
    return value if isinstance(value, (int, float, str, bool)) else list(value) if value is not None else None
shaders = [{"material": mat.name,
    "nodes": [{"name": n.name, "type": n.type, "inputs": {s.name: socket_value(s) for s in n.inputs}}
        for n in mat.node_tree.nodes if n.type in {'BSDF_PRINCIPLED', 'OUTPUT_MATERIAL'}],
    "links": [{"from": link.from_node.name, "output": link.from_socket.name,
        "to": link.to_node.name, "input": link.to_socket.name} for link in mat.node_tree.links]}
    for mat in materials.values()]
config = {key: getattr(scene.cycles, key) for key in
    ['max_bounces', 'diffuse_bounces', 'glossy_bounces', 'transmission_bounces', 'transparent_max_bounces', 'samples']}
config.update(engine=scene.render.engine, transparent_film=scene.render.film_transparent)
saved = {name: [(n, [(s, socket_value(s)) for s in n.inputs]) for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED']
    for name, mat in materials.items()}
if not scene.camera:
    raise ValueError('Probe requires a saved inspection camera')
points = [obj.matrix_world @ Vector(p) for obj in scene.objects if obj.type == 'MESH' for p in obj.bound_box]
center = (Vector([min(p[i] for p in points) for i in range(3)]) + Vector([max(p[i] for p in points) for i in range(3)])) / 2
scene.camera.location = center + Vector((-1, -1, .55)).normalized() * 12
scene.camera.rotation_euler = (center - scene.camera.location).to_track_quat('-Z', 'Y').to_euler()
scene.cycles.samples = 12
scene.render.resolution_x = 1000
scene.render.resolution_y = 700
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
results = []
for mode in ['original', 'transmission_bounces', 'reflection_isolation', 'hide_glazing', 'dark_reflections', 'inspection_rig', 'dielectric_shader']:
    for obj in glass:
        obj.hide_render = mode == 'hide_glazing'
    background = scene.world.node_tree.nodes.get('Background')
    if background:
        background.inputs['Strength'].default_value = .04 if mode == 'dark_reflections' else .6
    for light in [obj for obj in scene.objects if obj.type == 'LIGHT']:
        light.visible_glossy = mode != 'dark_reflections'
    for nodes in saved.values():
        for node, values in nodes:
            for socket, value in values:
                if value is not None:
                    socket.default_value = value
            if mode == 'reflection_isolation':
                node.inputs['Specular IOR Level'].default_value = 0
                node.inputs['Coat Weight'].default_value = 0
    if mode == 'inspection_rig':
        exec(INSPECTION_LIGHTING)
        configure_inspection_lighting(scene, center)
    if mode == 'dielectric_shader':
        for mat in materials.values():
            node = mat.node_tree.nodes.new('ShaderNodeBsdfGlass')
            node.inputs['Color'].default_value = (.7, .78, .75, 1)
            node.inputs['Roughness'].default_value = .08
            node.inputs['IOR'].default_value = 1.48
            for output in [n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL']:
                mat.node_tree.links.new(node.outputs['BSDF'], output.inputs['Surface'])
    scene.cycles.max_bounces = 16 if mode == 'transmission_bounces' else config['max_bounces']
    scene.cycles.transmission_bounces = 12 if mode == 'transmission_bounces' else config['transmission_bounces']
    scene.render.filepath = OUTPUT + '/' + mode + '.png'
    bpy.ops.render.render(write_still=True)
    results.append(dict(mode=mode, file=mode + '.png'))
print('CAR2LEGO_PROBE=' + json.dumps(dict(config=config, shaders=shaders, images=results,
    camera=dict(position=list(scene.camera.location), rotation=list(scene.camera.rotation_euler),
        ortho_scale=scene.camera.data.ortho_scale))))
'''


def run(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new probe directory")
    output.mkdir(parents=True, exist_ok=True)
    original = digest(source)
    with BlenderSession(output) as blender:
        blender.execute("import bpy\nbpy.ops.wm.open_mainfile(filepath=" + repr(str(source)) + ")", "load")
        import ast
        from car2lego3d.common import ROOT
        renderer = (ROOT / "scripts/blender_export_render.py").read_text(encoding="utf-8")
        helper = next(node for node in ast.parse(renderer).body
                      if isinstance(node, ast.FunctionDef) and node.name == "configure_inspection_lighting")
        lighting = ast.get_source_segment(renderer, helper)
        result = blender.execute("OUTPUT = " + repr(str(output)) + "\nINSPECTION_LIGHTING = " + repr(lighting) + "\n" + PROBE, "probe")
    text = "\n".join(row["text"] for row in result["result"]["content"] if row["type"] == "text")
    record, _ = json.JSONDecoder().raw_decode(text.split("CAR2LEGO_PROBE=", 1)[1])
    record.update(schema="glazing-ab-probe.v1", source=str(source), source_sha256=original,
        source_unchanged=digest(source) == original, quality_accepted=False)
    for item in record["images"]:
        item["sha256"] = digest(output / item["file"])
    write_json(output / "probe.json", record)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
