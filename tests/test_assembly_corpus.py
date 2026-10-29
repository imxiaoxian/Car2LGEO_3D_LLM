import json
from pathlib import Path

import pytest
from zipfile import ZipFile

from car2lego3d.assembly_corpus import Corpus, canonical_module, dependency_hashes
from car2lego3d.assembly_graph import Assembly


def test_hash_covers_embedded_geometry_and_all_sidecars():
    a = Assembly("0 FILE a.ldr\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 glass.dat\n0 FILE glass.dat\n3 16 0 0 0 10 0 0 0 10 0\n")
    b = Assembly(a.text().replace("10 0 0", "11 0 0"))
    assert canonical_module(a, a.root) != canonical_module(b, b.root)
    manifest = [dict(path="parts/a.dat", sha256="geometry"), dict(path="parts/a.conn", sha256="ports"),
                dict(path="parts/s/a.dat", sha256="subpart")]
    before = dependency_hashes(manifest)
    assert set(before) == {"a.dat", "s/a.dat"}
    manifest[1]["sha256"] = "changed"
    assert dependency_hashes(manifest)["a.dat"] != before["a.dat"]


def test_frozen_database_is_immutable_and_hash_checked(tmp_path):
    corpus = Corpus(tmp_path)
    frozen = corpus.freeze()
    reader = Corpus(tmp_path, snapshot=frozen["snapshot"])
    assert reader.snapshot() == frozen["snapshot"]
    with pytest.raises(Exception, match="readonly"):
        reader.db.execute("DELETE FROM models")
    reader.db.close()
    path = Path(frozen["database"])
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        Corpus(tmp_path, snapshot=frozen["snapshot"])


def fixture_corpus(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    brick = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
    texts = ["0 FILE leaf.ldr\n" + brick,
             "0 FILE parent.ldr\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 leaf.ldr\n0 FILE leaf.ldr\n" + brick]
    rows = []
    for i, text in enumerate(texts):
        filename = f"{i}.io"
        with ZipFile(source / filename, "w") as archive:
            archive.writestr("model.ldr", "0 !LICENSE Redistributable under CCAL version 2.0 : see CAreadme.txt\n" + text)
        rows.append(dict(id=str(i), file=filename, name_zh="Fixture", set_num=str(1000 + i),
            author="Fixture author", source_url="https://example.test/model", vehicle_types=[]))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(dict(models=rows)), encoding="utf-8")
    corpus = Corpus(tmp_path / "corpus")
    corpus.index(catalog, source)
    return corpus, source


def test_index_digest_matches_disk_and_cache_migration_rejects_corruption(tmp_path):
    corpus, _ = fixture_corpus(tmp_path)
    item = corpus.retrieve(limit=1)[0]
    item = corpus.module(item["id"])
    path = corpus.directory / item["metadata"]["file"]
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="content differs"):
        corpus.module(item["id"])
    assert corpus.repair_cache_newlines()["repaired"] == 1
    assert corpus.module(item["id"])
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="not a newline"):
        corpus.repair_cache_newlines()
    corpus.db.close()


def test_heldout_duplicate_and_parent_cannot_be_grafted(tmp_path):
    corpus, _ = fixture_corpus(tmp_path)
    leaf = corpus.db.execute("SELECT * FROM modules WHERE model_id='0'").fetchone()
    excluded = corpus.exclusions(exclude_families=["1000"])
    assert corpus.retrieve(exclude_families=["1000"]) == []
    for row in corpus.db.execute("SELECT id FROM modules"):
        assert not corpus.allowed(corpus.module(row["id"]), excluded)
    assert corpus.retrieve(exclude_hashes=[leaf["hash"]]) == []
    corpus.db.close()


def test_module_inspection_resolves_native_bounds_without_mutating_graph(tmp_path):
    import os
    from car2lego3d.assembly_tools import AssemblyTools
    native = Path(os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    corpus, _ = fixture_corpus(tmp_path)
    try:
        identity = corpus.retrieve(limit=1)[0]["id"]
        tools = AssemblyTools(tmp_path / "working", native, corpus)
        result = tools.call("query_module", dict(module_id=identity))
        assert result["ok"], result
        assert result["result"]["bounds_ldu"] and result["result"]["part_count"] == 1
        assert result["result"]["source_check"] == "unverified"
        assert tools.assembly.revision == 0 and not tools.assembly.bom()
        assert tools.call("context", dict(resource=result["result"]["resource"], pointer="/instances/0/id"))["ok"]
        excluded = AssemblyTools(tmp_path / "excluded", native, corpus, exclude_families=["1000"])
        assert not excluded.call("query_module", dict(module_id=identity))["ok"]
    finally:
        corpus.db.close()


def test_source_audit_is_bound_to_archive_module_and_evidence(tmp_path):
    import os
    native = Path(os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native catalog not installed")
    corpus, source = fixture_corpus(tmp_path)
    identity = corpus.db.execute("SELECT id FROM modules WHERE model_id='0'").fetchone()[0]
    result = corpus.review_source(identity, source, native, reviewer="test", notes="Constructed fixture only")
    assert result["status"] == "approved" and not result["official_structure_verified"]
    item = corpus.module(identity)
    assert item["metadata"]["training_eligible"]
    evidence = corpus.directory / item["metadata"]["source_review"]["file"]
    evidence.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="review evidence"):
        corpus.module(identity)
    corpus.db.close()


def test_nonmatching_query_does_not_return_unrelated_modules(tmp_path):
    corpus, _ = fixture_corpus(tmp_path)
    assert corpus.retrieve(query="Lamborghini Countach") == []
    assert corpus.retrieve(query="   ")
    corpus.db.close()


def test_retrieval_matches_brand_type_and_complete_model_tokens(tmp_path):
    corpus, _ = fixture_corpus(tmp_path)
    for row in corpus.db.execute("SELECT id,metadata FROM modules").fetchall():
        meta = json.loads(row["metadata"])
        meta.update(name_zh="BMW M40", brands=["bmw"], types=["coupe"])
        corpus.db.execute("UPDATE modules SET metadata=? WHERE id=?", (json.dumps(meta), row["id"]))
    corpus.db.commit()
    assert corpus.retrieve(query="M4") == []
    for query in ("BMW", "coupe", "M40", "BMW BMW"):
        items = corpus.retrieve(query=query)
        assert items and all(item["retrieval"]["score"] == 1 for item in items)
        assert all(item["retrieval"]["matched_terms"] == [query.split()[0].casefold()] for item in items)
    assert corpus.retrieve(query="BMW", types=["suv"]) == []
    corpus.db.close()


def test_chinese_phrase_query_matches_vehicle_name(tmp_path):
    corpus, _ = fixture_corpus(tmp_path)
    for row in corpus.db.execute("SELECT id,metadata FROM modules").fetchall():
        meta = json.loads(row["metadata"])
        meta["name_zh"] = "奥迪 RS Q e-tron 拉力赛车"
        corpus.db.execute("UPDATE modules SET metadata=? WHERE id=?", (json.dumps(meta), row["id"]))
    corpus.db.commit()
    assert corpus.retrieve(query="拉力")
    assert corpus.retrieve(query="e-tron")
    assert corpus.retrieve(query="法拉利") == []
    corpus.db.close()


def test_part_count_range_can_reach_larger_candidates_and_keeps_exclusions(tmp_path):
    corpus, _ = fixture_corpus(tmp_path)
    row = corpus.db.execute("SELECT * FROM modules WHERE model_id='0'").fetchone()
    corpus.db.execute("INSERT INTO modules VALUES(?,?,?,?,?,?,?,?)", (
        "large", row["model_id"], "different-hash", "chassis.ldr", 80,
        row["metadata"], "unverified", None))
    corpus.db.commit()
    items = corpus.retrieve(min_parts=30, max_parts=100)
    assert [item["id"] for item in items] == ["large"]
    assert corpus.retrieve(min_parts=30, max_parts=100, exclude_families=["1000"]) == []
    assert corpus.retrieve(min_parts=30, max_parts=100, exclude_hashes=["different-hash"]) == []
    assert corpus.retrieve(min_parts=30, max_parts=100, verified_only=True) == []
    with pytest.raises(ValueError, match="limits"):
        corpus.retrieve(min_parts=100, max_parts=30)
    corpus.db.close()


def test_empty_retrieval_reports_invalid_category_without_relaxing_filters(tmp_path):
    from car2lego3d.assembly_tools import AssemblyTools
    corpus, _ = fixture_corpus(tmp_path)
    for row in corpus.db.execute("SELECT id,metadata FROM modules").fetchall():
        metadata = json.loads(row["metadata"])
        metadata["types"] = ["sports_car"]
        corpus.db.execute("UPDATE modules SET metadata=? WHERE id=?", (json.dumps(metadata), row["id"]))
    corpus.db.commit()
    tools = AssemblyTools(tmp_path / "agent", tmp_path / "native", corpus)
    query = dict(query="Fixture", types=["module"], limit=3)
    result = tools.call("retrieve", query)["result"]
    assert result["modules"] == [] and result["filters"] == query
    diagnostic = result["diagnostics"]
    assert diagnostic["reason"] == "unknown_vehicle_types"
    assert diagnostic["unknown_types"] == ["module"]
    assert diagnostic["available_types"] == ["sports_car"]
    assert not diagnostic["filters_relaxed"]
    assert tools.call("retrieve", dict(query="Fixture", types=["sports_car"]))["result"]["modules"]
    held_out = AssemblyTools(tmp_path / "held-out", tmp_path / "native", corpus,
                             exclude_families=["1000"])
    allowed_query = dict(query="Fixture", types=["sports_car"])
    empty = held_out.call("retrieve", allowed_query)["result"]
    assert empty["modules"] == []
    assert empty["diagnostics"]["reason"] == "no_matching_modules"
    assert empty["diagnostics"]["exclusions_unchanged"]
    assert empty["filters"] == allowed_query
    assert held_out.call("retrieve", {})["result"]["modules"] == []
    corpus.db.close()
