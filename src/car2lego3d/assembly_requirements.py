"""Host-owned vehicle inventory, bound to actual parts rather than planner labels."""
from hashlib import sha256
import json

VERSION = "vehicle-modules.v1"


def identity(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def vehicle_contract(request, semantic_sha256, *, repairs=(), source_index=None):
    from .baseline_detail import detail_standard, scope_repairs
    standard = detail_standard(request)
    repairs, excluded_repairs = scope_repairs(repairs, standard)
    modules = {}

    def add(name, roles, purpose=None, minimum=1):
        modules[name] = dict(roles=roles, joint_purpose=purpose, minimum_joints=minimum if purpose else 0)

    add("chassis", ["structure"])
    for axle in ("front", "rear"):
        for side in ("left", "right"):
            add(f"wheel_{axle}_{side}", ["tyre", "rim", "hub"], "rolling")
    if standard is None or standard["steering_required"]:
        add("steering", ["rack", "carrier"], "steering")
    if standard is None or standard["suspension_required"]:
        add("suspension", ["spring", "carrier"], "suspension", 4)
    add("cabin", ["seats", "dashboard", "steering_wheel"])
    from .vehicle_delivery import core_case
    case = standard["case"] if standard else core_case(request.get("brand", ""), request.get("model", ""))
    source_doors = sum(n.get("role") == "door_assembly" for n in (source_index or {}).get("nodes", {}).values())
    doors = ("front_left", "front_right", "rear_left", "rear_right") if case == "lotus-for-me" or source_doors >= 4 else ("left", "right")
    for side in doors:
        roles = ["panel", "glass", "hinge"] if standard is None else ["panel", "hinge"]
        add("door_" + side, roles, "door")
    for end in ("front", "rear"):
        add(end, ["body"])
        add(end + "_lights", ["housing", "reflector", "lens"] if standard is None else ["lamp"])
    if case in {"countach", "f40"}:
        add("headlamp_mechanism", ["hinge", "actuator"] if standard is None else ["hinge"], "lamp")
    if standard is None or standard["cover_scope"] == "required":
        add("hood", ["cover", "hinge"], "hood")
        add("rear_cover", ["cover", "hinge"], "rear_cover")
    add("signature", ["feature"])
    return dict(schema=VERSION, vehicle={k: request.get(k) for k in ("brand", "model", "year")},
        semantic_sha256=semantic_sha256, modules=modules,
        nominal_scale=12, scale_enforced=False, role_labels_are_evidence=False,
        independent_role_review_required=True, reference_repairs=list(repairs),
        detail_standard=standard, excluded_reference_repairs=excluded_repairs,
        scope_review_required=["cover_articulation"] if standard and standard["cover_scope"] == "baseline_review_required" else [])


def descendants(graph, roots):
    nodes = {n["id"]: n for n in graph["instances"]}
    selected = set(roots)
    if not roots or any(root not in nodes or nodes[root]["kind"] != "module" for root in roots):
        raise ValueError("Bind actual non-root submodel instances")
    for node in graph["instances"]:
        parent, seen = node["parent"], set()
        while parent in nodes and parent not in seen:
            if parent in selected:
                selected.add(node["id"])
                break
            seen.add(parent)
            parent = nodes[parent]["parent"]
    return selected


def binding_conflicts(module, binding, bindings):
    """Find duplicate occurrences, independent of contract/dictionary order."""
    parts = {i for ids in binding.get("roles", {}).values() for i in ids}
    roots = set(binding.get("roots", []))
    conflicts = []
    for owner, other in sorted(bindings.items()):
        if owner == module or {owner, module} == {"steering", "suspension"}:
            continue
        reused = parts & {i for ids in other.get("roles", {}).values() for i in ids}
        shared_roots = roots & set(other.get("roots", []))
        if reused or shared_roots:
            conflicts.append(dict(owner=owner, instances=sorted(reused), roots=sorted(shared_roots)))
    return conflicts


def validate_binding(graph, contract, module, roots, roles, *, bindings=None):
    if module not in contract.get("modules", {}):
        raise ValueError("Module is not required by this vehicle contract")
    selected = descendants(graph, roots)
    nodes = {n["id"]: n for n in graph["instances"]}
    expected = set(contract["modules"][module]["roles"])
    if set(roles) != expected:
        raise ValueError("Provide every required role exactly: " + ", ".join(sorted(expected)))
    seen = set()
    for ids in roles.values():
        if (not ids or len(ids) != len(set(ids)) or any(i not in selected or nodes[i]["kind"] != "part" for i in ids)):
            raise ValueError("Roles need distinct actual part instances in the bound submodels")
        if seen.intersection(ids):
            raise ValueError("One part cannot stand in for distinct roles")
        seen.update(ids)
    binding = dict(roots=roots, roles=roles)
    conflicts = binding_conflicts(module, binding, bindings or {})
    if conflicts:
        raise ValueError("Module instances already assigned: " + json.dumps(conflicts, sort_keys=True)
            + ". Create an independent submodel instance with edit(module/add) or graft, then bind its new IDs; "
            "do not edit or reuse completed modules.")
    return binding


def evaluate(graph, catalog, contract, bindings, check, motion=None, role_review=None):
    """Report structural evidence separately from semantic/appearance judgements."""
    if not contract:
        return dict(schema=VERSION, status="unverified", applicable=False, modules=[], completion_ready=False)
    nodes = {n["id"]: n for n in graph["instances"]}
    graph_hash, bindings_hash = identity(graph), identity(bindings)
    from .assembly_layout import wheel_layout
    layout = wheel_layout(graph, catalog, contract, bindings)
    from .assembly_evidence import check_current
    current = check_current(graph, check)
    motion_current = check_current(graph, motion or {})
    review = role_review or {}
    review_bound = (review.get("schema") == "vehicle-module-review.v1"
        and review.get("graph_sha256") == graph_hash and review.get("bindings_sha256") == bindings_hash
        and review.get("contract_sha256") == identity(contract)
        and review.get("reviewer") and review.get("evidence_verified") is True)
    reports = []
    unknown = {row["instance"] for row in check.get("unknown", []) if isinstance(row, dict) and "instance" in row}
    colors = None
    for key, requirement in contract["modules"].items():
        binding, failures, unverified = bindings.get(key), [], []
        for item in layout["issues"]:
            if key in item["modules"]:
                (failures if item["status"] == "fail" else unverified).append(item["code"])
        if not current:
            unverified.append("current_revision_check_required")
        members, parts, joints, contacts = set(), set(), [], []
        if not binding:
            failures.append("missing_binding")
        else:
            try:
                validate_binding(graph, contract, key, binding["roots"], binding["roles"], bindings=bindings)
                members = descendants(graph, binding["roots"])
                parts = {i for i in members if nodes[i]["kind"] == "part"}
                if not parts:
                    failures.append("empty_module")
                if parts & unknown:
                    unverified.append("missing_native_geometry_or_connectivity")
                for role in ("glass", "lens"):
                    if role not in binding["roles"]:
                        continue
                    from .assembly_materials import palette, rgba
                    if colors is None:
                        colors = palette(catalog.root)
                    for i in binding["roles"][role]:
                        color = rgba(nodes[i]["color"], colors)
                        if color is None:
                            unverified.append("unknown_transparent_material:" + i)
                        elif color[3] == 255:
                            failures.append("opaque_" + role + ":" + i)
                purpose = requirement["joint_purpose"]
                joints = [i for i, j in graph["joints"].items()
                    if i in members and j.get("purpose") == purpose and j["limits"][0] < j["limits"][1]] if purpose else []
                if len(joints) < requirement["minimum_joints"]:
                    failures.append("missing_" + str(purpose) + "_joints")
                from .assembly_motion import joint_range_issues
                for j in joints:
                    failures.extend(joint_range_issues(graph["joints"][j]))
                from .assembly_mounts import mechanism_response, wheel_mount_issues
                failures.extend(wheel_mount_issues(graph, contract, bindings, key))
                if key in {"steering", "suspension"}:
                    response = mechanism_response(graph, contract, bindings, key, require_complete=True)
                    if response["status"] != "pass":
                        failures.append("wheel_response_failed")
                if key.startswith("door_") and not any(
                        set(binding["roles"]["panel"] + binding["roles"].get("glass", [])) <= descendants(graph, [j])
                        for j in joints if nodes[j]["kind"] == "module"):
                    failures.append("door_glass_not_in_shared_moving_subtree")
                contacts = [e for e in check.get("connections", []) if (e["a"] in parts) != (e["b"] in parts)]
                if not contacts:
                    failures.append("no_verified_external_connection")
                if purpose:
                    tested = {j["instance"]: j for j in (motion or {}).get("joints", [])}
                    for j in joints:
                        poses = tested.get(j, {}).get("poses", [])
                        if (not motion_current or len(poses) < 9
                                or any(p.get("status") != "pass" for p in poses)):
                            unverified.append("motion_not_passed:" + j)
            except (ValueError, KeyError, TypeError) as error:
                failures.append("invalid_binding:" + str(error))
        role_status = review.get("modules", {}).get(key, {}).get("status") if review_bound else "unverified"
        if role_status == "fail":
            failures.append("independent_role_review_failed")
        elif role_status != "pass":
            unverified.append("independent_role_review_required")
        status = "fail" if failures else "unverified" if unverified else "pass"
        reports.append(dict(id=key, status=status, parts=sorted(parts), roots=(binding or {}).get("roots", []),
            roles=(binding or {}).get("roles", {}), joints=joints, interface_contacts=contacts,
            failures=sorted(set(failures)), unverified=sorted(set(unverified))))
    repairs = []
    for task in contract.get("reference_repairs", []):
        decision = review.get("repairs", {}).get(task["id"], {}) if review_bound else {}
        closed = (decision.get("status") == "pass" and decision.get("source_sha256") == task["source_sha256"]
            and bool(decision.get("evidence")))
        repairs.append({**task, "status": "closed" if closed else "open", "evidence": decision.get("evidence", []) if closed else []})
    digital = (layout["status"] == "pass" and current and check.get("status") == "pass"
        and motion_current and motion.get("status") == "pass")
    structural = digital and all(not r["failures"] and set(r["unverified"]) <= {"independent_role_review_required"} for r in reports)
    scope_review = contract.get("scope_review_required", [])
    ready = (structural and not scope_review and all(r["status"] == "pass" for r in reports)
        and all(r["status"] == "closed" for r in repairs))
    return dict(schema=VERSION, revision=graph["revision"], applicable=True, modules=reports,
        status="pass" if ready else "fail" if any(r["failures"] for r in reports) or (current and check.get("status") == "fail") else "unverified",
        complete=sum(r["status"] == "pass" for r in reports), required=len(reports), completion_ready=ready,
        structural_ready=structural, repair_tasks=repairs, scope_review_required=scope_review,
        excluded_reference_repairs=contract.get("excluded_reference_repairs", []),
        wheel_layout=layout,
        graph_sha256=graph_hash, bindings_sha256=bindings_hash, contract_sha256=identity(contract),
        appearance="unverified", physical="unverified")
