import numpy as np
import pytest

from car2lego3d.assembly_graph import Assembly


def test_one_atomic_batch_builds_nested_door_and_glass_without_predicting_instance_ids():
    assembly = Assembly("0 FILE main.ldr\n")
    matrix = np.eye(4).tolist()
    result = assembly.transact(0, [
        dict(op="module", name="door.ldr", alias="door", matrix=matrix),
        dict(op="add", parent="@door", part="3001.dat", color=4, alias="panel", matrix=matrix),
        dict(op="add", parent="@door", part="3068b.dat", color=47, matrix=matrix),
        dict(op="joint", instance="@door", axis=[0, 1, 0], limits=[0, 70], purpose="door")])
    door = result["aliases"]["door"]
    assert result["revision"] == 1
    assert len([node for node in result["instances"] if node["parent"] == door]) == 2
    assert result["joints"][door]["purpose"] == "door"
    assert Assembly(assembly.text()).graph()["instances"] == result["instances"]


def test_unresolved_or_duplicate_alias_rolls_back_the_whole_batch():
    assembly = Assembly("0 FILE main.ldr\n")
    before = assembly.text()
    for operations in [
        [dict(op="add", parent="@missing", part="3001.dat", matrix=np.eye(4).tolist())],
        [dict(op="module", alias="same", name=name + ".ldr", matrix=np.eye(4).tolist()) for name in ("a", "b")]]:
        with pytest.raises(ValueError, match="alias"):
            assembly.transact(0, operations)
        assert assembly.revision == 0 and assembly.text() == before
