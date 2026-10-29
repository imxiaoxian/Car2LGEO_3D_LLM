"""Read actual declared seams and nearby supports from a saved design scene."""
import argparse
import json
from pathlib import Path

from car2lego3d.blender_mcp import BlenderSession
from car2lego3d.common import digest, write_json


INSPECT = '''import bpy, json, math
from mathutils.bvhtree import BVHTree
bpy.context.view_layer.update()
deps = bpy.context.evaluated_depsgraph_get()
roles = {'body', 'front_fascia', 'rear_fascia', 'bumper', 'front_fender', 'rear_fender',
    'cowl', 'a_pillar', 'header', 'engine_deck', 'cabin_buttress'}
supports = {}
for obj in bpy.context.scene.objects:
    if obj.type != 'MESH' or obj.get('car_role') not in roles:
        continue
    evaluated = obj.evaluated_get(deps)
    mesh = evaluated.to_mesh()
    if mesh.polygons:
        supports[obj.name] = (obj.get('car_role'), BVHTree.FromPolygons(
            [obj.matrix_world @ v.co for v in mesh.vertices], [list(p.vertices) for p in mesh.polygons]))
    evaluated.to_mesh_clear()
rows = []
for name in selected_names:
    obj = bpy.data.objects[name]
    uses = {}
    for face in obj.data.polygons:
        for edge in face.edge_keys:
            key = tuple(sorted(edge))
            uses[key] = uses.get(key, 0) + 1
    groups = []
    selections = [(group.name, {v.index for v in obj.data.vertices
        if any(g.group == group.index and g.weight > .5 for g in v.groups)})
        for group in obj.vertex_groups if group.name.startswith('join_')]
    selections.append(('__all_mesh_edges__', {v.index for v in obj.data.vertices}))
    for group_name, ids in selections:
        edges = []
        for edge in obj.data.edges:
            if not all(i in ids for i in edge.vertices):
                continue
            a, b = [obj.matrix_world @ obj.data.vertices[i].co for i in edge.vertices]
            steps = max(2, math.ceil((b-a).length/.01))
            samples = [a.lerp(b, i/steps) for i in range(steps+1)]
            choices = []
            for target, (role, tree) in supports.items():
                if target == name:
                    continue
                distances = [tree.find_nearest(p)[3] for p in samples]
                if all(d is not None for d in distances):
                    choices.append(dict(target=target, role=role, max_gap_m=max(distances)))
            edges.append(dict(vertices=list(edge.vertices), endpoints_m=[list(a),list(b)],
                raw_boundary=uses.get(tuple(sorted(edge.vertices))) == 1,
                length_m=(b-a).length, candidates=sorted(choices,key=lambda r:r['max_gap_m'])[:4]))
        groups.append(dict(name=group_name, edges=edges))
    rows.append(dict(name=name, role=obj.get('car_role'), groups=groups))
print('CAR2LEGO_SEAMS=' + json.dumps(rows))
'''


def run(source, output, objects):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new diagnostic directory")
    output.mkdir(parents=True, exist_ok=True)
    before = digest(source)
    with BlenderSession(output) as blender:
        blender.execute("import bpy\nbpy.ops.wm.open_mainfile(filepath=" + repr(str(source)) + ")", "load")
        result = blender.execute("selected_names=" + repr(objects) + "\n" + INSPECT, "inspect")
    text = "\n".join(row["text"] for row in result["result"]["content"] if row["type"] == "text")
    rows, _ = json.JSONDecoder().raw_decode(text.split("CAR2LEGO_SEAMS=", 1)[1])
    report = dict(schema="design-seam-diagnosis.v1", source=str(source), source_sha256=before,
        source_unchanged=digest(source) == before, objects=rows,
        digital_assembly="unverified", appearance="unverified", scene_modified=False)
    write_json(output / "diagnosis.json", report)
    print(json.dumps(dict(source_unchanged=report["source_unchanged"],
        objects=[dict(name=row["name"], groups=len(row["groups"]), edges=sum(len(g["edges"]) for g in row["groups"]))
            for row in rows])))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--object", dest="objects", action="append", required=True)
    run(**vars(parser.parse_args()))
