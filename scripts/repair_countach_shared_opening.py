"""Diagnostic patch for the saved QV design mesh, not native LEGO geometry."""
import bpy
from mathutils import Vector


shell = bpy.data.objects['QV_connected_body_shell']
if shell.get('car_role') != 'body':
    raise ValueError('Expected the metric design body, never a native part')
bpy.context.view_layer.update()


def group_points(obj, name):
    index = obj.vertex_groups[name].index
    return [obj.matrix_world @ v.co for v in obj.data.vertices
            if any(g.group == index and g.weight > .5 for g in v.groups)]


def interpolate(points, axis, value):
    points = sorted(points, key=lambda p: p[axis])
    for a, b in zip(points, points[1:]):
        if value <= b[axis]:
            t = max(0, min(1, (value - a[axis]) / max(1e-9, b[axis] - a[axis])))
            return a.lerp(b, t)
    return points[-1].copy()


for side, sign in [('left', -1), ('right', 1)]:
    skin = bpy.data.objects['QV_DG_' + side + '_skin']
    sill = sorted(group_points(shell, 'join_door_panel__' + side + '_sill'), key=lambda p: p.x)
    front = min(group_points(bpy.data.objects['QV_A_pillar_' + side], 'join_door_frame'), key=lambda p: p.z)
    rear = min(group_points(bpy.data.objects['QV_B_pillar_' + side], 'join_door_frame'), key=lambda p: p.z)
    anchors = [(sill[0] + Vector((0, 0, .003)), front),
               (sill[-1] + Vector((0, 0, .003)), rear)]
    source = [skin.matrix_world @ v.co for v in skin.data.vertices]
    low, high = min(p.x for p in source), max(p.x for p in source)
    # Restore the complete ruled door surface from its original opening datums.
    inverse = skin.matrix_world.inverted()
    for vertex in skin.data.vertices:
        world = skin.matrix_world @ vertex.co
        u = max(0, min(1, (world.x - low) / (high - low)))
        bottom = interpolate(sill, 0, low + (high - low) * u) + Vector((0, 0, .003))
        top = front.lerp(rear, u)
        t = max(0, min(1, (world.z - bottom.z) / (top.z - bottom.z)))
        target = bottom.lerp(top, t)
        import math
        target.y += sign * .012 * math.sin(math.pi * t) * math.sin(math.pi * u)
        if (target - world).length > .08:
            raise ValueError('Door restoration exceeds the diagnosed local envelope')
        vertex.co = inverse @ target
    skin.data.update()

    inverse = shell.matrix_world.inverted()
    for bottom, top in anchors:
        x = bottom.x
        for vertex in shell.data.vertices:
            world = shell.matrix_world @ vertex.co
            distance = abs(world.x - x)
            if distance >= .04 or sign * world.y < .73 or world.z < bottom.z - .004:
                continue
            if world.z > top.z + .004:
                continue
            t = max(0, min(1, (world.z - bottom.z) / (top.z - bottom.z)))
            control = bottom.lerp(top, t)
            blend = 1 - (distance / .04) ** 2 * (3 - 2 * distance / .04)
            target = world.copy()
            target.y += (control.y - world.y) * blend
            if (target - world).length > .08:
                raise ValueError('Shared opening exceeds the diagnosed local envelope')
            vertex.co = inverse @ target
    skin['opening_boundary_method'] = 'Original sill and pillar controls shared with adjacent body returns'
shell.data.update()
bpy.context.view_layer.update()
