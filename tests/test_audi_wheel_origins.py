"""Regression checks for old Studio custom-part origins and native replacements."""
import importlib.util
import csv
import json

import numpy as np
import pytest

from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.official_library import ArchiveGeometry


spec = importlib.util.spec_from_file_location("audi_repair", ROOT / "scripts/repair_audi_wheel_origins.py")
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


@pytest.mark.parametrize("prefix,start", [("1 256", 2), ("11 256 549 False 0", 5)])
def test_rebases_in_the_part_frame_and_preserves_studio_metadata(prefix, start):
    source = ("0 FILE wheel assembly\r\n" + prefix + " 10 20 30 0 1 0 0 0 -1 -1 0 0 2491.dat\r\n"
              "0 STEP\r\n0 NOFILE\r\n").encode()
    fixed, counts = repair.rebase_document(source)
    fields = fixed.decode().splitlines()[1].split()
    assert fields[:start] == prefix.split()
    assert np.allclose(np.asarray(fields[start:start+3], dtype=float), [-91.809, 20, 30])
    assert counts == {"2491.dat": 1}
    assert b"0 FILE wheel assembly\r\n" in fixed and fixed.endswith(b"0 STEP\r\n0 NOFILE\r\n")


def test_replaces_complete_hub_and_discards_only_obsolete_embedded_sections():
    source = ("0 FILE main\n1 0 0 0 0 1 0 0 0 1 0 0 0 1 3468c01.dat\n0 NOFILE\n"
              "0 FILE 3468c01.dat\n0 obsolete hub\n3 16 0 0 0 1 0 0 0 1 0\n0 NOFILE\n"
              "0 FILE spring.dat\n0 preserve spring\n0 NOFILE\n").encode()
    fixed, counts = repair.rebase_document(source)
    line = fixed.decode().splitlines()[1]
    name, color, matrix = ArchiveGeometry.reference(line)
    assert name == "100943p01.dat" and color == 0
    assert np.allclose(matrix[:3, 3], [0, -48, 25.5])
    assert b"obsolete hub" not in fixed and b"preserve spring" in fixed
    assert counts == {"3468c01.dat": 1}


def test_source_guard_and_narrow_custom_dependency_removal(tmp_path):
    source = tmp_path / "different.io"
    source.write_bytes(b"unrelated archive")
    with pytest.raises(ValueError, match="verified original"):
        repair.repair_archive(source, tmp_path / "output.io")
    for name in ("CustomParts/2491.dat", "CustomParts/connectivity/3468c01.conn", "CustomParts/collider/4192.col"):
        assert repair.obsolete_member(name)
    for name in ("CustomParts/731c03.dat", "CustomParts/p/4-4cyli.dat", "modelv2.ldr"):
        assert not repair.obsolete_member(name)


def test_publish_keeps_backup_and_synchronizes_hashes_without_changing_other_models(tmp_path, monkeypatch):
    result = tmp_path / "result"
    reports = result / "_reports"
    reports.mkdir(parents=True)
    source = result / "audi.io"
    source.write_bytes(b"original")
    candidate = tmp_path / "candidate.io"
    candidate.write_bytes(b"repaired candidate")
    monkeypatch.setattr(repair, "SOURCE_SHA", digest(source))
    row = dict(id=repair.MODEL_ID, file=source.name, sha256=digest(source), bytes=source.stat().st_size)
    other = dict(id="b"*24, file="other.io", sha256="untouched", brands=["other"])
    catalog_path = tmp_path / "catalog.zh.json"
    catalog = dict(models=[row, other], types={})
    write_json(catalog_path, catalog)
    write_json(result / "车辆信息.json", catalog)
    write_json(reports / "manifest.json", [dict(library_id=repair.MODEL_ID, validation=dict(embedded_custom_files=80))])
    write_json(reports / "final_integrity.json", dict(total_io_bytes=123, files=[dict(file=source.name)]))
    write_json(reports / "chinese-sync-validation.json", dict(io_content_unchanged=True))
    with (result / "models.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows([["文件名", "SHA256"], [source.name, digest(source)], ["other.io", "untouched"]])
    report = dict(removed_obsolete_members=["CustomParts/2491.dat", "CustomParts/connectivity/2491.conn",
                                            "CustomParts/collider/2491.col"],
                  validation=dict(wheel_pairs=[dict(ok=True)]))
    repair.publish(result, catalog_path, candidate, report)
    updated = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert updated == json.loads((result / "车辆信息.json").read_text(encoding="utf-8"))
    assert updated["models"][1] == other
    assert updated["models"][0]["sha256"] == digest(candidate) == digest(source)
    assert (reports / "before-audi-wheel-origin-fix/audi.io").read_bytes() == b"original"
    integrity = json.loads((reports / "final_integrity.json").read_text(encoding="utf-8"))
    assert integrity["total_io_bytes"] == 123 + len(b"repaired candidate") - len(b"original")
    assert integrity["files"][0]["sha256"] == digest(source)
    manifest = json.loads((reports / "manifest.json").read_text(encoding="utf-8"))
    assert manifest[0]["validation"]["embedded_custom_files"] == 79
    with (result / "models.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[1][1] == digest(source) and rows[2] == ["other.io", "untouched"]
