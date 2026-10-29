"""Audit target vehicle family/model/module overlap without changing training data."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3

from common import file_hash, read_json, write_json


PATTERNS = {
    "countach": r"countach|康塔什|康塔奇",
    "f40": r"(?<![a-z0-9])f\s*40(?![0-9])",
    "bmw_m4": r"(?<![a-z0-9])m\s*4(?![0-9])",
    "lotus_for_me": r"\bfor\s*me\b|福米",
}


def inspect_isolation(dataset, corpus, snapshot, output):
    dataset, corpus = Path(dataset).resolve(), Path(corpus).resolve()
    database = corpus / "snapshots" / snapshot / "corpus.sqlite3"
    manifest = read_json(database.parent / "manifest.json")
    if manifest["snapshot"] != snapshot or file_hash(database) != manifest["database_sha256"]:
        raise ValueError("Frozen corpus integrity failed")
    rows = {split: [json.loads(line) for line in (dataset / (split + ".jsonl")).read_text(encoding="utf-8").splitlines()
                   if line.strip()] for split in ("train", "validation", "test")}
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        models = [dict(row, metadata=json.loads(row["metadata"])) for row in db.execute("SELECT id,family,metadata FROM models")]
        modules = [dict(row) for row in db.execute("SELECT model_id,hash FROM modules")]
    finally:
        db.close()
    targets = {}
    for case, pattern in PATTERNS.items():
        matched = [model for model in models if re.search(pattern,
            model["metadata"].get("name_zh", "") + " " + model["metadata"].get("name", ""), re.I)]
        families = {model["family"] for model in matched}
        family_models = [model for model in models if model["family"] in families]
        identities = {model["id"] for model in family_models}
        hashes = {module["hash"] for module in modules if module["model_id"] in identities}
        partitions = {}
        for split, samples in rows.items():
            overlaps = []
            for row in samples:
                source = row["source"]
                source_families = set(source.get("families", [source["family"]]))
                shared_hashes = hashes & set(source["module_hashes"])
                reasons = []
                if families & source_families:
                    reasons.append("family")
                if source["model_id"] in identities:
                    reasons.append("model")
                if shared_hashes:
                    reasons.append("module_hash")
                if reasons:
                    overlaps.append(dict(model_id=source["model_id"], families=sorted(source_families),
                        module_hashes=source["module_hashes"], reasons=reasons))
            unique = {json.dumps(row, sort_keys=True) for row in overlaps}
            partitions[split] = dict(overlapping_samples=len(overlaps),
                reason_counts=dict(Counter(reason for row in overlaps for reason in row["reasons"])),
                sources=[json.loads(row) for row in sorted(unique)])
        targets[case] = dict(name_pattern=pattern, families=sorted(families),
            matching_models=[dict(id=model["id"], family=model["family"],
                set_num=model["metadata"].get("set_num"), name_zh=model["metadata"].get("name_zh")) for model in matched],
            related_family_models=len(family_models), target_module_hashes=len(hashes), partitions=partitions,
            training_isolated=bool(matched) and partitions["train"]["overlapping_samples"] == 0,
            status="no_name_matching_corpus_source" if not matched else "overlap" if partitions["train"]["overlapping_samples"] else "family_model_module_isolated")
    result = dict(schema="assembly-core-vehicle-training-isolation.v1", snapshot=snapshot,
        database_sha256=file_hash(database), dataset_path=str(dataset),
        partition_hashes={split: file_hash(dataset / (split + ".jsonl")) for split in rows},
        cases=targets, training_changed=False, quality_accepted=False,
        limitations=["Name matching can miss aliases; no match is not proof of complete source isolation",
            "An overlapping target family cannot be claimed as held-out whole-car generalization",
            "Future isolation changes require a new dataset/adapter, never relabeling this completed run"])
    write_json(output, result)
    print(json.dumps({case: dict(status=value["status"], families=value["families"],
        train_overlapping_samples=value["partitions"]["train"]["overlapping_samples"],
        matching_names=[model["name_zh"] for model in value["matching_models"]]) for case, value in targets.items()}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inspect_isolation(args.dataset, args.corpus, args.snapshot, args.output)
