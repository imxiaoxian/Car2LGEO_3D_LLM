"""Bounded native mounting proposals on disposable assembly copies."""
from collections import Counter
from copy import copy, deepcopy
from itertools import permutations, product

import numpy as np

from .assembly_catalog import compatible, shaft_match, validate
from .assembly_layout import wheel_layout
from .assembly_mounts import protected_changed
from .assembly_requirements import identity


def rotations():
    yield np.eye(3)
    for order in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            rotation = np.eye(3)[list(order)] * np.asarray(signs)[:, None]
            if np.linalg.det(rotation) > .99 and not np.array_equal(rotation, np.eye(3)):
                yield rotation


def placement_operations(graph, moving, delta):
    """Move the root and its parent-frame joint together, preserving inner joints."""
    nodes = {node["id"]: node for node in graph["instances"]}
    root = nodes[moving]
    parent = np.eye(4) if root["parent"] == "root" else np.asarray(nodes[root["parent"]]["world"])
    local_delta = np.linalg.inv(parent) @ delta @ parent
    operations = [dict(op="transform", instance=moving,
        matrix=(np.linalg.inv(parent) @ delta @ np.asarray(root["world"])).tolist())]
    if moving in graph["joints"]:
        joint = deepcopy(graph["joints"][moving])
        joint["axis"] = (local_delta[:3, :3] @ np.asarray(joint["axis"])).tolist()
        joint["origin"] = (local_delta @ [*joint.get("origin", [0, 0, 0]), 1])[:3].tolist()
        operations.append(joint)
    return operations


def check_summary(report):
    return dict(status=report["status"], components=len(report["components"]),
        collisions=len(report["collisions"]), unknown=len(report["unknown"]),
        invalid_transforms=report["invalid_transforms"],
        collision_limit_reached=report["collision_limit_reached"], gates=report["gates"])


def axial_offsets(a, b, samples):
    """Sample the seated shaft's finite insertion interval; native matching decides legality."""
    yield 0.
    _, pa, oa, da, _ = a
    _, pb, ob, db, _ = b
    if samples == 1 or pa.kind != "axle" or pb.kind != "axle":
        return
    la = pa.length - (2 if pa.semantic == "axle_guard" and pb.semantic != "pin_socket" else 0)
    lb = pb.length - (2 if pb.semantic == "axle_guard" and pa.semantic != "pin_socket" else 0)
    low, high = sorted((0., -np.sign(da @ db) * la))
    overlap = 3.75 if "pin_socket" in (pa.semantic, pb.semantic) and "pin" not in (pa.semantic, pb.semantic) else .25
    lower, upper = -lb + overlap - high, -overlap - low
    if min(la, lb) < overlap or lower > upper:
        return
    current = float((oa - ob) @ db)
    # Include both ends of the overlap interval. Caps and axle keys still use the
    # same matcher as the full check; a finite sample never proves infeasibility.
    offsets = np.linspace(lower, upper, samples - 1)
    for offset in sorted(offsets, key=lambda value: (abs(value - current), value)):
        shift = float(offset - current)
        if abs(shift) > 1e-7 and shaft_match((a[0], pa, oa + shift * db, da, a[4]), b):
            yield shift


def axial_trials(seated, moving, source, pa, target, pb, samples):
    graph = seated.assembly.graph()
    nodes = {node["id"]: node for node in graph["instances"]}

    def world_port(instance, port):
        matrix = np.asarray(nodes[instance]["world"])
        return (instance, port, matrix[:3, 3] + matrix[:3, :3] @ port.position,
                matrix[:3, :3] @ port.direction, matrix[:3, :3] @ port.forward)

    a, b = world_port(source[0], pa), world_port(target[0], pb)
    for offset in axial_offsets(a, b, samples):
        trial = seated
        if offset:
            trial = copy(seated)
            trial.assembly = deepcopy(seated.assembly)
            delta = np.eye(4)
            delta[:3, 3] = offset * b[3]
            trial.tool_edit(graph["revision"], placement_operations(graph, moving, delta))
        yield trial, dict(offset_ldu=offset, direction_world=b[3].tolist())


def search_mounts(tools, revision, moving, targets, limit=6, max_evaluations=64,
                  max_port_pairs=20000, source_ports=None, target_ports=None, max_axial_samples=9):
    graph = tools.assembly.graph()
    if revision != graph["revision"]:
        raise ValueError("Stale assembly revision")
    nodes = {node["id"]: node for node in graph["instances"]}
    if moving not in nodes:
        raise ValueError("Moving root must be an existing part or module")
    inside = lambda instance: instance == moving or instance.startswith(moving + "/")
    if any(target not in nodes or inside(target) for target in targets):
        raise ValueError("Targets must be existing parts/modules outside the moving subtree")
    sources = [node for node in nodes.values() if node["kind"] == "part" and inside(node["id"])]
    fixed = [node for node in nodes.values() if node["kind"] == "part" and not inside(node["id"])
        and any(node["id"] == root or node["id"].startswith(root + "/") for root in targets)]
    if not sources or not fixed:
        raise ValueError("Moving and target scopes must contain concrete parts")

    def ports(parts, selection):
        available = {(node["id"], port.id): port for node in parts
                     for port in tools.catalog.ports(node["part"])}
        if selection is None:
            return list(available.items())
        selected = [(row["instance"], row["port"]) for row in selection]
        if any(key not in available for key in selected):
            raise ValueError("Port filter must name observed ports within its source/target scope")
        return [(key, available[key]) for key in selected]

    def checkpoint():
        if tools.checkpoint is not None:
            tools.checkpoint()

    checkpoint()
    baseline = validate(graph, tools.catalog)
    before = tools.verification_inputs()
    bindings_hash = identity(tools.module_bindings)
    statistics = dict(port_pairs=0, compatible_pairs=0, evaluations=0, duplicate_poses=0, axial_alternatives=0)
    rejected, proposals, seen = Counter(), [], set()
    result = dict(schema="assembly-mount-search.v2", revision=revision, moving=moving, targets=targets,
        **before, bindings_sha256=bindings_hash, baseline=check_summary(baseline),
        limits=dict(proposals=limit, evaluations=max_evaluations, port_pairs=max_port_pairs,
                    axial_samples_per_pair=max_axial_samples),
        statistics=statistics, proposals=proposals, rejected=rejected,
        coverage=dict(rotations=24, orientation="current first, then proper axis rotations relative to current world frame",
            axial_seating="native snap first, then finite insertion-interval samples checked against native stops and keys",
            axial_samples_per_pair=max_axial_samples),
        stop_reason="exhausted", truncated=False, motion="unverified", appearance="unverified", physical="unverified",
        limitations="Existing parts and mounting ports only; no adapters or new structures synthesized. "
            "Installation proposals do not certify motion, vehicle completeness, appearance or physical assembly.")
    if baseline["unknown"]:
        result.update(status="unverified", stop_reason="missing_native_evidence",
                      unknown=baseline["unknown"][:8], unknown_omitted=max(0, len(baseline["unknown"])-8))
        return result
    source_options, target_options = ports(sources, source_ports), ports(fixed, target_ports)
    result["coverage"].update(source_ports=len(source_options), target_ports=len(target_options))
    moving_parts = {node["id"] for node in sources}
    origin = np.asarray(nodes[moving]["world"])[:3, 3]
    frozen = getattr(tools, "protected_state", None)
    stop = False
    for rotation in rotations():
        checkpoint()
        seed = copy(tools)
        seed.assembly = deepcopy(tools.assembly)
        delta = np.eye(4)
        delta[:3, :3], delta[:3, 3] = rotation, origin - rotation @ origin
        seed.tool_edit(revision, placement_operations(graph, moving, delta))
        for (source, pa) in source_options:
            if stop:
                break
            for (target, pb) in target_options:
                if statistics["port_pairs"] >= max_port_pairs:
                    result["stop_reason"], stop = "port_pair_budget", True
                    break
                statistics["port_pairs"] += 1
                if statistics["port_pairs"] % 128 == 0:
                    checkpoint()
                if not compatible(pa, pb):
                    continue
                statistics["compatible_pairs"] += 1
                trial = copy(seed)
                trial.assembly = deepcopy(seed.assembly)
                try:
                    trial.tool_snap(trial.assembly.revision, source[0], source[1], target[0], target[1], moving=moving)
                except (ValueError, StopIteration) as error:
                    # Control interrupts propagate even if they inherit ValueError.
                    from .job_runtime import AssemblyControlError
                    if isinstance(error, AssemblyControlError):
                        raise
                    rejected["seating_failed"] += 1
                    continue
                for positioned, seating in axial_trials(trial, moving, source, pa, target, pb, max_axial_samples):
                    candidate = positioned.assembly.graph()
                    moved = next(node for node in candidate["instances"] if node["id"] == moving)
                    pose = identity(dict(matrix=np.round(moved["world"], 7).tolist(),
                        joint=positioned.assembly.joints.get(moving)))
                    if pose in seen:
                        statistics["duplicate_poses"] += 1
                        continue
                    # Failed contacts cannot deduplicate other contacts at the same pose.
                    if statistics["evaluations"] >= max_evaluations:
                        result["stop_reason"], stop = "evaluation_budget", True
                        break
                    checkpoint()
                    statistics["evaluations"] += 1
                    statistics["axial_alternatives"] += bool(seating["offset_ldu"])
                    report = validate(candidate, positioned.catalog)
                    exact = next((contact for contact in report["connections"]
                        if (contact["a"], contact["port_a"], contact["b"], contact["port_b"]) in {
                            (*source, *target), (*target, *source)}), None)
                    reason = ("selected_contact_missing" if exact is None else
                        "collisions" if report["collisions"] else "unknown" if report["unknown"] else
                        "invalid_transforms" if report["invalid_transforms"] else None)
                    if reason is None and not any(moving_parts | {target[0]} <= set(component)
                                                 for component in report["components"]):
                        reason = "moving_subtree_disconnected"
                    if reason is None and frozen and protected_changed(candidate, tools.module_bindings, frozen):
                        reason = "protected_module_changed"
                    layout = wheel_layout(candidate, positioned.catalog, tools.vehicle_contract,
                                          tools.module_bindings, require_complete=False)
                    if reason is None and layout["applicable"] and layout["status"] != "pass":
                        reason = "wheel_layout_" + layout["status"]
                    if reason:
                        rejected[reason] += 1
                        continue
                    seen.add(pose)
                    operations = [dict(op="transform", instance=moving, matrix=moved["local"])]
                    if moving in positioned.assembly.joints:
                        operations.append(deepcopy(positioned.assembly.joints[moving]))
                    proposals.append(dict(id="mount-" + str(len(proposals)), installation="pass",
                        contact=exact, seating=seating, whole_graph=check_summary(report),
                        wheel_layout=dict(applicable=layout["applicable"], status=layout["status"],
                                          scope=layout.get("scope")),
                        protected_unchanged=True, build_arguments=dict(revision=revision, operations=operations,
                            strict=report["status"] == "pass"),
                        motion="unverified", appearance="unverified", physical="unverified"))
                    if len(proposals) >= limit:
                        result["stop_reason"], stop = "proposal_limit", True
                        break
                if stop:
                    break
        if stop:
            break
    checkpoint()
    if before != tools.verification_inputs() or bindings_hash != identity(tools.module_bindings):
        raise ValueError("Assembly or native evidence changed during mounting search; retry on current inputs")
    result.update(status="proposals" if proposals else "no_proposal_within_scope",
                  truncated=stop, rejected=dict(rejected))
    return result
