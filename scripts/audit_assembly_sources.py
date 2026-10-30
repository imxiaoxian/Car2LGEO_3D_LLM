"""Bounded, reproducible audits of real community modules; no source writes."""
import argparse
from collections import Counter
import json
from pathlib import Path

from car2lego3d.assembly_corpus import Corpus
from car2lego3d.common import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("data/assembly-corpus"))
    parser.add_argument("--source-root", type=Path, default=Path("D:/lego/result"))
    parser.add_argument("--native-root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--max-parts", type=int, default=12)
    parser.add_argument("--min-parts", type=int, default=3)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--provider", default="LDraw OMR")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000 or not 3 <= args.min_parts <= args.max_parts <= 100:
        parser.error("Use a bounded audit: 1..1000 modules, 3 <= min-parts <= max-parts <= 100")
    corpus = Corpus(args.corpus)
    results = []
    try:
        models = {r["id"]: json.loads(r["metadata"]) for r in corpus.db.execute("SELECT id,metadata FROM models")}
        candidates = corpus.db.execute("SELECT * FROM modules WHERE part_count BETWEEN ? AND ? ORDER BY part_count,id",
            (args.min_parts, args.max_parts)).fetchall()
        seen = set()
        for row in candidates:
            meta, source = json.loads(row["metadata"]), models[row["model_id"]]
            if (row["hash"] in seen or source.get("provider") != args.provider
                    or source.get("license_status") != "declared" or meta.get("source_review_status") == "approved"):
                continue
            seen.add(row["hash"])
            try:
                audit = corpus.review_source(row["id"], args.source_root, args.native_root,
                    reviewer="automated-source-audit.v1",
                    notes="Archive and exact module identity, declared license, native digital connections and collisions. No semantic or whole-car acceptance.")
                report = audit["structure"]
                item = dict(module=row["id"], status=audit["status"], structure=report["status"],
                    unknown=len(report["unknown"]), collisions=len(report["collisions"]), components=len(report["components"]))
            except (ValueError, OSError) as exc:
                item = dict(module=row["id"], status="error", error=str(exc))
            results.append(item)
            print(json.dumps(item), flush=True)
            if len(results) >= args.limit:
                break
        write_json(args.output, dict(schema="assembly-source-audit-batch.v1", results=results,
            statuses=dict(Counter(r["status"] for r in results)), corpus=corpus.summary()))
    finally:
        corpus.db.close()


if __name__ == "__main__":
    main()
