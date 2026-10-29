"""Catalog, portable downloads and actual nested LDraw preview checks."""
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, unquote
from zipfile import ZipFile

import numpy as np
import pytest
import trimesh
from PIL import Image

from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.desktop import DesktopStore
from car2lego3d.official_library import ArchiveGeometry, OfficialLibrary
from car2lego3d.web import Handler, JobStore


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    path = root / "法拉利 F40 [10248-1].io"
    text = """0 FILE main.ldr
1 4 10 20 30 1 0 0 0 1 0 0 0 1 door left.ldr
0 FILE door left.ldr
1 16 5 0 0 0 0 1 0 1 0 -1 0 0 window.dat
0 FILE window.dat
1 16 0 0 0 1 0 0 0 1 0 0 0 1 detail.dat
0 NOFILE
"""
    with ZipFile(path, "w") as archive:
        archive.writestr("model.ldr", text)
        archive.writestr("CustomParts/parts/detail.dat", "0 triangle\n3 16 0 0 0 10 0 0 0 10 0\n")
    brand = dict(id="ferrari", en="Ferrari", zh="法拉利")
    row = dict(id="a"*24, name_zh="法拉利 F40", original_name="Ferrari F40", model="法拉利 F40", brands=[brand],
               vehicle_types=["sports_car"], vehicle_type_labels=["跑车"], vehicle_year=None, release_year=2015,
               variant_zh="", set_num="10248-1", file=path.name, sha256=digest(path), thumbnail_file=None)
    other = dict(row, id="b"*24, name_zh="福特 GT 与 Bronco", original_name="Ford GT and Bronco", model="福特 GT 与 Bronco",
                 brands=[dict(id="ford", en="Ford", zh="福特"), brand],
                 vehicle_types=["sports_car", "suv"], vehicle_year=1966, release_year=2021, set_num="76905-1")
    catalog = tmp_path / "catalog.json"
    write_json(catalog, dict(types=dict(sports_car="跑车", suv="SUV"), models=[row, other]))
    return OfficialLibrary(catalog, root, tmp_path / "cache", tmp_path / "parts")


@pytest.mark.parametrize("make,model", [("Ferrari", "F40"), ("法拉利", "ｆ ４０"), ("FERRARI", "法拉利 F-40")])
def test_vehicle_match_normalizes_brand_and_model_without_model_prefix_collisions(library, make, model):
    result = library.match_vehicle(make, model)
    assert result["model_ids"] == ["a"*24]
    assert result["url"].startswith("/sets/" + "a"*24 + "?")
    assert library.match_vehicle("Ferrari", "F400") is None
    assert library.match_vehicle("Ford", "F40") is None
    assert library.match_vehicle("Ferrari", "Ferrari") is None
    assert library.match_vehicle("", "F40") is None


def test_vehicle_matching_year_is_optional_and_never_uses_set_release_year(library):
    assert library.match_vehicle("Ferrari", "F40", 1987)["total"] == 1
    assert library.match_vehicle("Ford", "GT", 1966)["total"] == 1
    assert library.match_vehicle("Ford", "GT", 2021) is None
    with pytest.raises(ValueError):
        library.match_vehicle("Ford", "GT", "invalid")


def test_multiple_matches_have_reproducible_listing_and_brand_aliases(library):
    data = library.data()
    data["models"].extend([
        dict(data["models"][0], id="c"*24, name_zh="法拉利 F40 超级跑车", original_name="Ferrari F40 Supercar"),
        dict(data["models"][0], id="d"*24, name_zh="大众甲壳虫", original_name="Volkswagen Beetle", model="大众甲壳虫",
             brands=[dict(id="volkswagen", en="Volkswagen", zh="大众")]),
    ])
    write_json(library.catalog_path, data)
    match = library.match_vehicle("法拉利", "F 40", 1990)
    assert match["model_ids"] == ["a"*24, "c"*24]
    params = parse_qs(match["url"].split("?", 1)[1])
    page = library.listing(params)
    assert [r["id"] for r in page["models"]] == match["model_ids"]
    assert page["collection_total"] == 4
    assert library.match_vehicle("VW", "Beetle")["model_ids"] == ["d"*24]


def test_submission_reuses_library_before_job_lock_without_starting_generation(library, monkeypatch, tmp_path):
    monkeypatch.setattr("car2lego3d.official_library.official_library", lambda: library)
    store = DesktopStore(JobStore(tmp_path / "jobs", None))
    store.active = "existing-task"
    monkeypatch.setattr(store, "run", lambda *_: pytest.fail("A library match must not generate"))
    result = store.submit(dict(make="法拉利", model="F40", year=1990))
    assert result["status"] == "matched" and result["library_match"]["total"] == 1
    assert not list(store.root.glob("*/request.json"))
    assert store.active == "existing-task"


def test_photo_recognition_reuses_archive_and_stops_before_research(library, monkeypatch, tmp_path):
    monkeypatch.setattr("car2lego3d.official_library.official_library", lambda: library)
    monkeypatch.setattr("car2lego3d.desktop.bridge", lambda *args: dict(make="Ferrari", model="F40", year=1987))
    store = DesktopStore(JobStore(tmp_path / "jobs", None))
    monkeypatch.setattr(store, "research", lambda *_: pytest.fail("A recognized library match must stop before research"))
    buffer = BytesIO()
    Image.new("RGB", (32, 32)).save(buffer, "PNG")
    result = store.submit({}, image=buffer.getvalue())
    store.worker.join(5)
    detail = store.detail(result["id"])
    assert detail["status"] == "matched" and detail["library_match"]["total"] == 1
    assert detail["car"]["year"] is None and store.active is None
    assert not (store.directory(result["id"]) / "vehicle-specs.json").exists()


@pytest.mark.parametrize("options,parent", [({}, None), ({"customization_text": "black paint"}, None), ({"color": 0}, None), ({}, "b"*32)])
def test_unmatched_and_customized_submissions_keep_generation(library, monkeypatch, tmp_path, options, parent):
    monkeypatch.setattr("car2lego3d.official_library.official_library", lambda: library)
    store = DesktopStore(JobStore(tmp_path / "jobs", None))
    monkeypatch.setattr(store, "run", lambda *_: None)
    if parent:
        write_json(store.root / parent / "status.json", dict(status="draft"))
    model = "Unknown Model" if not options and not parent else "F40"
    result = store.submit(dict(make="Ferrari", model=model, **options), parent=parent)
    store.worker.join(5)
    assert result["id"] and not result["library_match"]
    assert (store.directory(result["id"]) / "request.json").exists()


def test_filter_combinations_pagination_and_separate_years(library):
    assert library.listing({})["collection_total"] == 2
    assert library.listing(parse_qs("brand=ferrari&type=suv&year=vehicle:1966"))["total"] == 1
    assert library.listing(parse_qs("brand=ford&year=release:2015"))["total"] == 0
    assert library.listing(parse_qs("q=F40&year=vehicle:unknown"))["total"] == 1
    page = library.listing(parse_qs("limit=1&page=2"))
    assert page["page"] == page["pages"] == 2
    assert page["models"][0]["id"] == "b"*24
    assert "file" not in page["models"][0]
    assert page["facets"]["vehicle_years"] == [1966]
    assert page["facets"]["release_years"] == [2021, 2015]
    with pytest.raises(ValueError):
        library.listing(parse_qs("page=abc"))
    with pytest.raises(ValueError):
        library.listing(parse_qs("year=2015"))


def test_preview_preserves_nested_matrices_and_embedded_geometry(library, tmp_path):
    parser = ArchiveGeometry(library.file("a"*24), library.parts)
    name, color, matrix = parser.instances()[0]
    assert name == "window.dat" and color == 4
    assert matrix[:3, 3].tolist() == [15, 20, 30]
    target = tmp_path / "preview.glb"
    report = parser.export(target)
    assert report["missing_geometry"] == []
    scene = trimesh.load(target, force="scene")
    points = scene.to_geometry().vertices
    assert np.allclose(points.min(axis=0), [15, -30, -30])
    assert np.allclose(points.max(axis=0), [15, -20, -20])


def test_missing_geometry_is_reported_and_paths_cannot_escape(library, tmp_path):
    parser = ArchiveGeometry(library.file("a"*24), library.parts)
    parser.sections["window.dat"].append("1 16 0 0 0 1 0 0 0 1 0 0 0 1 missing.dat")
    report = parser.export(tmp_path / "partial.glb")
    assert report["missing_geometry"] == ["missing.dat"]
    with pytest.raises(FileNotFoundError):
        library.file("../model.io")
    catalog = json.loads(library.catalog_path.read_text(encoding="utf-8"))
    catalog["models"][0]["file"] = "../outside.io"
    write_json(library.catalog_path, catalog)
    with pytest.raises(FileNotFoundError):
        library.file("a"*24)


def test_legacy_color_spacing_and_high_resolution_primitive(library, tmp_path):
    (library.parts / "p/48").mkdir(parents=True)
    (library.parts / "p/48/1-12cyli.dat").write_text("3 16 0 0 0 1 0 0 0 1 0\n")
    (library.parts / "LDConfig.ldr").write_text("0 !COLOUR Red     CODE   4   VALUE #C91A09   EDGE #333333\n")
    parser = ArchiveGeometry(library.file("a"*24), library.parts)
    parser.sections["detail.dat"] = ["1 16 0 0 0 1 0 0 0 1 0 0 0 1 1-12cyli.dat"]
    report = parser.export(tmp_path / "red.glb")
    assert report["primitive_aliases"] == {"1-12cyli.dat": "48/1-12cyli.dat"}
    assert report["missing_geometry"] == []
    scene = trimesh.load(tmp_path / "red.glb", force="scene")
    mesh = next(iter(scene.geometry.values()))
    assert mesh.visual.material.baseColorFactor.tolist() == [149, 3, 1, 255]


def test_synthesized_surfaces_in_ldr_sections_are_not_discarded(library, tmp_path):
    parser = ArchiveGeometry(library.file("a"*24), library.parts)
    parser.sections["door left.ldr"].append("3 4 0 0 0 1 0 0 0 1 0")
    report = parser.export(tmp_path / "flexible.glb")
    scene = trimesh.load(tmp_path / "flexible.glb", force="scene")
    assert sum(len(mesh.faces) for mesh in scene.geometry.values()) == 2
    assert report["missing_geometry"] == []


def test_download_utf8_headers_bytes_and_http_catalog(library, monkeypatch, tmp_path):
    monkeypatch.setattr("car2lego3d.official_library.official_library", lambda: library)
    store = DesktopStore(JobStore(tmp_path / "jobs", None))
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, jobs=store.jobs, desktop=store, directory=str(ROOT / "frontend/public")))
    thread = Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)
    try:
        connection.request("GET", "/api/v1/sets/library?brand=ferrari&type=sports_car")
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200 and body["total"] == 2
        connection.request("POST", "/api/v1/designs", body=json.dumps(dict(make="Ferrari", model="F40")),
                           headers={"Origin": f"http://127.0.0.1:{server.server_port}", "Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())["library_match"]["total"] == 1
        connection.request("GET", "/api/v1/sets/library/" + "a"*24 + "/download")
        response = connection.getresponse()
        assert response.status == 200
        assert unquote(response.getheader("Content-Disposition").split("filename*=UTF-8''")[1]) == library.file("a"*24).name
        assert response.read() == library.file("a"*24).read_bytes()
        connection.request("GET", "/api/v1/sets/library/" + "a"*24 + "/preview")
        response = connection.getresponse()
        assert response.status == 202 and json.loads(response.read())["status"] == "processing"
        library.jobs["a"*24].result(timeout=15)
        state = library.preview("a"*24)
        assert state["status"] == "ready"
        connection.request("GET", state["model_url"])
        response = connection.getresponse()
        assert response.status == 200 and response.read()[:4] == b"glTF"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
        library.pool.shutdown()
