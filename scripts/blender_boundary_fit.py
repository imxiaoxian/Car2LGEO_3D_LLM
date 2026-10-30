"""Available to metric scene patches; never scales or fits native LEGO parts."""
import bpy
import bmesh
import json
from mathutils.bvhtree import BVHTree


def car2lego_fit_boundary(object_name, group_name, target_names, max_distance=.05, tolerance=.006, refinements=3, *, commit=True):
    if not 0 < tolerance <= max_distance <= .1 or not 0 <= refinements <= 3:
        raise ValueError('Boundary fitting needs a local, bounded repair')
    obj = bpy.data.objects[object_name]
    group = obj.vertex_groups[group_name]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points, faces = [], []
    for name in target_names:
        if name == object_name:
            raise ValueError('A boundary cannot seat on itself')
        target = bpy.data.objects[name].evaluated_get(depsgraph)
        mesh = target.to_mesh()
        offset = len(points)
        points.extend(target.matrix_world @ v.co for v in mesh.vertices)
        faces.extend(tuple(offset + i for i in p.vertices) for p in mesh.polygons)
        target.to_mesh_clear()
    if not faces:
        raise ValueError('Boundary targets lack evaluated geometry')
    tree = BVHTree.FromPolygons(points, faces)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        deform = bm.verts.layers.deform.active
        if deform is None:
            raise ValueError('Boundary has no vertex membership evidence')
        anchor = bm.verts.layers.float_vector.new('car2lego_boundary_original')
        for vertex in bm.verts:
            vertex[anchor] = obj.matrix_world @ vertex.co
        inverse = obj.matrix_world.inverted()
        original_count = len(bm.verts)
        history = []
        for attempt in range(refinements + 1):
            edges = [e for e in bm.edges if e.is_boundary
                and all(v[deform].get(group.index, 0) > .99 for v in e.verts)]
            if not edges:
                raise ValueError('Select actual open boundary edges, not interior seams')
            for vertex in {v for edge in edges for v in edge.verts}:
                point = obj.matrix_world @ vertex.co
                nearest, _, _, distance = tree.find_nearest(point)
                if nearest is None or distance > max_distance or (nearest - vertex[anchor]).length > max_distance:
                    raise ValueError('Boundary exceeds total local fitting envelope; source mesh preserved')
                vertex.co = inverse @ nearest
            gaps = []
            failing = []
            worst = None
            for edge in edges:
                a, b = [obj.matrix_world @ v.co for v in edge.verts]
                samples = [(a.lerp(b, t), tree.find_nearest(a.lerp(b, t))) for t in (.25, .5, .75)]
                distances = [hit[3] for _, hit in samples]
                gaps.extend(distances)
                for point, hit in samples:
                    if worst is None or hit[3] > worst['distance_m']:
                        worst = dict(point_m=list(point), target_m=list(hit[0]), distance_m=hit[3])
                if max(distances) > tolerance:
                    failing.append(edge)
            history.append(dict(refinement=attempt, edges=len(edges), failing_edges=len(failing), worst=worst))
            if max(gaps) <= tolerance:
                bm.verts.layers.float_vector.remove(anchor)
                if commit:
                    bm.to_mesh(obj.data)
                    obj.data.update()
                return dict(status='fitted', max_gap_m=max(gaps), refinements=attempt,
                    committed=commit, added_vertices=len(bm.verts) - original_count,
                    standard_part_scaling=False, history=history)
            if attempt < refinements:
                bmesh.ops.subdivide_edges(bm, edges=failing, cuts=1, use_grid_fill=False)
        raise ValueError('Boundary fit did not converge; source mesh preserved: ' + json.dumps(dict(
            object=object_name, group=group_name, targets=target_names, history=history)))
    finally:
        bm.free()
