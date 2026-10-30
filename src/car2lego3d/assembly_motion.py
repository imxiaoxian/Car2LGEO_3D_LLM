"""Sample parent-local articulation without modifying the saved assembly."""
from copy import deepcopy
from itertools import product

import numpy as np
from scipy.spatial.transform import Rotation


def joint_range_issues(joint):
    if joint.get("purpose") != "rolling":
        return []
    if joint.get("type", "revolute") != "revolute":
        return ["rolling_requires_revolute_joint"]
    low, high = joint["limits"]
    return ["rolling_requires_360_degrees"] if high - low < 360 - 1e-8 else []


def posed_graph(graph, positions):
    nodes = {node["id"]: node for node in graph["instances"]}
    result = deepcopy(graph)
    resolved = {"root": np.eye(4)}
    visiting = set()

    def world(identity):
        if identity in resolved:
            return resolved[identity]
        if identity in visiting:
            raise ValueError("Cyclic articulated hierarchy")
        visiting.add(identity)
        node = nodes[identity]
        movement = np.eye(4)
        if identity in positions:
            joint = graph["joints"][identity]
            axis = np.asarray(joint["axis"], dtype=float)
            value = positions[identity]
            if joint.get("type", "revolute") == "prismatic":
                movement[:3, 3] = axis * value
            else:
                movement[:3, :3] = Rotation.from_rotvec(axis * np.deg2rad(value)).as_matrix()
                origin = np.asarray(joint.get("origin", [0, 0, 0]))
                movement[:3, 3] = origin - movement[:3, :3] @ origin
        resolved[identity] = world(node["parent"]) @ movement @ np.asarray(node["local"])
        visiting.remove(identity)
        return resolved[identity]

    for node in result["instances"]:
        node["world"] = world(node["id"]).tolist()
    return result


def evaluate_motion(graph, validate_pose, samples=9, *, checkpoint=None):
    if type(samples) is not int or not 9 <= samples <= 181:
        raise ValueError("Motion needs 9 to 181 samples")

    def inspect(positions):
        if checkpoint:
            checkpoint()
        measurement = validate_pose(posed_graph(graph, positions))
        collisions = measurement["collisions"]
        unknown = len(measurement["unknown"])
        invalid = measurement["invalid_transforms"]
        connectivity = measurement.get("gates", {}).get("connections", "unverified")
        return dict(positions=positions, collisions=collisions, unknown=unknown, invalid=invalid,
            connectivity=connectivity, components=measurement.get("components"),
            status="fail" if collisions or invalid or connectivity == "fail" else "unverified"
                if unknown or connectivity != "pass" else "pass")

    records = []
    for identity, joint in graph["joints"].items():
        values = np.linspace(*joint["limits"], samples)
        poses = {float(value): inspect({identity: float(value)}) for value in values}
        for _ in range(2):
            ordered = sorted(poses)
            extra = {(a + b) / 2 for a, b in zip(ordered, ordered[1:])
                     if poses[a]["collisions"] or poses[b]["collisions"]}
            for value in sorted(extra - poses.keys()):
                poses[value] = inspect({identity: value})
        records.append(dict(instance=identity, purpose=joint.get("purpose"),
            range_issues=joint_range_issues(joint), limits=joint["limits"],
            unit="LDU" if joint.get("type") == "prismatic" else "degrees",
            poses=[dict(value=value, **poses[value]) for value in sorted(poses)]))

    steering = [name for name, joint in graph["joints"].items() if joint.get("purpose") == "steering"]
    suspension = [name for name, joint in graph["joints"].items() if joint.get("purpose") == "suspension"]
    rolling = [name for name, joint in graph["joints"].items() if joint.get("purpose") == "rolling"]
    parents = {node["id"]: node["parent"] for node in graph["instances"]}

    def contains(ancestor, child):
        seen = set()
        while child in parents and child not in seen:
            seen.add(child)
            child = parents[child]
            if child == ancestor:
                return True
        return False

    pairs = list(product(steering, suspension))
    pairs.extend((carrier, wheel) for carrier, wheel in product(steering + suspension, rolling)
                 if contains(carrier, wheel) or contains(wheel, carrier))
    combined = []
    for a, b in pairs:
        # Include refinement points discovered in the individual collision sweeps.
        values_a = next(row["poses"] for row in records if row["instance"] == a)
        values_b = next(row["poses"] for row in records if row["instance"] == b)
        poses = [inspect({a: pa["value"], b: pb["value"]}) for pa, pb in product(values_a, values_b)]
        combined.append(dict(instances=[a, b], poses=poses))
    statuses = [pose["status"] for row in [*records, *combined] for pose in row["poses"]]
    known_purposes = {"door", "hood", "rear_cover", "steering", "suspension", "rolling", "lamp"}
    unknown_purposes = [name for name, joint in graph["joints"].items()
                        if joint.get("purpose") not in known_purposes]
    combined_status = ("unverified" if unknown_purposes else "not_applicable" if not combined else
        "fail" if any(pose["status"] == "fail" for row in combined for pose in row["poses"]) else
        "unverified" if any(pose["status"] == "unverified" for row in combined for pose in row["poses"]) else "pass")
    status = ("fail" if "fail" in statuses or any(row["range_issues"] for row in records) else "unverified"
        if not statuses or "unverified" in statuses or unknown_purposes else "pass")
    if checkpoint:
        checkpoint()
    return dict(schema="assembly-motion.v4", revision=graph["revision"], status=status, joints=records,
        combined_joint_states=combined_status, combined=combined, unknown_joint_purposes=unknown_purposes,
        scope="sampled_single_joints_steering_suspension_and_nested_rolling_pairs_with_connectivity",
        unsampled_multi_joint_combinations="unverified", physical="unverified")
