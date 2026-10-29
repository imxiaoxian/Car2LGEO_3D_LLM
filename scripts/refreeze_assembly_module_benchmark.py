"""Re-execute previously held-out source modules against a new frozen executor."""
import argparse
import json
from pathlib import Path

from car2lego3d.assembly_agent import AssemblyAgent
from car2lego3d.assembly_benchmark import freeze_suite, read
from car2lego3d.assembly_corpus import Corpus
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.assembly_training_data import replay_run
from car2lego3d.common import digest, write_json
from build_assembly_demonstrations import SourceStepPlanner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-suite", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=Path("data/assembly-corpus"))
    parser.add_argument("--native-root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory; prior evidence is immutable")
    previous = read(args.previous_suite)
    corpus = Corpus(args.corpus, snapshot=previous["snapshot"])
    dataset = args.output / "dataset"
    dataset.mkdir(parents=True)
    rows = []
    try:
        for task in previous["tasks"]:
            for path, expected in previous["source_files"].items():
                if digest(Path(path)) != expected:
                    raise ValueError("Previous source file changed: " + path)
            if len(task["modules"]) != 1:
                raise ValueError("Refreeze currently requires one reviewed source module per task")
            mission = task["mission"]
            source = args.output / "sources" / task["id"]
            excluded = task["exclusions"]
            tools = AssemblyTools(source, args.native_root, corpus, exclude_models=excluded["models"],
                exclude_families=excluded["families"], exclude_hashes=excluded["hashes"])
            operations = mission["semantic"]["operations"]
            state = AssemblyAgent(tools, SourceStepPlanner(operations), mission,
                max_steps=previous["budgets"]["max_steps"], max_seconds=previous["budgets"]["max_seconds"]).run()
            if state.get("stop_reason") != "planner_finished" or state.get("digital_status") != "pass":
                raise ValueError("Source demonstration did not finish with passing structure")
            rows.extend(replay_run(dict(directory=str(source), source_modules=task["modules"]),
                                   corpus, args.native_root, dataset))
        for split in ("train", "validation", "test"):
            (dataset / f"{split}.jsonl").write_text("".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in (rows if split == "test" else [])),
                encoding="utf-8")
        suite = freeze_suite(dataset, corpus, args.native_root, args.output / "suite",
            max_steps=previous["budgets"]["max_steps"], max_seconds=previous["budgets"]["max_seconds"])
        write_json(args.output / "refreeze.json", dict(previous_suite=str(args.previous_suite.resolve()),
            previous_sha256=digest(args.previous_suite), tasks=len(suite["tasks"]),
            scope="Executed held-out source demonstrations only; no model or whole-car acceptance"))
        print(json.dumps(dict(suite=str(args.output / "suite/suite.json"), tasks=len(suite["tasks"]))))
    finally:
        corpus.db.close()


if __name__ == "__main__":
    main()
