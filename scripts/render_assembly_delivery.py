"""Blender worker: six complete views and eight repeatable geometric crops."""
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view


source, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(source))
objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
if not objects:
    raise ValueError("No native part geometry in exported GLB")
points = [obj.matrix_world @ Vector(vertex) for obj in objects for vertex in obj.bound_box]
low = Vector([min(point[i] for point in points) for i in range(3)])
high = Vector([max(point[i] for point in points) for i in range(3)])
center, size = (low + high) / 2, high - low
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 24
scene.cycles.use_denoising = True
scene.render.resolution_x = 1200
scene.render.resolution_y = 900
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.world.use_nodes = True
background = scene.world.node_tree.nodes["Background"]
background.inputs["Color"].default_value = (.7, .7, .7, 1)
background.inputs["Strength"].default_value = .04
scene.view_settings.view_transform = "AgX"
lighting = []
for name, delta, energy, scale in [
        ("key", (-1, -1, 2), 500, 1.4), ("fill", (1, 1, 1.5), 350, 1.0),
        ("rim", (.5, -1, 1), 200, .8)]:
    light = bpy.data.lights.new(name, "AREA")
    light.energy = energy * max(size.length, .1) ** 2
    light.shape = "DISK"
    light.size = size.length * scale
    obj = bpy.data.objects.new(name, light)
    scene.collection.objects.link(obj)
    obj.location = center + Vector(delta) * size.length
    obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
    obj.visible_glossy = False
    lighting.append(dict(name=name, position_m=list(obj.location), energy_w=light.energy,
                         size_m=light.size, visible_glossy=False))
camera_data = bpy.data.cameras.new("inspection-camera")
camera = bpy.data.objects.new("inspection-camera", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera
camera_data.type = "ORTHO"
camera_data.clip_start = .0001
camera_data.clip_end = max(100, size.length * 20)
whole = [
    ("front", (-1, 0, .15)), ("rear", (1, 0, .15)),
    ("left", (0, -1, .1)), ("right", (0, 1, .1)),
    ("front_left", (-1, -1, .7)), ("rear_right", (1, 1, .7))]
details = [
    ("left_door_window", (.48, .03, .55), (0, -1, .2), .48),
    ("right_door_window", (.48, .97, .55), (0, 1, .2), .48),
    ("front_lights", (.04, .5, .36), (-1, -.3, .25), .46),
    ("rear_lights", (.96, .5, .36), (1, .3, .25), .46),
    ("cabin_front_connection", (.32, .5, .62), (-1, -1, 1), .52),
    ("cabin_rear_connection", (.72, .5, .62), (1, 1, 1), .52),
    ("front_wheel", (.23, .05, .22), (-.3, -1, .2), .33),
    ("rear_wheel", (.78, .05, .22), (.3, -1, .2), .33)]
images = []


def shot(name, category, target, direction, frame):
    target = Vector(target)
    direction = Vector(direction).normalized()
    camera.location = target + direction * max(size.length * 3, 1)
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    if category == "whole":
        inverse = camera.matrix_world.inverted()
        projected = [inverse @ point for point in points]
        width = max(p.x for p in projected) - min(p.x for p in projected)
        height = max(p.y for p in projected) - min(p.y for p in projected)
        camera_data.ortho_scale = max(width, height * (1200 / 900)) * 1.15
    else:
        camera_data.ortho_scale = max(size.x, size.y) * frame
    bpy.context.view_layer.update()
    projected = [world_to_camera_view(scene, camera, point) for point in points]
    frame_bounds = [[min(point[i] for point in projected), max(point[i] for point in projected)] for i in (0, 1)]
    if category == "whole" and any(low < 0 or high > 1 for low, high in frame_bounds):
        raise ValueError("Whole-vehicle camera clips exported geometry: " + name)
    scene.render.filepath = str(output / (name + ".png"))
    bpy.ops.render.render(write_still=True)
    images.append(dict(view=name, category=category, file=name + ".png", synthetic=False,
        camera=dict(position=list(camera.location), target=list(target), ortho_scale=camera_data.ortho_scale,
            projected_bounds=frame_bounds, complete_geometry_in_frame=category == "whole"),
        focus="whole geometry" if category == "whole" else "normalized geometric crop; verify module framing visually"))


for name, direction in whole:
    shot(name, "whole", center, direction, 1)
for name, fraction, direction, frame in details:
    target = low + Vector([size[i] * fraction[i] for i in range(3)])
    shot(name, "detail", target, direction, frame)
bpy.ops.wm.save_as_mainfile(filepath=str(output / "assembly.blend"))
(output / "presentation.json").write_text(json.dumps(dict(schema="assembly-presentation.v1", images=images,
    source=str(source), bounds_m=[list(low), list(high)], appearance="unverified",
    render_rig=dict(schema="blender-inspection-lighting.v1", world_strength=.04, lights=lighting,
        purpose="inspection of glazing, interior and panel boundaries",
        material_shaders_modified=False, optical_measurement=False)), indent=2), encoding="utf-8")
