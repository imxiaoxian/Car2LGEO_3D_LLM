"""Blender worker: six fully framed native-geometry views for planner feedback."""
import json
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


source, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(source))
objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
points = [obj.matrix_world @ Vector(vertex) for obj in objects for vertex in obj.bound_box]
if not points:
    raise ValueError("No resolved native part geometry")
low = Vector([min(point[i] for point in points) for i in range(3)])
high = Vector([max(point[i] for point in points) for i in range(3)])
center, size = (low + high) / 2, high - low
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 12
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 480, 360
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.8, .8, .8, 1)
scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .15
scene.view_settings.view_transform = "AgX"
light_scale = max(size.length, .001)
lighting = []
for name, delta, energy in [("key", (-1, -1, 2), 500), ("fill", (1, 1, 1.5), 350), ("rim", (.5, -1, 1), 200)]:
    light = bpy.data.lights.new(name, "AREA")
    light.energy = energy * light_scale ** 2
    light.shape, light.size = "DISK", light_scale
    obj = bpy.data.objects.new(name, light)
    scene.collection.objects.link(obj)
    obj.location = center + Vector(delta) * light_scale
    obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
    obj.visible_glossy = False
    lighting.append(dict(name=name, energy_w=light.energy, size_m=light.size, position_m=list(obj.location)))
data = bpy.data.cameras.new("preview-camera")
camera = bpy.data.objects.new("preview-camera", data)
scene.collection.objects.link(camera)
scene.camera = camera
data.type, data.clip_start = "ORTHO", .0001
data.clip_end = max(100, size.length * 20)
views = [("front", (-1, 0, 0)), ("back", (1, 0, 0)), ("left", (0, -1, 0)),
    ("right", (0, 1, 0)), ("top", (0, 0, 1)), ("isometric", (-1, -1, .8))]
images = []
for name, direction in views:
    camera.location = center + Vector(direction).normalized() * max(size.length * 3, 1)
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    inverse = camera.matrix_world.inverted()
    projected = [inverse @ point for point in points]
    # A projected asymmetric assembly need not be centred on its world-space AABB.
    width = 2 * max(abs(point.x) for point in projected)
    height = 2 * max(abs(point.y) for point in projected)
    data.ortho_scale = max(width, height * (480 / 360), .001) * 1.15
    bpy.context.view_layer.update()
    projected = [world_to_camera_view(scene, camera, point) for point in points]
    bounds = [[min(point[i] for point in projected), max(point[i] for point in projected)] for i in (0, 1)]
    if any(a < 0 or b > 1 for a, b in bounds):
        raise ValueError("Camera clips native geometry: " + name)
    scene.render.filepath = str(output / (name + ".png"))
    bpy.ops.render.render(write_still=True)
    images.append(dict(view=name, file=name + ".png", camera=dict(
        position_m=list(camera.location), target_m=list(center), projected_bounds=bounds,
        complete_geometry_in_frame=True)))
(output / "views.json").write_text(json.dumps(dict(schema="assembly-preview.v1", images=images,
    bounds_m=[list(low), list(high)], material_shaders_modified=False,
    render_rig=dict(schema="assembly-preview-lighting.v1", scale_m=light_scale, lights=lighting,
        world_strength=.15, view_transform="AgX", optical_measurement=False),
    coordinates="Blender world; source GLB imported through glTF", appearance="unverified"), indent=2), encoding="utf-8")
