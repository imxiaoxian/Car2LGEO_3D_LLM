"""Common export and camera rig; executed by Blender MCP after model creation."""
import bpy
import bmesh
import json
import math
from pathlib import Path
from mathutils import Vector
from mathutils.bvhtree import BVHTree

directory = Path(OUTPUT_DIRECTORY)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 1
for obj in list(scene.objects):
    if obj.type in {"LIGHT", "CAMERA"}:
        bpy.data.objects.remove(obj, do_unlink=True)
objects = [o for o in scene.objects if o.type in {"MESH", "CURVE", "SURFACE", "FONT"} and not o.hide_render]
if not objects:
    raise ValueError("Model contains no geometry")
normalized = []
for obj in objects:
    if obj.type != "MESH":
        continue
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # Consistent outward winding is required before later EXACT Boolean differences.
    if bm.faces and all(e.is_manifold for e in bm.edges):
        previous = bm.calc_volume(signed=True)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
        obj.data.update()
        if previous < 0:
            normalized.append(obj.name)
    bm.free()
bpy.context.view_layer.update()
depsgraph = bpy.context.evaluated_depsgraph_get()
points, object_bounds, geometry, semantic_geometry = [], [], {}, {}
for obj in objects:
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    vertices = [evaluated.matrix_world @ v.co for v in mesh.vertices]
    faces = [tuple(p.vertices) for p in mesh.polygons]
    mesh.calc_loop_triangles()
    inverse = evaluated.matrix_world.inverted_safe()
    semantic_geometry[obj.name] = dict(
        vertices_m=[list(inverse @ v) for v in vertices],
        triangles=[list(t.vertices) for t in mesh.loop_triangles],
        material_indices=[mesh.polygons[t.polygon_index].material_index for t in mesh.loop_triangles])
    triangles = len(mesh.loop_triangles)
    signed_volume = sum(vertices[t.vertices[0]].dot(vertices[t.vertices[1]].cross(vertices[t.vertices[2]]))
                        for t in mesh.loop_triangles) / 6
    edge_uses = {}
    for face in faces:
        for a, b in zip(face, face[1:] + face[:1]):
            key = tuple(sorted((a, b)))
            edge_uses[key] = edge_uses.get(key, 0) + 1
    evaluated.to_mesh_clear()
    if not vertices:
        continue
    points.extend(vertices)
    obj_low = [min(p[i] for p in vertices) for i in range(3)]
    obj_high = [max(p[i] for p in vertices) for i in range(3)]
    ancestors, parent = [], obj.parent
    while parent is not None:
        ancestors.append(parent.name)
        parent = parent.parent
    geometry[obj.name] = (vertices, faces)
    object_bounds.append(dict(name=obj.name, bounds_m=[obj_low, obj_high],
        role=obj.get("car_role", ""), parent=obj.parent.name if obj.parent else None,
        ancestors=ancestors, triangles=triangles, signed_volume_m3=signed_volume,
        boundary_edges=sum(n == 1 for n in edge_uses.values()),
        nonmanifold_edges=sum(n > 2 for n in edge_uses.values())))
low = Vector([min(p[i] for p in points) for i in range(3)])
high = Vector([max(p[i] for p in points) for i in range(3)])
centre, size = (low + high) / 2, high - low
if EXPORT:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.gltf(filepath=str(directory / "calibrated.glb"), export_format="GLB",
        use_selection=True, export_yup=False, export_apply=True)
    # OBJ uses full-size mm with the same X-long / Z-up frame.
    bpy.ops.wm.obj_export(filepath=str(directory / "calibrated-mm.obj"), export_selected_objects=True,
        forward_axis="Y", up_axis="Z", global_scale=1000)
trees = {name: BVHTree.FromPolygons(vertices, faces) for name, (vertices, faces) in geometry.items() if faces}
surface_connections = []


def measure_connection(obj, label, edges, target_roles, region, width_coverage=None, *, target_objects=None, records=None):
    candidates = objects if target_objects is None else target_objects
    targets = [o for o in candidates if o != obj and o.get("car_role") in target_roles and o.name in trees]
    samples, spans = [], []
    for a, b in edges:
        length = (b - a).length
        steps = max(2, math.ceil(length / .01))
        spans.append(dict(offset=len(samples), count=steps + 1, length_m=length))
        samples.extend(a.lerp(b, i / steps) for i in range(steps + 1))
    distances, witnesses = [], []
    for point in samples:
        hits = [(o.name, trees[o.name].find_nearest(point)) for o in targets]
        nearest = min(((name, hit) for name, hit in hits if hit[0] is not None), key=lambda item: item[1][3], default=None)
        distances.append(nearest[1][3] if nearest else None)
        if nearest:
            witnesses.append(dict(point_m=list(point), nearest_m=list(nearest[1][0]),
                target=nearest[0], distance_m=nearest[1][3]))
    destination = surface_connections if records is None else records
    destination.append(dict(source=obj.name, seam=label, region=region,
        target_roles=sorted(target_roles), targets=[o.name for o in targets], sample_count=len(samples),
        sampled_length_m=sum((b - a).length for a, b in edges), distances_m=distances,
        worst_samples=sorted(witnesses, key=lambda item: item['distance_m'], reverse=True)[:10],
        source_edge_count=len(edges),
        sampling=dict(schema="seam-sampling.v1", maximum_step_m=.01, edges=spans),
        method="source mesh seam edges sampled every <=10 mm to evaluated target triangles"))
    if width_coverage is not None:
        destination[-1].update(boundary_width_coverage=width_coverage,
            edge_selection="cross-car boundary tangents within lower/upper height band")


def open_boundary_edges(faces):
    uses = {}
    for face in faces:
        for a, b in zip(face, face[1:] + face[:1]):
            edge = tuple(sorted((a, b)))
            uses[edge] = uses.get(edge, 0) + 1
    return [edge for edge, count in uses.items() if count == 1]


def glazing_contact_edges(vertices, boundary, side):
    zlow, zhigh = min(v[2] for v in vertices), max(v[2] for v in vertices)
    cutoff = zlow + .2 * (zhigh - zlow) if side == "lower" else zhigh - .2 * (zhigh - zlow)
    def in_band(z):
        return z <= cutoff + 1e-8 if side == "lower" else z >= cutoff - 1e-8
    # Height alone includes sloping pillar edges, which do not seat on the cowl/header.
    edges = [(a, b) for a, b in boundary if in_band((a[2] + b[2]) / 2)
        and abs(b[1] - a[1]) > max(abs(b[0] - a[0]), abs(b[2] - a[2]))]
    band = [v[1] for v in vertices if in_band(v[2])]
    width = max(band) - min(band) if band else 0
    intervals = sorted((min(a[1], b[1]), max(a[1], b[1])) for a, b in edges)
    covered, end = 0, float('-inf')
    for low, high in intervals:
        covered += max(0, high - max(low, end))
        end = max(end, high)
    return edges, min(1, covered / width) if width > 1e-8 else 0


for obj in objects:
    if obj.type != "MESH":
        continue
    vertices = [obj.matrix_world @ v.co for v in obj.data.vertices]
    # Explicit seam groups identify real topology, not arbitrary declared contact points.
    for group in obj.vertex_groups:
        if not group.name.startswith("join_"):
            continue
        indices = {v.index for v in obj.data.vertices if any(g.group == group.index and g.weight > .5 for g in v.groups)}
        edges = [(vertices[e.vertices[0]], vertices[e.vertices[1]]) for e in obj.data.edges
                 if all(i in indices for i in e.vertices)]
        target_role = group.name[5:].split("__", 1)[0]
        measure_connection(obj, group.name, edges, [target_role], obj.get("car_connection_region", ""))
    role = obj.get("car_role")
    if role not in {"windscreen", "windshield", "rear_window"} or not vertices:
        continue
    edge_uses = {}
    for face in obj.data.polygons:
        for edge in face.edge_keys:
            key = tuple(sorted(edge))
            edge_uses[key] = edge_uses.get(key, 0) + 1
    boundary = [(vertices[a], vertices[b]) for (a, b), uses in edge_uses.items() if uses == 1]
    for side, target_roles in (("lower", ["body", "cowl"] if role != "rear_window" else ["body", "engine_deck"]),
                               ("upper", ["roof", "header"])):
        edges, coverage = glazing_contact_edges(vertices, boundary, side)
        measure_connection(obj, side, edges, target_roles, "cabin", coverage)

door_contacts, door_perimeters = [], []
for door in scene.objects:
    if door.get("car_role") != "door_assembly":
        continue
    members = [o for o in object_bounds if door.name in o["ancestors"]]
    member_names = {m["name"] for m in members}
    bounds_by_name = {o["name"]: o for o in object_bounds}
    moving_doors = {o.name for o in scene.objects if o.get("car_role") == "door_assembly"}
    fixed = [o for o in objects if not moving_doors.intersection(bounds_by_name[o.name]["ancestors"])]
    own_frames = [o for o in objects if o.name in member_names
        and o.get("car_role") in {"door_panel", "door_frame", "glass_gasket", "door_seal"}]
    for member in members:
        if member["role"] not in {"door_panel", "side_window"}:
            continue
        obj = scene.objects[member["name"]]
        raw = [obj.matrix_world @ v.co for v in obj.data.vertices]
        boundary = open_boundary_edges([list(p.vertices) for p in obj.data.polygons])
        edges = [(raw[a], raw[b]) for a, b in boundary]
        # All perimeter edges are measured, including edges without planner labels.
        targets = fixed + [o for o in own_frames if o.get("car_role") == "door_frame"] if member["role"] == "door_panel" else own_frames
        roles = {"body", "pillar", "cowl", "sill", "rocker", "roof", "body_transition", "door_frame"} if member["role"] == "door_panel" else {
            "door_panel", "door_frame", "glass_gasket", "door_seal"}
        measure_connection(obj, "perimeter", edges, roles, "doors", target_objects=targets, records=door_perimeters)
        door_perimeters[-1].update(door=door.name, role=member["role"],
            boundary_status="measured" if boundary else "unverified_closed_source_mesh")
    frame_vertices, frame_faces = [], []
    for member in members:
        if member["role"] not in {"door_panel", "door_frame"}:
            continue
        vertices, faces = geometry[member["name"]]
        offset = len(frame_vertices)
        frame_vertices.extend(vertices)
        frame_faces.extend(tuple(i + offset for i in f) for f in faces)
    tree = BVHTree.FromPolygons(frame_vertices, frame_faces) if frame_faces else None
    for window in (m for m in members if m["role"] == "side_window"):
        vertices, _ = geometry[window["name"]]
        zlow, zhigh = window["bounds_m"][0][2], window["bounds_m"][1][2]
        bottom = [v for v in vertices if v.z <= zlow + .2 * (zhigh - zlow) + 1e-6]
        anchors = [min(bottom, key=lambda v: v.x), max(bottom, key=lambda v: v.x)]
        distances = [tree.find_nearest(v)[3] for v in anchors] if tree else []
        door_contacts.append(dict(door=door.name, window=window["name"],
            members=[m["name"] for m in members], lower_edge_anchor_distances_m=distances,
            method="evaluated glass lower-front/rear vertices to own door/frame triangle surfaces"))
glass_visibility = []
for obj in objects:
    role = obj.get("car_role", "")
    if role not in {"side_window", "windscreen", "windshield", "quarter_window", "rear_window"}:
        continue
    vertices, faces = geometry.get(obj.name, ([], []))
    if not vertices:
        continue
    centre_glass = sum(vertices, Vector()) / len(vertices)
    if role in {"windscreen", "windshield"}:
        outward = Vector((-1, 0, 0))
    elif role == "rear_window":
        outward = Vector((1, 0, 0))
    else:
        outward = Vector((0, -1 if centre_glass.y < 0 else 1, 0))
    normals = [(vertices[f[1]] - vertices[f[0]]).cross(vertices[f[2]] - vertices[f[0]])
               for f in faces if len(f) >= 3]
    if normals:
        normal = max(normals, key=lambda n: n.length_squared)
        if normal.length > 1e-8:
            normal.normalize()
            outward = normal if normal.dot(outward) >= 0 else -normal
    stride = max(1, len(vertices) // 16)
    samples = [centre_glass] + [centre_glass.lerp(v, .5) for v in vertices[::stride][:16]]
    hits, clear = [], []
    for point in samples:
        hit, _, _, _, hit_obj, _ = scene.ray_cast(depsgraph, point + outward * 3, -outward, distance=3.05)
        hits.append(hit_obj.name if hit else None)
        own_frame = bool(hit and obj.parent and hit_obj.parent == obj.parent and (
            hit_obj.get("car_role") == "door_frame" or any(word in hit_obj.name.lower()
            for word in ("divider", "frame", "gasket", "seal", "rail"))))
        clear.append(bool(hit and (hit_obj.name == obj.name or own_frame)))
    glass_visibility.append(dict(window=obj.name, samples=len(samples),
        visible_fraction=sum(clear) / len(samples), first_hits=hits,
        method="outward glass-normal rays; own assembly frame/dividers permitted", ray_direction=list(outward)))
metadata = {"bounds_m": [list(low), list(high)], "dimensions_mm": list(size * 1000),
    "objects": object_bounds, "blender_version": bpy.app.version_string,
    "triangles": sum(o["triangles"] for o in object_bounds), "door_window_contacts": door_contacts,
    "glass_visibility": glass_visibility, "surface_connections": surface_connections,
    "door_perimeter_contacts": door_perimeters,
    "inward_meshes_corrected": normalized,
    "measurement": "evaluated world-space vertices; mirrors and trim included"}
(directory / "scene-metrics.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def metadata_json(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict"):
        return metadata_json(value.to_dict())
    if hasattr(value, "to_list"):
        return metadata_json(value.to_list())
    if isinstance(value, dict):
        return {key: metadata_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [metadata_json(item) for item in value]
    raise TypeError("Unsupported scene metadata type: " + type(value).__name__)


def property_json(obj, key, default):
    value = obj.get(key)
    if value is None:
        return default
    return metadata_json(json.loads(value) if isinstance(value, str) else value)


semantic_nodes = []
for obj in scene.objects:
    if obj.type not in {"EMPTY", "MESH", "CURVE", "SURFACE", "FONT"} or obj.hide_render:
        continue
    materials = []
    for slot in obj.material_slots:
        material = slot.material
        if material is None:
            materials.append(dict(name="unassigned", rgba=[.5, .5, .5, 1], transmission=0, roughness=.4))
            continue
        bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if material.use_nodes else None
        materials.append(dict(name=material.name,
            rgba=list(bsdf.inputs["Base Color"].default_value) if bsdf else list(material.diffuse_color),
            transmission=float(bsdf.inputs["Transmission Weight"].default_value) if bsdf else 0,
            roughness=float(bsdf.inputs["Roughness"].default_value) if bsdf else float(material.roughness)))
    local = obj.parent.matrix_world.inverted_safe() @ obj.matrix_world if obj.parent else obj.matrix_world
    semantic_nodes.append(dict(id=obj.name, role=obj.get("car_role", "unassigned"),
        parent=obj.parent.name if obj.parent else None,
        local_transform=[list(row) for row in local], world_transform=[list(row) for row in obj.matrix_world],
        materials=materials, geometry=semantic_geometry.get(obj.name),
        ports=property_json(obj, "car_ports", []), joint=property_json(obj, "car_joint", None),
        boundaries={g.name: dict(frame='object', source='original-control-mesh',
            points_m=[list(v.co) for v in obj.data.vertices if any(
                item.group == g.index and item.weight > .5 for item in v.groups)])
            for g in obj.vertex_groups if g.name.startswith("join_")} if obj.type == "MESH" else {}))
semantic = dict(schema="car2lego3d.semantic-scene.v1", units="metres", axes="X front-to-rear, Y left-to-right, Z up",
    provenance=globals().get("SCENE_PROVENANCE", {}), nodes=semantic_nodes,
    bounds_m=[list(low), list(high)], physical_verified=False)
(directory / "semantic-scene.json").write_text(json.dumps(semantic, indent=2), encoding="utf-8")
scene.render.engine = "CYCLES"
scene.cycles.samples = 12
scene.cycles.use_denoising = True
try:
    preferences = bpy.context.preferences.addons["cycles"].preferences
    preferences.compute_device_type = "CUDA"
    preferences.get_devices()
    for device in preferences.devices:
        device.use = device.type == "CUDA"
    if any(device.use for device in preferences.devices):
        scene.cycles.device = "GPU"
except (TypeError, RuntimeError):
    pass
scene.render.resolution_x = 1000
scene.render.resolution_y = 700
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.film_transparent = True
scene.view_settings.view_transform = "AgX"


def configure_inspection_lighting(scene, centre):
    # Broad softboxes otherwise obscure a shallow windscreen with a white reflection.
    for obj in list(scene.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (.7, .7, .7, 1)
    background.inputs["Strength"].default_value = .04
    lights = []
    for name, position, energy, scale in [("Key", (-2,-4,6), 2200, 5),
                                         ("Fill", (2,4,4), 1800, 5),
                                         ("Rim", (5,-2,5), 2400, 4)]:
        bpy.ops.object.light_add(type="AREA", location=centre + Vector(position))
        light = bpy.context.object
        light.name = name
        light.data.energy = energy
        light.data.shape = "DISK"
        light.data.size = scale
        light.visible_glossy = False
        light.rotation_euler = (centre - light.location).to_track_quat("-Z", "Y").to_euler()
        lights.append(dict(name=name, position_m=list(light.location), energy_w=energy,
                           size_m=scale, visible_glossy=light.visible_glossy))
    return dict(schema="blender-inspection-lighting.v1", world_strength=.04, lights=lights,
        purpose="inspection of glazing, interior and panel boundaries",
        material_shaders_modified=False, optical_measurement=False)


lighting = configure_inspection_lighting(scene, centre)
(directory / "render-rig.json").write_text(json.dumps(lighting, indent=2), encoding="utf-8")
bpy.ops.object.camera_add()
camera = bpy.context.object
scene.camera = camera
camera.data.type = "ORTHO"
directions = {"front": (-1,0,0), "back": (1,0,0), "left": (0,-1,0), "right": (0,1,0),
              "three-quarter": (-1,-1,.55), "rear-quarter": (1,1,.55)}
for name, direction in directions.items():
    camera.location = centre + Vector(direction).normalized() * 12
    camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
    rotation = camera.rotation_euler.to_matrix().transposed()
    projected = [rotation @ (p - centre) for p in points]
    width = max(p.x for p in projected) - min(p.x for p in projected)
    height = max(p.y for p in projected) - min(p.y for p in projected)
    camera.data.ortho_scale = max(width, height * 1000/700) * 1.15
    scene.render.filepath = str(directory / f"render-{name}.png")
    bpy.ops.render.render(write_still=True)
if EXPORT:
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / "model.blend"))
print("Export/render complete", list(size))
