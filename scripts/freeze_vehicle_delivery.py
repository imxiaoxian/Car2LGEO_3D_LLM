"""Freeze read-only source identities and audit existing training target isolation."""
import argparse
from pathlib import Path

from car2lego3d.assembly_corpus import Corpus
from car2lego3d.common import write_json
from car2lego3d.delivery_targets import freeze_sources, training_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("D:/lego/result"))
    parser.add_argument("--corpus", type=Path, default=Path("data/assembly-corpus"))
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be a new directory to preserve historical evidence")
    targets = freeze_sources(args.source)
    corpus = Corpus(args.corpus, snapshot=args.snapshot)
    try:
        audit = training_audit(corpus, sorted(args.datasets.glob("*.jsonl")))
        write_json(args.output / "targets.json", targets)
        write_json(args.output / "training-isolation.json", audit)
        print({"targets": len(targets["targets"]), "datasets": len(audit["datasets"]),
            "comparison_eligible": audit["current_comparison_eligible"],
            "samples_with_conflicts": sum(len(r["conflicts"]) for r in audit["datasets"]),
            "stale_protocol_samples": sum(len(r["stale_protocol"]) for r in audit["datasets"])})
    finally:
        corpus.db.close()


if __name__ == "__main__":
    main()
