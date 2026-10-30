import json

import pytest

from car2lego3d.assembly_corpus import Corpus
from car2lego3d.assembly_website import vehicle_exclusions


@pytest.mark.parametrize("brand,model,source,neighbor", [
    ("宝马", "M4 Competition Coupe", "BMW M4 GT3 EVO", "BMW M40"),
    ("日产", "Skyline GT-R (R34)", "《速度与激情2》日产 Skyline GT-R (R34) 跑车 [42210-1]", "Skyline GT-R (R35)"),
    ("布加迪", "Chiron Pur Sport", "布加迪 Chiron Pur Sport 超级跑车 [42222-1]", "Chiron Sport")])
def test_fresh_website_creation_excludes_target_family_and_duplicate_modules(tmp_path, brand, model, source, neighbor):
    corpus = Corpus(tmp_path)
    for identity, family, title in [("target", "target-family", source),
            ("same-family", "target-family", "Alternative archive"), ("unrelated", "other-family", neighbor)]:
        metadata = dict(original_name=title, brands=[dict(zh=brand)])
        corpus.db.execute("INSERT INTO models VALUES(?,?,?,?,?,?)", (identity, identity, family, json.dumps(metadata), "parsed", None))
    corpus.db.execute("INSERT INTO modules(id,model_id,hash,section,part_count,metadata,check_status) VALUES(?,?,?,?,?,?,?)",
        ("target-module", "target", "target-module-hash", "wheel.ldr", 2, "{}", "unverified"))
    corpus.db.commit()
    result = vehicle_exclusions(corpus, dict(brand=brand, model=model))
    assert result == dict(models=["same-family", "target"], families=["target-family"], hashes=["target-module-hash"])
    assert vehicle_exclusions(corpus, dict(brand="Lotus", model="For Me")) == dict(models=[], families=[], hashes=[])
    corpus.db.close()


def test_other_vehicles_do_not_inherit_a_core_vehicle_exclusion(tmp_path):
    corpus = Corpus(tmp_path)
    assert vehicle_exclusions(corpus, dict(brand="BMW", model="M40")) == dict(models=[], families=[], hashes=[])
    corpus.db.close()
