"""Exact spatial datums from completed Blender phases for subsequent patches."""
import numpy as np

from .semantic import validate_scene, world_vertices


SCHEMA = "car2lego3d.phase-geometry.v1"
INTERFACE_ROLES = {
    "body": {"chassis", "axle_module", "running_gear", "suspension_carrier"},
    "cabin": {"body", "cowl", "header", "front_fender", "rear_fender", "engine_deck"},
    "doors-glass": {"body", "roof", "cowl", "header", "pillar", "a_pillar", "b_pillar",
                    "cabin_buttress", "door_assembly", "door_panel", "door_frame"},
    "front": {"body", "roof", "cowl", "header", "windscreen", "front_fender", "front_fascia",
              "pillar", "a_pillar", "door_assembly", "door_panel", "door_frame"},
    "rear": {"body", "roof", "header", "rear_window", "quarter_window", "rear_fender",
             "rear_fascia", "engine_deck", "cabin_buttress", "door_assembly", "door_panel"},
}


def transform_points(points, matrix):
    points = np.asarray(points, dtype=float).reshape((-1, 3))
    return (points @ matrix[:3, :3].T + matrix[:3, 3]).tolist()


def transform_axis(axis, matrix):
    direction = matrix[:3, :3] @ np.asarray(axis, dtype=float)
    return (direction / np.linalg.norm(direction)).tolist()


def phase_geometry(scene, phase):
    """Retain all object identities; expand contact datums relevant to this phase.

    This is a navigation context, not replacement or decimation of source meshes.
    Every included boundary retains all original control points in world metres.
    """
    if scene is None:
        return dict(schema=SCHEMA, units="metres", objects=[], interfaces=[])
    named = validate_scene(scene)
    bounds = {}
    for node in named.values():
        if node.get("geometry"):
            vertices = world_vertices(node)
            bounds[node["id"]] = np.array([vertices.min(axis=0), vertices.max(axis=0)])
    # Empty module parents need the envelope of their actual descendants too.
    for node in named.values():
        if node.get("geometry"):
            box, parent = bounds[node["id"]], node.get("parent")
            while parent:
                if parent in bounds:
                    bounds[parent] = np.array([np.minimum(bounds[parent][0], box[0]),
                                               np.maximum(bounds[parent][1], box[1])])
                else:
                    bounds[parent] = box.copy()
                parent = named[parent].get("parent")
    objects, interfaces = [], []
    for node in named.values():
        row = {key: node.get(key) for key in ("id", "role", "parent")}
        if node["id"] in bounds:
            row["world_bounds_m"] = bounds[node["id"]].tolist()
        if node.get("geometry"):
            row["mesh"] = dict(vertices=len(node["geometry"]["vertices_m"]),
                               triangles=len(node["geometry"]["triangles"]))
        matrix = np.asarray(node["world_transform"], dtype=float)
        if node.get("joint"):
            joint = node["joint"]
            parent_matrix = np.asarray(named[node["parent"]]["world_transform"]) if node.get("parent") else np.eye(4)
            row["joint_world"] = {**joint, "frame": "world",
                "origin_m": transform_points([joint["origin_m"]], parent_matrix)[0],
                "axis": transform_axis(joint["axis"], parent_matrix)}
            if joint["type"] == "prismatic":
                factor = np.linalg.norm(parent_matrix[:3, :3] @ np.asarray(joint["axis"]))
                row["joint_world"]["limits"] = [value * float(factor) for value in joint["limits"]]
            row["joint_parent"] = joint
        boundaries = node.get("boundaries", {})
        row["boundary_groups"] = {key: len(value["points_m"]) for key, value in boundaries.items()}
        objects.append(row)
        selected = phase == "details" or node["role"] in INTERFACE_ROLES.get(phase, set())
        if selected and (boundaries or node.get("ports")):
            interfaces.append(dict(id=node["id"], frame="world",
                boundaries={key: dict(source=value.get("source"),
                    points_m=transform_points(value["points_m"], matrix)) for key, value in boundaries.items()},
                ports=[{**port, "position_m": transform_points([port["position_m"]], matrix)[0],
                    "axis": transform_axis(port["axis"], matrix)} for port in node.get("ports", [])]))
    return dict(schema=SCHEMA, units="metres", phase=phase, objects=objects, interfaces=interfaces,
        geometry_omitted_from_prompt=True, source_geometry_preserved=True,
        instructions="Bounds use all evaluated mesh vertices, including parent transforms. Joint datums above "
            "use world coordinates; car_joint written to Blender must still use the PARENT frame. "
            "Reuse existing contact boundaries instead of estimating adjoining coordinates. Boundary points "
            "are original control-mesh vertices, not an ordered curve. For exact topology or unexpanded groups, "
            "read bpy.data.objects[id].data.vertices/edges/vertex_groups in your patch; use matrix_world. "
            "For evaluated surfaces use evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh() "
            "and to_mesh_clear(). No existing source geometry has been removed or simplified.")
