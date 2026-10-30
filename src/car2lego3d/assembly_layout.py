"""Neutral-pose vehicle stance measured from native tyre surfaces in LDraw/LDU."""
from itertools import combinations

import numpy as np

from .assembly_requirements import identity

VERSION = "vehicle-wheel-layout.v1"
# Two LDU (0.8 mm) allows native surface tessellation, not a plate-height error.
TOLERANCE_LDU = 2.0


def wheel_layout(graph, catalog, contract, bindings, *, require_complete=True):
    required = sorted(name for name in (contract or {}).get("modules", {}) if name.startswith("wheel_"))
    nodes = {node["id"]: node for node in graph["instances"]}
    rows, issues, missing = {}, [], []

    def issue(code, modules, status="fail", **measurements):
        issues.append(dict(code=code, modules=sorted(modules), status=status, **measurements))

    for name in required:
        binding = bindings.get(name)
        if not binding:
            missing.append(name)
            continue
        tyre_ids = binding.get("roles", {}).get("tyre", [])
        points, tyres = [], []
        if not tyre_ids or len(tyre_ids) != len(set(tyre_ids)):
            issue("wheel_geometry_unverified", [name], "unverified", reason="missing_or_duplicate_tyre_roles")
            continue
        try:
            for instance in tyre_ids:
                node = nodes[instance]
                if node["kind"] != "part":
                    raise ValueError("Tyre role is not a part")
                triangles, _ = catalog.library.triangles(node["part"])
                native = np.asarray(triangles, dtype=float).reshape(-1, 3)
                world = np.asarray(node["world"], dtype=float)
                if (not len(native) or not np.isfinite(native).all() or world.shape != (4, 4)
                        or not np.isfinite(world).all()):
                    raise ValueError("Missing finite native surface or world transform")
                transformed = native @ world[:3, :3].T + world[:3, 3]
                points.append(transformed)
                tyres.append(dict(instance=instance, part=node["part"], world=node["world"]))
            points = np.concatenate(points)
            low, high = points.min(axis=0), points.max(axis=0)
            if np.any(high - low <= 0):
                raise ValueError("Degenerate tyre surface")
            rows[name] = dict(tyres=tyres, bounds_ldu=[low.tolist(), high.tolist()],
                center_ldu=((low + high) / 2).tolist(), ground_y_ldu=float(high[1]))
        except (AttributeError, KeyError, OSError, TypeError, ValueError) as error:
            issue("wheel_geometry_unverified", [name], "unverified", reason=str(error))

    for a, b in combinations(rows, 2):
        delta = abs(rows[a]["ground_y_ldu"] - rows[b]["ground_y_ldu"])
        if delta > TOLERANCE_LDU:
            issue("wheel_ground_contact_mismatch", [a, b], delta_ldu=delta, delta_mm=delta * .4)

    chassis = None
    if rows and "chassis" in bindings:
        from .assembly_mounts import owned_members
        try:
            members = owned_members(graph, bindings, "chassis")
            surfaces = []
            for instance in sorted(members):
                node = nodes[instance]
                if node["kind"] != "part":
                    continue
                triangles, _ = catalog.library.triangles(node["part"])
                native = np.asarray(triangles, dtype=float).reshape(-1, 3)
                world = np.asarray(node["world"], dtype=float)
                if (not len(native) or not np.isfinite(native).all() or world.shape != (4, 4)
                        or not np.isfinite(world).all()):
                    raise ValueError("Missing finite chassis surface or transform")
                surfaces.append(native @ world[:3, :3].T + world[:3, 3])
            if not surfaces:
                raise ValueError("No native chassis surfaces")
            points = np.concatenate(surfaces)
            chassis = dict(bounds_ldu=[points.min(axis=0).tolist(), points.max(axis=0).tolist()],
                bottom_y_ldu=float(points[:, 1].max()))
            for name, row in rows.items():
                penetration = chassis["bottom_y_ldu"] - row["ground_y_ldu"]
                if penetration > TOLERANCE_LDU:
                    issue("wheel_ground_above_chassis", [name], support_module="chassis",
                        penetration_ldu=penetration, penetration_mm=penetration * .4)
        except (AttributeError, KeyError, OSError, TypeError, ValueError) as error:
            issue("chassis_ground_clearance_unverified", list(rows), "unverified", reason=str(error))

    axles = {}
    for axle in ("front", "rear"):
        left, right = (f"wheel_{axle}_{side}" for side in ("left", "right"))
        if left not in rows or right not in rows:
            continue
        a, b = rows[left], rows[right]
        delta = abs(a["center_ldu"][0] - b["center_ldu"][0])
        if delta > TOLERANCE_LDU:
            issue("wheel_axle_position_mismatch", [left, right], delta_ldu=delta, delta_mm=delta * .4)
        if a["bounds_ldu"][1][2] >= b["bounds_ldu"][0][2]:
            issue("wheel_lateral_order_or_overlap", [left, right])
        axles[axle] = dict(modules=[left, right],
            center_z_ldu=(a["center_ldu"][2] + b["center_ldu"][2]) / 2)
    if "front" in axles and "rear" in axles:
        delta = abs(axles["front"]["center_z_ldu"] - axles["rear"]["center_z_ldu"])
        if delta > TOLERANCE_LDU:
            issue("wheel_axle_centerline_mismatch", required, delta_ldu=delta, delta_mm=delta * .4)
    # Compare installed front/rear tyres even before the second side is present.
    for front in (name for name in rows if name.startswith("wheel_front_")):
        for rear in (name for name in rows if name.startswith("wheel_rear_")):
            clearance = rows[rear]["bounds_ldu"][0][0] - rows[front]["bounds_ldu"][1][0]
            if clearance <= 0:
                issue("wheel_axle_order_or_overlap", [front, rear], clearance_ldu=clearance)

    statuses = {item["status"] for item in issues}
    status = ("fail" if "fail" in statuses else "unverified" if "unverified" in statuses
        else "pending" if missing and require_complete else "pass")
    return dict(schema=VERSION, revision=graph["revision"], graph_sha256=identity(graph),
        bindings_sha256=identity(bindings), applicable=bool(required), status=status,
        complete=not missing and len(rows) == len(required),
        scope="all_required_wheels" if require_complete else "installed_wheels_only",
        coordinates="LDraw/LDU: X front to rear, Y down, Z left to right; 1 LDU = 0.4 mm",
        tolerance_ldu=TOLERANCE_LDU, required=required, missing=missing, wheels=rows, issues=issues,
        chassis=chassis,
        limitations="Neutral-pose surface layout only; connector, motion and semantic checks are separate.")
