"""Recheck independent planner outputs against their frozen assembly suite."""
import argparse
from pathlib import Path

from car2lego3d.assembly_benchmark import compare_results, read
from car2lego3d.assembly_corpus import Corpus
from car2lego3d.common import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=Path("data/assembly-corpus"))
    parser.add_argument("--native-root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    args = parser.parse_args()
    target = args.round / "comparison.json"
    if target.exists():
        parser.error("Comparison already exists; retained evidence is immutable")
    suite = read(args.round / "suite/suite.json")
    results = [read(args.round / name / "benchmark-result.json")
               for name in ("codex", "local_base", "local_finetuned")]
    corpus = Corpus(args.corpus, snapshot=suite["snapshot"])
    try:
        report = compare_results(suite, results, corpus, args.native_root)
        write_json(target, report)
        print({"report": str(target), "winner": report["winner"],
               "execution_pass": {row["planner"]: row["metrics"]["execution_pass"] for row in results}})
    finally:
        corpus.db.close()


if __name__ == "__main__":
    main()
