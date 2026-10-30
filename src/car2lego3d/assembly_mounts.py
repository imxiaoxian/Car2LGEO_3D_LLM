"""Owned mechanism geometry and host-scoped installation into moving carriers."""
from copy import deepcopy

import numpy as np

from .assembly_requirements import descendants


def semantic_node(node):
    # Copy-on-write changes definition filenames, never occurrence geometry.
    return {k: v for k, v in node.items() if k != "section" and not (k == "part" and node["kind"] == "module")}


def owned_members(graph, bindings, name):
    members = descendants(graph, bindings[name]["roots"])
    for other, binding in bindings.items():
        if other == name:
            continue
        nested = [root for root in binding["roots"] if root in members and root not in bindings[name]["roots"]]
        if nested:
            members -= descendants(graph, nested)
    return members


def carrier_targets(graph, bindings, purpose):
    binding = bindings.get(purpose)
    if not binding:
        return []
    nodes = {n["id"]: n for n in graph["instances"]}
    members = descendants(graph, binding["roots"])
    return sorted(key for key, joint in graph["joints"].items()
        if key in members and nodes[key]["kind"] == "module" and joint.get("purpose") == purpose
        and joint["limits"][0] < joint["limits"][1]
        and any(part in descendants(graph, [key]) for part in binding["roles"].get("carrier", [])))


def wheel_mount_issues(graph, contract, bindings, name):
    if not name.startswith("wheel_") or name not in bindings:
        return []
    required = [p for p in ("steering", "suspension") if p in contract["modules"]
        and (p != "steering" or name.startswith("wheel_front_"))]
    roots = bindings[name]["roots"]
    return ["wheel_not_mounted_under_" + purpose for purpose in required
        if not all(any(root != target and root in descendants(graph, [target])
            for target in carrier_targets(graph, bindings, purpose)) for root in roots)]


def installation_targets(graph, contract, bindings, task):
    if task == "suspension":
        return carrier_targets(graph, bindings, "steering")
    if task.startswith("wheel_"):
        purpose = ("suspension" if "suspension" in contract["modules"] else
            "steering" if task.startswith("wheel_front_") else None)
        targets = carrier_targets(graph, bindings, purpose) if purpose else []
        if task.startswith("wheel_front_") and "steering" in contract["modules"]:
            steering = carrier_targets(graph, bindings, "steering")
            targets = [target for target in targets if any(target in descendants(graph, [s]) for s in steering)]
        return targets
    return []


def protection(graph, bindings, *, mounts=(), editable_roots=()):
    members = set().union(*(descendants(graph, b["roots"]) for b in bindings.values()))
    editable = descendants(graph, editable_roots) if editable_roots else set()
    required = {part for binding in bindings.values()
        for part in binding["roots"] + [p for values in binding["roles"].values() for p in values]}
    if editable.intersection(required):
        raise ValueError("Editable subtree contains completed module roles or roots")
    members -= editable
    return deepcopy(dict(instances={n["id"]: n for n in graph["instances"] if n["id"] in members},
        joints={k: v for k, v in graph["joints"].items() if k in members}, bindings=bindings,
        mounts=list(mounts)))


def protected_changed(graph, bindings, frozen):
    nodes = {n["id"]: n for n in graph["instances"]}
    old = frozen["instances"]
    if (any(key not in nodes or semantic_node(nodes[key]) != semantic_node(value) for key, value in old.items())
            or any(bindings.get(key) != value for key, value in frozen["bindings"].items())
            or {k: v for k, v in graph["joints"].items() if k in old} != frozen["joints"]):
        return True
    members = set().union(*(descendants(graph, b["roots"]) for b in frozen["bindings"].values()))
    added = members - old.keys()
    # Only independent module subtrees can be installed at host-declared carriers.
    for key in added:
        root = key
        while nodes[root]["parent"] in added:
            root = nodes[root]["parent"]
        if nodes[root]["kind"] != "module" or nodes[root]["parent"] not in frozen.get("mounts", []):
            return True
    return False


def mechanism_response(graph, contract, bindings, purpose, *, require_complete=False):
    """Check wheel response, not merely motion of an unrelated labelled carrier."""
    from .assembly_motion import posed_graph
    required = [name for name in contract["modules"] if name.startswith("wheel_")
        and (purpose != "steering" or name.startswith("wheel_front_"))]
    nodes = {n["id"]: n for n in graph["instances"]}
    targets = carrier_targets(graph, bindings, purpose)
    rows = []
    for name in required:
        binding = bindings.get(name)
        if not binding:
            rows.append(dict(module=name, status="fail" if require_complete else "pending", reason="wheel_not_installed"))
            continue
        parts = [i for role in ("tyre", "rim", "hub") for i in binding["roles"].get(role, [])]
        moving = next((key for key in targets if parts and set(parts) <= descendants(graph, [key])), None)
        if moving is None:
            rows.append(dict(module=name, status="fail", reason="wheel_not_in_shared_" + purpose + "_subtree"))
            continue
        # Steering must turn the wheel axis; spinning an axle is not steering.
        rolling_parts = {i for role in ("tyre", "rim") for i in binding["roles"].get(role, [])}
        rolling = next((key for key, j in graph["joints"].items()
            if j.get("purpose") == "rolling" and rolling_parts and rolling_parts <= descendants(graph, [key])
            and key in descendants(graph, binding["roots"]) and nodes[key]["kind"] == "module"), None)
        values = np.linspace(*graph["joints"][moving]["limits"], 9)
        frames = [{n["id"]: n for n in posed_graph(graph, {moving: float(value)})["instances"]} for value in values]
        if purpose == "steering" and rolling:
            axis = np.asarray(graph["joints"][rolling]["axis"])
            directions = [np.asarray(frame[nodes[rolling]["parent"]]["world"])[:3, :3] @ axis
                if nodes[rolling]["parent"] != "root" else axis for frame in frames]
            changed = max(np.linalg.norm(np.cross(directions[0], value)) for value in directions) > 1e-6
        elif purpose == "suspension":
            changed = all(max(np.linalg.norm(np.asarray(frame[part]["world"])[:3, 3]
                - np.asarray(frames[0][part]["world"])[:3, 3]) for frame in frames) > 1e-6 for part in parts)
        else:
            changed = False
        rows.append(dict(module=name, joint=moving, samples=len(values), status="pass" if changed else "fail",
            reason=None if changed else "wheel_does_not_respond_to_" + purpose))
    statuses = {row["status"] for row in rows}
    return dict(status="fail" if "fail" in statuses else "pending" if "pending" in statuses else "pass",
        wheels=rows, scope="wheel_response_only_geometry_and_connections_checked_separately")
