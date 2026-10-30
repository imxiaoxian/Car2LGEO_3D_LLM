from types import SimpleNamespace

import numpy as np
import pytest

from car2lego3d.assembly_layout import wheel_layout
from car2lego3d.assembly_requirements import evaluate, identity
from car2lego3d.assembly_tools import AssemblyTools


def native_box(root, name, half, offset=(0, 0, 0)):
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * half + offset
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
        (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    path = root / "parts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("0 Native surface fixture\n" + "\n".join("3 16 " + " ".join(map(str, corners[list(face)].ravel()))
        for face in faces) + "\n")


def vehicle(tmp_path, *, different_radii=False, missing=None):
    native = tmp_path / "native"
    # The native mesh origin deliberately differs from its geometric center.
    native_box(native, "tyre.dat", [50, 50, 12], [8, -13, 7])
    native_box(native, "large.dat", [60, 60, 12], [8, -13, 7])
    native_box(native, "rim.dat", [20, 20, 10])
    native_box(native, "hub.dat", [5, 5, 15])
    tools = AssemblyTools(tmp_path / "working", native)
    contract, bindings = dict(modules={}), {}
    operations = [dict(op="add", part="hub.dat", alias="body", matrix=np.eye(4).tolist())]
    for axle, x in (("front", 100), ("rear", 500)):
        for side, z in (("left", -150), ("right", 150)):
            name = f"wheel_{axle}_{side}"
            contract["modules"][name] = dict(roles=["tyre", "rim", "hub"], joint_purpose="rolling", minimum_joints=1)
            if name == missing:
                continue
            radius = 60 if different_radii and axle == "rear" else 50
            mount = np.eye(4)
            mount[:3, 3] = [x - 8, 13 - radius, z - 7]
            operations.extend([
                dict(op="module", name=name + ".ldr", alias=name, matrix=mount.tolist()),
                dict(op="joint", instance="@" + name, axis=[0, 0, 1], limits=[0, 360], purpose="rolling"),
                *[dict(op="add", parent="@" + name, part=("large.dat" if radius == 60 else "tyre.dat")
                    if role == "tyre" else role + ".dat", alias=name + role, matrix=np.eye(4).tolist())
                    for role in ("tyre", "rim", "hub")]])
    created = tools.call("edit", dict(revision=0, operations=operations))
    assert created["ok"], created
    ids = created["result"]["aliases"]
    for name in contract["modules"]:
        if name != missing:
            bindings[name] = dict(roots=[ids[name]], roles={role: [ids[name + role]] for role in ("tyre", "rim", "hub")})
    tools.vehicle_contract, tools.module_bindings = contract, bindings
    return tools, ids


def move(tools, ids, name, delta):
    graph = tools.assembly.graph()
    node = next(n for n in graph["instances"] if n["id"] == ids[name])
    matrix = np.asarray(node["local"])
    matrix[:3, 3] += delta
    result = tools.call("edit", dict(revision=tools.assembly.revision,
        operations=[dict(op="transform", instance=ids[name], matrix=matrix.tolist())]))
    assert result["ok"], result


def measure(tools, **kwargs):
    return wheel_layout(tools.assembly.graph(), tools.catalog, tools.vehicle_contract, tools.module_bindings, **kwargs)


def test_native_surface_centers_and_different_radii_share_ground(tmp_path):
    tools, _ = vehicle(tmp_path, different_radii=True)
    report = measure(tools)
    assert report["status"] == "pass" and report["complete"]
    assert report["graph_sha256"] == identity(tools.assembly.graph())
    assert report["bindings_sha256"] == identity(tools.module_bindings)
    assert report["wheels"]["wheel_front_left"]["center_ldu"] == [100, -50, -150]
    assert report["wheels"]["wheel_rear_left"]["center_ldu"] == [500, -60, -150]
    assert {w["ground_y_ldu"] for w in report["wheels"].values()} == {0}


def test_known_32_8_mm_stance_error_fails(tmp_path):
    tools, ids = vehicle(tmp_path)
    move(tools, ids, "wheel_front_left", [0, -82, 0])
    report = measure(tools)
    assert report["status"] == "fail"
    errors = [i for i in report["issues"] if i["code"] == "wheel_ground_contact_mismatch"]
    assert errors and errors[0]["delta_mm"] == pytest.approx(32.8)


def test_matching_wheel_heights_cannot_put_the_chassis_through_the_ground(tmp_path):
    tools, ids = vehicle(tmp_path)
    added = tools.call("edit", dict(revision=tools.assembly.revision, operations=[
        dict(op="module", name="chassis.ldr", alias="chassis", matrix=np.eye(4).tolist()),
        dict(op="add", part="hub.dat", alias="frame", parent="@chassis", matrix=np.eye(4).tolist())]))
    aliases = added["result"]["aliases"]
    tools.vehicle_contract["modules"]["chassis"] = dict(roles=["structure"], joint_purpose=None, minimum_joints=0)
    tools.module_bindings["chassis"] = dict(roots=[aliases["chassis"]], roles=dict(structure=[aliases["frame"]]))
    # All tyres currently touch Y=0, but frame extends to Y=5 below that plane.
    report = measure(tools)
    assert report["status"] == "fail"
    assert {i["code"] for i in report["issues"]} == {"wheel_ground_above_chassis"}
    assert report["chassis"]["bottom_y_ldu"] == 5
    for name in list(tools.module_bindings):
        if name.startswith("wheel_"):
            move(tools, ids, name, [0, 20, 0])
    assert measure(tools)["status"] == "pass"


@pytest.mark.parametrize("target,delta,code", [
    ("wheel_front_left", [5, 0, 0], "wheel_axle_position_mismatch"),
    ("wheel_front_left", [0, 0, 300], "wheel_lateral_order_or_overlap"),
    ("wheel_rear_left", [0, 0, 15], "wheel_axle_centerline_mismatch"),
    ("wheel_rear_left", [-410, 0, 0], "wheel_axle_order_or_overlap")])
def test_wrong_axle_track_and_order_fail(tmp_path, target, delta, code):
    tools, ids = vehicle(tmp_path)
    move(tools, ids, target, delta)
    report = measure(tools)
    assert report["status"] == "fail" and any(i["code"] == code for i in report["issues"])


def test_missing_wheel_is_pending_only_partial_candidate_may_pass(tmp_path):
    tools, _ = vehicle(tmp_path, missing="wheel_rear_right")
    report = measure(tools)
    assert report["status"] == "pending" and not report["complete"]
    assert report["missing"] == ["wheel_rear_right"]
    assert measure(tools, require_complete=False)["status"] == "pass"


def test_missing_native_geometry_is_unverified_and_not_a_pass(tmp_path):
    tools, _ = vehicle(tmp_path)
    report = wheel_layout(tools.assembly.graph(), SimpleNamespace(root=tmp_path),
        tools.vehicle_contract, tools.module_bindings)
    assert report["status"] == "unverified" and not report["complete"]
    assert {i["code"] for i in report["issues"]} == {"wheel_geometry_unverified"}
    (tools.catalog_root / "parts/tyre.dat").unlink()
    assert measure(tools)["status"] == "unverified"


def test_nested_rotation_uses_world_geometry(tmp_path):
    tools, _ = vehicle(tmp_path)
    graph = tools.assembly.graph()
    # Simulate a second nested rotated parent and inverse local transform.
    # World remains authoritative; using native bounds plus translation fails.
    name = "wheel_front_left"
    tyre = next(n for n in graph["instances"] if n["id"] == tools.module_bindings[name]["roles"]["tyre"][0])
    world = np.asarray(tyre["world"])
    world[:3, :3] = np.diag([-1, -1, 1])
    world[:3, 3] = [108, -63, -157]
    tyre["world"] = world.tolist()
    tyre["local"] = np.eye(4).tolist()
    report = wheel_layout(graph, tools.catalog, tools.vehicle_contract, tools.module_bindings)
    assert report["status"] == "pass"
    assert report["wheels"][name]["center_ldu"] == [100, -50, -150]


def digital_pass(graph, *args, **kwargs):
    parts = [n["id"] for n in graph["instances"] if n["kind"] == "part"]
    return dict(revision=graph["revision"], status="pass", unknown=[], collisions=[], invalid_transforms=[],
        components=[parts], connections=[dict(a=parts[0], b=part) for part in parts[1:]], gates=dict(connections="pass"))


def test_check_export_and_module_candidate_cannot_admit_bad_stance(tmp_path, monkeypatch):
    from car2lego3d import assembly_catalog, assembly_tools
    from car2lego3d.assembly_schedule import module_check
    tools, ids = vehicle(tmp_path)
    move(tools, ids, "wheel_front_left", [0, -82, 0])
    # Isolate layout admission from the independently tested contact/collider checker.
    monkeypatch.setattr(assembly_tools, "validate", digital_pass)
    monkeypatch.setattr(assembly_catalog, "validate", digital_pass)
    report = tools.tool_check()
    assert report["status"] == "fail" and report["gates"]["wheel_layout"] == "fail"
    measured = module_check(tools, "wheel_front_left", compare=False)
    assert measured["status"] == "fail" and "wheel_ground_contact_mismatch" in measured["failures"]
    exported = tools.tool_export()
    assert exported["delivery_status"] == "draft" and exported["checks"]["status"] == "fail"
    assert not exported["module_progress"]["completion_ready"]


def test_final_evaluation_recomputes_layout_instead_of_trusting_old_pass(tmp_path):
    tools, ids = vehicle(tmp_path)
    move(tools, ids, "wheel_front_left", [0, -82, 0])
    graph = tools.assembly.graph()
    check = dict(digital_pass(graph), graph_sha256=identity(graph), evidence_files=[])
    motion = dict(check, joints=[dict(instance=key, poses=[dict(status="pass")] * 9) for key in graph["joints"]])
    review = dict(schema="vehicle-module-review.v1", graph_sha256=identity(graph),
        bindings_sha256=identity(tools.module_bindings), contract_sha256=identity(tools.vehicle_contract),
        reviewer="independent-test-review", evidence_verified=True,
        modules={name: dict(status="pass") for name in tools.vehicle_contract["modules"]})
    report = evaluate(graph, tools.catalog, tools.vehicle_contract, tools.module_bindings, check, motion, review)
    assert report["status"] == "fail" and not report["structural_ready"] and not report["completion_ready"]
    assert report["wheel_layout"]["status"] == "fail"
