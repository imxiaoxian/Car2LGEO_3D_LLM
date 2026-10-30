from pathlib import Path

from car2lego3d.assembly_catalog import NativeCatalog


def test_native_description_cache_survives_catalog_recreation_and_detects_edits(tmp_path, monkeypatch):
    parts = tmp_path / "parts"
    parts.mkdir()
    part = parts / "wheel.dat"
    part.write_text("0 Wheel 49.6\n")
    original, reads = Path.open, []

    def tracked(path, *args, **kwargs):
        if path == part:
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked)
    assert NativeCatalog(tmp_path).search("wheel")["parts"][0]["description"] == "Wheel 49.6"
    assert len(reads) == 1
    catalog = NativeCatalog(tmp_path)
    assert catalog.search("wheel")["total"] == 1
    assert len(reads) == 1
    part.write_text("0 Tyre updated 49.6\n")
    reads.clear()
    assert catalog.search("tyre")["total"] == 1
    assert catalog.search("wheel")["parts"][0]["description"] == "Tyre updated 49.6"
    assert len(reads) == 1


def test_description_cache_keeps_package_precedence_and_notices_added_parts(tmp_path):
    native, package = tmp_path / "native", tmp_path / "package"
    for root in (native, package):
        (root / "parts").mkdir(parents=True)
        (root / "parts/part.dat").write_text("0 " + root.name + " part\n")
    catalog = NativeCatalog(native, [package])
    assert catalog.search("package")["total"] == 1
    assert catalog.search("native")["total"] == 0
    (native / "parts/new.dat").write_text("0 New native beam\n")
    assert catalog.search("beam")["parts"][0]["part"] == "new.dat"


def test_edit_only_rereads_changed_description_and_removal_disappears(tmp_path, monkeypatch):
    parts = tmp_path / "parts"
    parts.mkdir()
    for index in range(40):
        (parts / f"{index}.dat").write_text(f"0 Beam {index}\n")
    catalog = NativeCatalog(tmp_path)
    assert catalog.search("beam", limit=50)["total"] == 40
    original, reads = Path.open, []

    def tracked(path, *args, **kwargs):
        if path.parent == parts:
            reads.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked)
    (parts / "7.dat").write_text("0 Updated wheel\n")
    (parts / "8.dat").unlink()
    reads.clear()
    assert catalog.search("wheel")["parts"] == [dict(part="7.dat", description="Updated wheel")]
    assert reads == ["7.dat"]
    assert catalog.search("beam", limit=50)["total"] == 38
