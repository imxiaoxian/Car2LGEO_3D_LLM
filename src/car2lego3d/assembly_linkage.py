"""Bounded closed-contact kinematics; no graph mutation or physical certification."""
import numpy as np
from scipy.optimize import least_squares

from .assembly_catalog import validate, world_ports
from .assembly_motion import joint_range_issues, posed_graph


def contact_key(edge):
    return tuple(sorted(((edge["a"], edge["port_a"]), (edge["b"], edge["port_b"]))))


def evaluate_linkage(graph, catalog, driver, passive, samples=9, max_evaluations=2000, *, rolling=None, checkpoint=None):
    if type(samples) is not int or not 9 <= samples <= 181:
        raise ValueError("Linkage needs 9 to 181 samples")
    if type(max_evaluations) is not int or not 1 <= max_evaluations <= 20000:
        raise ValueError("Linkage needs 1 to 20000 residual evaluations")
    selected = [driver, *passive]
    rolling = [] if rolling is None else list(rolling)
    if (len(rolling) > 4 or len(set([*selected, *rolling])) != len(selected) + len(rolling)
            or samples * samples * len(rolling) > 2000):
        raise ValueError("Select up to four distinct rolling joints with at most 2000 combined poses")
    if not 1 <= len(passive) <= 8 or len(set(selected)) != len(selected):
        raise ValueError("Select one driver and one to eight distinct passive joints")
    for identity in selected:
        joint = graph["joints"].get(identity)
        if not joint or joint.get("type", "revolute") != "revolute":
            raise ValueError("Linkage currently supports declared revolute joints only")
        low, high = joint["limits"]
        if not np.isfinite([low, high]).all() or not low <= 0 <= high or low == high:
            raise ValueError("Each selected joint needs finite nonzero limits including neutral")
    for identity in rolling:
        joint = graph["joints"].get(identity)
        if not joint or joint.get("purpose") != "rolling" or joint_range_issues(joint):
            raise ValueError("Rolling sweeps need declared revolute rolling joints spanning 360 degrees")

    def check():
        if checkpoint:
            checkpoint()

    def ports(pose):
        entries, unknown = world_ports([n for n in pose["instances"] if n["kind"] == "part"], catalog)
        return {(p[0], p[1].id): p for p in entries}, unknown

    check()
    neutral = posed_graph(graph, {})
    baseline = validate(neutral, catalog, collision_limit=10)
    original = {contact_key(edge) for edge in baseline["connections"]}
    initial, unknown = ports(neutral)
    report = dict(schema="assembly-linkage-motion.v1", revision=graph["revision"],
        driver=driver, passive=list(passive), status="unverified", poses=[],
        rolling=list(rolling), combined=[],
        neutral_status=baseline["status"], residual_evaluations=0,
        constraints="all_original_native_contacts_with_fixed_axial_insertion",
        rotation_residual_scale_ldu=20, residual_tolerance=1e-5,
        scope="sampled_revolute_closed_contact_kinematics_other_joints_neutral",
        intermediate_poses="unverified", force_transmission="unverified", physical="unverified")
    if baseline["status"] != "pass" or unknown or not original:
        report.update(status=baseline["status"] if baseline["status"] != "pass" else "unverified",
            issue="neutral_native_assembly_must_pass", neutral=baseline)
        return report

    # Preserve observed offsets, including native serialization roundoff, in part A's frame.
    initial_rotations = {n["id"]: np.asarray(n["world"])[:3, :3] for n in neutral["instances"]}
    constraints = []
    for a, b in sorted(original):
        pa, pb = initial[a], initial[b]
        frame = initial_rotations[a[0]].T
        offset = frame @ (pb[2] - pa[2])
        direction = frame @ pb[3]
        locked = pa[1].kind not in {"axle", "hinge"} or (pa[1].cross_type and pb[1].cross_type)
        forward = frame @ pb[4]
        constraints.append((a, b, offset, direction, locked, forward))
    report["contacts"] = [dict(a=a[0], port_a=a[1], b=b[0], port_b=b[1]) for a, b in sorted(original)]

    class Exhausted(Exception):
        pass

    def residual(positions):
        check()
        if report["residual_evaluations"] >= max_evaluations:
            raise Exhausted
        report["residual_evaluations"] += 1
        pose = posed_graph(graph, positions)
        observed, missing = ports(pose)
        if missing:
            raise ValueError("Native ports changed during linkage evaluation")
        values = []
        rotations = {n["id"]: np.asarray(n["world"])[:3, :3] for n in pose["instances"]}
        for a, b, offset, direction, locked, forward in constraints:
            pa, pb = observed[a], observed[b]
            frame = rotations[a[0]]
            values.extend(pb[2] - pa[2] - frame @ offset)
            values.extend(20 * (pb[3] - frame @ direction))
            if locked:
                values.extend(20 * (pb[4] - frame @ forward))
        return np.asarray(values)

    bounds = np.asarray([graph["joints"][name]["limits"] for name in passive]).T
    solved = {0.: np.zeros(len(passive))}
    driven_residual = 0.
    values = np.linspace(*graph["joints"][driver]["limits"], samples)
    try:
        for value in sorted(map(float, values), key=lambda v: (abs(v), v)):
            seed = solved[min(solved, key=lambda v: abs(v - value))]
            def objective(angles):
                return residual({driver: value, **dict(zip(passive, map(float, angles)))})
            driven_residual = max(driven_residual, float(np.max(np.abs(objective(np.zeros(len(passive)))))))
            result = least_squares(objective, seed, bounds=bounds, xtol=1e-10, ftol=1e-10,
                gtol=1e-10, max_nfev=max_evaluations)
            error = float(np.max(np.abs(result.fun)))
            rank = int(np.linalg.matrix_rank(result.jac, tol=1e-6))
            positions = {driver: value, **dict(zip(passive, map(float, result.x)))}
            check()
            measurement = validate(posed_graph(graph, positions), catalog, collision_limit=10)
            present = {contact_key(edge) for edge in measurement["connections"]}
            lost = original - present
            status = ("fail" if measurement["status"] == "fail" or lost else "unverified"
                if measurement["status"] != "pass" or not result.success or error > 1e-5
                or rank < len(passive) else "pass")
            report["poses"].append(dict(positions=positions, status=status,
                converged=bool(result.success), residual_max=error, passive_rank=rank,
                lost_contacts=[list(pair) for pair in sorted(lost)], check=measurement))
            if result.success and error <= 1e-5:
                solved[value] = result.x
    except Exhausted:
        report["issue"] = "residual_budget_exhausted"
    statuses = [pose["status"] for pose in report["poses"]]
    coupled = driven_residual > .25 and any(np.max(np.abs(angles)) > 1e-3 for angles in solved.values())
    report.update(driver_requires_passive_response=bool(coupled),
        status="fail" if "fail" in statuses else "unverified" if len(statuses) != samples
        or "unverified" in statuses or not coupled else "pass")
    if not coupled and "issue" not in report:
        report["issue"] = "driver_coupling_not_established"
    report["poses"].sort(key=lambda row: row["positions"][driver])
    if report["status"] == "pass":
        for identity in rolling:
            poses = []
            for steering_pose in report["poses"]:
                for value in np.linspace(*graph["joints"][identity]["limits"], samples):
                    check()
                    positions = {**steering_pose["positions"], identity: float(value)}
                    measurement = validate(posed_graph(graph, positions), catalog, collision_limit=10)
                    lost = original - {contact_key(edge) for edge in measurement["connections"]}
                    status = "fail" if lost or measurement["status"] == "fail" else measurement["status"]
                    poses.append(dict(positions=positions, status=status,
                        lost_contacts=[list(pair) for pair in sorted(lost)], check=measurement))
            report["combined"].append(dict(rolling=identity, poses=poses))
        combined_statuses = [p["status"] for row in report["combined"] for p in row["poses"]]
        if "fail" in combined_statuses:
            report["status"] = "fail"
        elif "unverified" in combined_statuses:
            report["status"] = "unverified"
    report["simultaneous_multiple_rolling_joints"] = "unverified"
    check()
    return report
