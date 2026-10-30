"""Diagnostic correction of inward-facing open glazing before inward Solidify."""
import bpy


for name in ('QV_windscreen', 'QV_rear_window'):
    obj = bpy.data.objects[name]
    if not any(m.type == 'SOLIDIFY' and m.offset < 0 for m in obj.modifiers):
        raise ValueError('Expected an open finite-thickness design pane')
    normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
    normals = [normal_matrix @ p.normal for p in obj.data.polygons]
    if not normals or not all(n.z < -.1 for n in normals):
        raise ValueError('Glazing orientation needs diagnosis rather than an unconditional flip')
    for face in obj.data.polygons:
        face.flip()
    obj.data.update()
    obj['glazing_orientation_repair'] = 'Open-pane normals face exterior; thickness grows into cabin'
bpy.context.view_layer.update()
