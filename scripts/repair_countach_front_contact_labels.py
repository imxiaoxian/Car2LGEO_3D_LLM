"""Correct audited design-mesh mounting labels without moving geometry."""
import bpy
from mathutils.bvhtree import BVHTree


bpy.context.view_layer.update()
deps = bpy.context.evaluated_depsgraph_get()
targets = {}
for name in ("QV_fixed_body_shell", "FRONT_QV_front_lower_lip"):
    obj = bpy.data.objects[name]
    evaluated = obj.evaluated_get(deps)
    mesh = evaluated.to_mesh()
    targets[name] = BVHTree.FromPolygons([obj.matrix_world @ v.co for v in mesh.vertices],
        [list(p.vertices) for p in mesh.polygons])
    evaluated.to_mesh_clear()

repairs = [
    ("FRONT_QV_front_lower_lip", "bumper", "join_body__bumper_return", [
        ("body", "mount_left", (4, 7), "QV_fixed_body_shell"),
        ("body", "mount_right", (5, 6), "QV_fixed_body_shell")]),
    ("FRONT_QV_front_fascia_corner_left", "front_fascia", "join_body__fascia_corner_return", [
        ("body", "mount_outer", (4, 7), "QV_fixed_body_shell"),
        ("bumper", "mount_lower", (4, 5), "FRONT_QV_front_lower_lip")]),
    ("FRONT_QV_front_fascia_corner_right", "front_fascia", "join_body__fascia_corner_return", [
        ("body", "mount_outer", (5, 6), "QV_fixed_body_shell"),
        ("bumper", "mount_lower", (4, 5), "FRONT_QV_front_lower_lip")]),
]

for name, role, previous, contacts in repairs:
    obj = bpy.data.objects[name]
    if obj.get("car_role") != role or len(obj.data.vertices) != 8:
        raise ValueError("The audited closed front component changed: " + name)
    old = obj.vertex_groups[previous]
    ids = {v.index for v in obj.data.vertices if any(g.group == old.index and g.weight > .5 for g in v.groups)}
    if ids != {0, 1, 2, 3}:
        raise ValueError("Original front-face label differs from the audited source: " + name)
    before = [tuple(v.co) for v in obj.data.vertices]
    evidence = []
    for target_role, label, indices, target_name in contacts:
        if tuple(sorted(indices)) not in {tuple(sorted(e.vertices)) for e in obj.data.edges}:
            raise ValueError("Mounting edge is not present in the source topology")
        a, b = [obj.matrix_world @ obj.data.vertices[i].co for i in indices]
        # This patch changes labels only. Actual placement must already meet the unchanged tolerance.
        gaps = [targets[target_name].find_nearest(a.lerp(b, i / 32))[3] for i in range(33)]
        if any(d is None or d > .012 for d in gaps):
            raise ValueError("Mounting interface needs geometry repair: " + name)
        evidence.append(dict(role=target_role, group="join_" + target_role + "__" + label,
            vertices=list(indices), target=target_name, maximum_gap_m=max(gaps)))
    obj.vertex_groups.remove(old)
    for item in evidence:
        obj.vertex_groups.new(name=item["group"]).add(item["vertices"], 1, "REPLACE")
    obj["car_connection_region"] = "front"
    obj["car_connection_audit"] = dict(reason="Closed front-face exterior perimeter was mislabeled as a body interface",
        replaced_group=previous, mounts=evidence, geometry_changed=False)
    if before != [tuple(v.co) for v in obj.data.vertices]:
        raise ValueError("Contact label repair changed geometry")
    print("CAR2LEGO_CONTACT_RELABEL", name, evidence)
