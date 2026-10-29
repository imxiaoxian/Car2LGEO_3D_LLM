"""Render immutable source modules for human role review, never motion acceptance."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def verify_scene(scene, graph):
    import numpy as np

    parts = {node["id"]: node for node in graph["instances"] if node["kind"] == "part"}
    frame = np.diag([.0004, -.0004, -.0004, 1])
    found = set()
    for name in scene.graph.nodes_geometry:
        identity, separator, _ = name.rpartition(":")
        if not separator or identity not in parts:
            raise ValueError("Unexpected GLB instance: " + name)
        matrix, _ = scene.graph[name]
        if not np.allclose(matrix, frame @ np.asarray(parts[identity]["world"]), atol=1e-8, rtol=1e-6):
            raise ValueError("GLB placement differs from audited assembly: " + identity)
        found.add(identity)
    if found != set(parts):
        raise ValueError("GLB does not contain every audited native part")
    return len(found)


def prepare(audit_path, baseline, modules):
    import trimesh
    from car2lego3d.assembly_graph import Assembly
    from car2lego3d.common import digest

    audit_path, baseline = Path(audit_path).resolve(), Path(baseline).resolve()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    state_path, glb = baseline / "state.json", baseline / "assembly.glb"
    if (not audit.get("baseline_import_matches_source")
            or digest(Path(audit["source"])) != audit["source_sha256"]
            or digest(state_path) != audit["baseline_state_sha256"]):
        raise ValueError("Source or audited baseline changed")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    graph = Assembly(state["text"], state["source_sha256"]).graph()
    scene = trimesh.load(glb, force="scene", process=False)
    count = verify_scene(scene, graph)
    indexed = {row["instance"]: row for row in audit["modules"]}
    selected = []
    for identity in modules:
        row = indexed[identity]
        selected.append({key: row[key] for key in ("instance", "section", "part_count",
            "part_instances", "candidate_axes", "boundary_kind_counts", "missing_connection_evidence")})
    if not selected or len(set(modules)) != len(modules):
        raise ValueError("Select distinct audited module instances")
    return dict(schema="baseline-articulation-presentation.v1", case=audit["case"],
        source=audit["source"], source_sha256=audit["source_sha256"],
        audit=str(audit_path), audit_sha256=digest(audit_path),
        baseline_state=str(state_path), baseline_state_sha256=digest(state_path),
        glb=str(glb), glb_sha256=digest(glb), verified_glb_instances=count,
        modules=selected, fresh_creation=False, appearance="unverified",
        articulation="unverified", motion="not_checked",
        limits="Colour highlights locate source modules; names, candidate axes and images do not prove free travel.")


def worker(config_path):
    import bpy
    from mathutils import Vector

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    output = Path(config["output"])
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=config["glb"])
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not objects:
        raise ValueError("No imported native geometry")
    points = [obj.matrix_world @ Vector(vertex) for obj in objects for vertex in obj.bound_box]
    low = Vector([min(point[i] for point in points) for i in range(3)])
    high = Vector([max(point[i] for point in points) for i in range(3)])
    center, size = (low + high) / 2, high - low
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 900, 600
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.7, .7, .7, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .5
    scene.view_settings.view_transform = "AgX"
    for index, direction in enumerate(((-1, -1, 2), (1, 1, 2))):
        light = bpy.data.lights.new("review-light-" + str(index), "AREA")
        light.energy, light.size = 400 * size.length ** 2, size.length * 1.5
        obj = bpy.data.objects.new(light.name, light)
        scene.collection.objects.link(obj)
        obj.location = center + Vector(direction) * size.length
        obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
    camera_data = bpy.data.cameras.new("review-camera")
    camera = bpy.data.objects.new("review-camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera, camera_data.type = camera, "ORTHO"
    camera_data.clip_start, camera_data.clip_end = .0001, 100
    original_materials = {obj.name: list(obj.data.materials) for obj in objects}

    def material(name, color):
        result = bpy.data.materials.new(name)
        result.use_nodes = True
        shader = result.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = color
        shader.inputs["Roughness"].default_value = .55
        return result

    context = material("context-grey", (.18, .2, .22, 1))
    highlight = material("selected-module", (1, .22, .025, 1))
    # glTF shares meshes; object-linked slots keep highlights instance-specific.
    def paint(selected):
        for obj in objects:
            chosen = any(obj.name.startswith(identity + ":") for identity in selected)
            for slot in obj.material_slots:
                slot.link = "OBJECT"
                slot.material = highlight if chosen else context

    images = []
    def shot(name, direction, identity=None):
        direction = Vector(direction).normalized()
        camera.location = center + direction * max(size.length * 3, 1)
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        bpy.context.view_layer.update()
        projected = [camera.matrix_world.inverted() @ point for point in points]
        width = max(p.x for p in projected) - min(p.x for p in projected)
        height = max(p.y for p in projected) - min(p.y for p in projected)
        camera_data.ortho_scale = max(width, height * 1.5) * 1.12
        scene.render.filepath = str(output / (name + ".png"))
        bpy.ops.render.render(write_still=True)
        images.append(dict(file=name + ".png", module=identity,
            camera=dict(position=list(camera.location), target=list(center),
                ortho_scale=camera_data.ortho_scale), role="unverified", motion="not_checked"))

    shot("source_oblique_a", (-1, -1, .8))
    shot("source_oblique_b", (1, 1, .8))
    for index, module in enumerate(config["modules"]):
        identities = module["part_instances"]
        imported = {identity for identity in identities
            if any(obj.name.startswith(identity + ":") for obj in objects)}
        if imported != set(identities):
            raise ValueError("Imported object names cannot uniquely resolve the selected module")
        paint(identities)
        for side, direction in (("a", (-1, -1, .8)), ("b", (1, 1, .8))):
            shot(f"module_{index:02d}_{side}", direction, module["instance"])
    (output / "rendered.json").write_text(json.dumps(dict(images=images,
        bounds_m=[list(low), list(high)], materials="orange selected module; grey context",
        geometry_modified=False, acceptance="unverified"), indent=2), encoding="utf-8")


def main():
    from car2lego3d.common import ROOT, digest, write_json
    from car2lego3d.gpu_lock import GPULock
    from car2lego3d.gpt_reference import configuration
    from dotenv import dotenv_values

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--module", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory")
    config = prepare(args.audit, args.baseline, args.module)
    args.output.mkdir(parents=True)
    config["output"] = str(args.output.resolve())
    config["renderer_sha256"] = digest(Path(__file__))
    config_path = args.output / "inputs.json"
    write_json(config_path, config)
    env = dotenv_values(ROOT / ".env")
    provider, _ = configuration()
    binary = env.get("CAR2LEGO_BLENDER") or str(provider /
        "backend/storage/tool_research/blender-4.5.14-windows-x64/blender.exe")
    command = [binary, "--background", "--factory-startup", "--python-exit-code", "1",
        "--python", str(Path(__file__).resolve()), "--", "--blender-worker", str(config_path.resolve())]
    with GPULock(purpose="baseline-role-review"), (args.output / "blender.log").open("wb") as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            check=True, timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    rendered = json.loads((args.output / "rendered.json").read_text(encoding="utf-8"))
    for item in rendered["images"]:
        item["sha256"] = digest(args.output / item["file"])
    for key in ("source", "audit", "baseline_state", "glb"):
        if digest(Path(config[key])) != config[key + "_sha256"]:
            raise ValueError("Review source changed during render: " + key)
    write_json(args.output / "presentation.json", dict(config, **rendered))
    print(json.dumps(dict(case=config["case"], images=len(rendered["images"]),
        articulation="unverified", motion="not_checked")), flush=True)


if __name__ == "__main__":
    if "--blender-worker" in sys.argv:
        worker(sys.argv[sys.argv.index("--blender-worker") + 1])
    else:
        main()
