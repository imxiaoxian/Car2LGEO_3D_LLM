"""Weld two audited coincident deck endpoints without changing the surface."""
import bpy


def group_positions(obj):
    return {group.name: {tuple(v.co) for v in obj.data.vertices
        if any(g.group == group.index and g.weight > .5 for g in v.groups)}
        for group in obj.vertex_groups}


for side, sign in (("left", -1), ("right", 1)):
    obj = bpy.data.objects["QV_stationary_deck_return_" + side]
    if obj.type != "MESH" or obj.get("car_role") != "rear_fender":
        raise ValueError("Audited deck object changed: " + obj.name)
    collapsed = [edge for edge in obj.data.edges
        if (obj.data.vertices[edge.vertices[0]].co - obj.data.vertices[edge.vertices[1]].co).length <= 1e-8]
    if len(collapsed) != 1 or set(collapsed[0].vertices) != {386, 387}:
        raise ValueError("Audited collapsed edge changed: " + obj.name)
    a, b = (obj.data.vertices[i].co.copy() for i in (386, 387))
    expected = (4.0, sign * .56, .732)
    if a != b or any(abs(a[i] - expected[i]) > 1e-7 for i in range(3)):
        raise ValueError("Deck endpoints are not the audited coincident pair")
    before = {tuple(v.co) for v in obj.data.vertices}
    groups = group_positions(obj)
    counts = (len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons))
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    for face in obj.data.polygons:
        face.select = False
    for edge in obj.data.edges:
        edge.select = False
    for vertex in obj.data.vertices:
        vertex.select = vertex.index in {386, 387}
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.merge(type="CENTER")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    if (len(obj.data.vertices) != counts[0] - 1
            or before != {tuple(v.co) for v in obj.data.vertices}
            or groups != group_positions(obj)):
        raise ValueError("Weld changed the audited surface or contact groups")
    if any((obj.data.vertices[e.vertices[0]].co - obj.data.vertices[e.vertices[1]].co).length <= 1e-8
            for e in obj.data.edges):
        raise ValueError("Collapsed edge remains")
    obj["car_topology_audit"] = dict(reason="Two coincident endpoints formed one zero-length deck seam edge",
        merged_source_vertices=[386, 387], unique_positions_unchanged=True,
        contact_group_positions_unchanged=True, tolerance_changed=False,
        vertices_before=counts[0], vertices_after=len(obj.data.vertices))
    print("CAR2LEGO_DECK_WELD", obj.name, counts,
        (len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons)))
