import json
from zipfile import ZipFile

import numpy as np
import pytest

from car2lego3d.assembly_graph import Assembly, IOArchive, LDRAW_TO_BLENDER

MODEL = """0 FILE main.ldr
1 4 100 0 0 0 0 1 0 1 0 -1 0 0 door.ldr
1 1 -100 0 0 1 0 0 0 1 0 0 0 1 door.ldr
0 FILE door.ldr
0 STEP
1 16 10 0 0 1 0 0 0 1 0 0 0 1 3001.dat
1 47 0 -24 0 1 0 0 0 1 0 0 0 1 3002.dat
"""


def test_nested_transform_color_and_copy_on_write():
    a = Assembly(MODEL)
    graph = a.graph()
    parts = [n for n in graph["instances"] if n["kind"] == "part"]
    assert [n["color"] for n in parts] == [4, 47, 1, 47]
    assert np.allclose(np.array(parts[0]["world"])[:3, 3], [100, 0, -10])
    assert parts[0]["step"] == 1
    identity = parts[0]["id"]
    a.transact(0, [dict(op="color", instance=identity, color=14)])
    after = [n for n in a.graph()["instances"] if n["kind"] == "part"]
    assert [n["color"] for n in after] == [14, 47, 1, 47]
    assert after[0]["id"] == identity
    assert Assembly(a.text()).bom() == a.bom()
    assert np.allclose(LDRAW_TO_BLENDER @ [20, -8, 0, 1], [.008, .0032, 0, 1])


def test_transaction_rollback_revision_and_scale():
    a = Assembly(MODEL)
    identity = a.graph()["instances"][1]["id"]
    with pytest.raises(ValueError, match="scaled"):
        a.transact(0, [dict(op="color", instance=identity, color=14),
                       dict(op="transform", instance=identity, matrix=np.diag([2, 1, 1, 1]).tolist())])
    assert a.revision == 0 and a.text() == MODEL
    with pytest.raises(ValueError, match="validation failed"):
        a.transact(0, [dict(op="remove", instance=identity)], lambda g: {"status": "fail"})
    assert a.revision == 0
    with pytest.raises(ValueError, match="Stale"):
        a.transact(1, [])


def test_occurrence_ids_and_copy_on_write_replay_exactly():
    left, right = Assembly(MODEL), Assembly(MODEL)
    identity = left.graph()["instances"][1]["id"]
    operations = [dict(op="color", instance=identity, color=14),
        dict(op="add", part="3001.dat", matrix=np.eye(4).tolist()),
        dict(op="add", part="3001.dat", matrix=np.eye(4).tolist())]
    assert left.transact(0, operations) == right.transact(0, operations)
    assert left.text() == right.text()
    restored = Assembly(left.text(), left.source_hash)
    restored.revision = left.revision
    update = [dict(op="add", parent=left.graph()["instances"][0]["id"],
        part="3002.dat", matrix=np.eye(4).tolist())]
    assert left.transact(1, update) == restored.transact(1, update)


def test_archive_preserves_unknown_members_and_model_variants(tmp_path):
    src = tmp_path / "original.io"
    with ZipFile(src, "w") as z:
        for name in ("model.ldr", "model2.ldr", "modelv2.ldr"):
            z.writestr(name, MODEL)
        z.writestr(".info", json.dumps(dict(total_parts=4, unknown=123)))
        z.writestr("unknown.bin", b"\x00\xffmetadata")
        z.writestr("model.lxfml", b"opaque derived model")
        z.comment = b"preserved"
    arc = IOArchive(src)
    output = tmp_path / "output.io"
    arc.export(output)
    assert output.read_bytes() == src.read_bytes()
    arc.assembly.transact(0, [dict(op="color", instance=arc.assembly.graph()["instances"][1]["id"], color=14)])
    report = arc.export(output)
    assert report["stale_derived_members"] == ["model.lxfml"]
    with ZipFile(output) as z:
        assert z.read("unknown.bin") == b"\x00\xffmetadata"
        assert z.comment == b"preserved"
        assert z.read("model.ldr") == z.read("model2.ldr") == z.read("modelv2.ldr")
        assert json.loads(z.read(".info"))["unknown"] == 123
    assert src.read_bytes() != output.read_bytes()
    reopened = IOArchive(output)
    assert reopened.assembly.revision == 1
    identity = reopened.assembly.graph()["instances"][0]["id"]
    reopened.assembly.transact(1, [dict(op="joint", instance=identity, axis=[0, 1, 0], limits=[0, 90])])
    second = tmp_path / "second.io"
    reopened.export(second)
    with ZipFile(second) as z:
        assert len(z.namelist()) == len(set(z.namelist()))
    assert IOArchive(second).assembly.joints[identity]["limits"] == [0, 90]
