"""Native contact joints must preserve the fixed support and the parent frame."""
from copy import deepcopy
import os
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from car2lego3d.assembly_tools import AssemblyTools
from scripts.probe_connector_module import demonstration_operations, rolling_evidence


@pytest.fixture
def scene(tmp_path, monkeypatch):
    native = Path(os.environ.get("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    monkeypatch.setenv("CAR2LEGO_BRICKLAYER_AUDIT", "off")
    tools = AssemblyTools(tmp_path, native)
    result = tools.call("build", dict(revision=0, operations=demonstration_operations(rolling=True)[:-1]))
    assert result["ok"], result
    ids = result["result"]["aliases"]
    args = dict(revision=1, instance=ids["wheel"], source=ids["axle"], source_port="0",
        target=ids["bearing"], target_port="2", limits=[0, 360], purpose="rolling")
    return tools, args


def test_contact_joint_rotates_actual_wheel_with_fixed_support_and_survives_restart(scene):
    tools, args = scene
    before = deepcopy(tools.assembly.graph()["instances"])
    result = tools.call("articulate", args)
    assert result["ok"], result
    assert result["revision_before"] == 1 and result["revision_after"] == 2
    assert tools.assembly.graph()["instances"] == before
    joint = result["result"]["joint"]
    np.testing.assert_allclose(joint["axis"], [0, 0, -1])
    np.testing.assert_allclose(joint["origin"], [0, 10, -10])
    assert result["result"]["motion"] == "unverified"
    assert result["result"]["instance"] == args["instance"]
    assert tools.call("check", {})["result"]["status"] == "pass"
    motion = tools.call("motion", dict(samples=9))["result"]
    assert motion["status"] == "pass" and len(motion["joints"][0]["poses"]) == 9
    assert all(p["connectivity"] == "pass" for p in motion["joints"][0]["poses"])
    assert rolling_evidence(tools)["status"] == "pass"
    restored = AssemblyTools(tools.directory, tools.catalog_root)
    assert restored.assembly.graph() == tools.assembly.graph()
    assert rolling_evidence(restored)["status"] == "pass"


def test_joint_axis_is_converted_to_rotated_translated_parent_frame(tmp_path, scene):
    tools, _ = scene
    parent = np.eye(4)
    parent[:3, :3] = Rotation.from_euler("xyz", [25, 60, -30], degrees=True).as_matrix()
    parent[:3, 3] = [100, 45, -220]
    tools = AssemblyTools(tmp_path / "nested", tools.catalog_root)
    ops = [dict(op="module", name="mount.ldr", alias="mount", matrix=parent.tolist())]
    for op in demonstration_operations(rolling=True):
        if op["op"] in {"add", "module"} and "parent" not in op:
            op["parent"] = "@mount"
        ops.append(op)
    result = tools.call("build", dict(revision=0, operations=ops))
    assert result["ok"], result
    assert result["revision_after"] == 1
    joint = next(iter(tools.assembly.joints.values()))
    np.testing.assert_allclose(joint["axis"], [0, 0, -1], atol=1e-6)
    np.testing.assert_allclose(joint["origin"], [0, 10, -10], atol=1e-4)
    assert tools.call("motion", dict(samples=9))["result"]["status"] == "pass"


@pytest.mark.parametrize("fault", ["stale", "port", "detached", "inside", "source_outside", "range", "prismatic", "protected"])
def test_invalid_joint_leaves_graph_and_checkpoint_unchanged(scene, fault, monkeypatch):
    tools, args = scene
    if fault == "stale":
        args["revision"] = 0
    elif fault == "port":
        args["source_port"] = "invented"
    elif fault == "detached":
        args["source_port"] = "1"
    elif fault == "inside":
        args.update(target=args["source"], target_port="1")
    elif fault == "source_outside":
        args["instance"] = args["target"]
    elif fault == "range":
        args["limits"] = [-np.pi, np.pi]
    elif fault == "prismatic":
        args["type"] = "prismatic"
    elif fault == "protected":
        from car2lego3d import assembly_mounts
        tools.protected_state = dict(bindings={"fixture": {}})
        monkeypatch.setattr(assembly_mounts, "protected_changed", lambda *args: True)
    before = (tools.assembly.graph(), (tools.directory / "state.json").read_bytes())
    result = tools.call("articulate", args)
    assert not result["ok"], result
    assert (tools.assembly.graph(), (tools.directory / "state.json").read_bytes()) == before


def test_failed_contact_joint_rolls_back_entire_build(tmp_path, scene):
    native = scene[0].catalog_root
    tools = AssemblyTools(tmp_path / "rollback", native)
    ops = demonstration_operations(rolling=True)
    ops[-1]["source_port"] = "1"
    before = tools.assembly.graph()
    result = tools.call("build", dict(revision=0, operations=ops))
    assert not result["ok"], result
    assert tools.assembly.graph() == before
    assert not (tools.directory / "state.json").exists()


def test_rolling_gate_rejects_missing_motion_and_changed_graph(scene):
    tools, args = scene
    assert tools.call("articulate", args)["ok"]
    assert tools.call("check", {})["ok"]
    assert rolling_evidence(tools)["status"] == "fail"
    assert tools.call("motion", dict(samples=9))["ok"]
    assert rolling_evidence(tools)["status"] == "pass"
    result = tools.call("edit", dict(revision=2, operations=[dict(op="color", instance=args["source"], color=4)]))
    assert result["ok"], result
    assert "motion_missing_failed_or_stale" in rolling_evidence(tools)["issues"]
