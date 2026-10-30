"""Execute source assembly steps; these are demonstrations, not planner results."""
import argparse
import json
from pathlib import Path

from car2lego3d.assembly_agent import AssemblyAgent, wire_json
from car2lego3d.assembly_corpus import Corpus
from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.assembly_training_data import build
from car2lego3d.common import write_json


class SourceStepPlanner:
    name = "corpus_demonstration"

    def __init__(self, operations):
        self.decisions = []
        for index, operation in enumerate(operations):
            self.decisions.append(("context", dict(resource="semantic", pointer=f"/operations/{index}")))
            self.decisions.append(("edit", dict(revision=index, operations=[operation])))
        self.decisions.extend([("check", {}), ("export", {}), ("finish", {})])

    def propose(self, payload, step):
        tool, arguments = self.decisions[step]
        return dict(tool=tool, arguments_json=wire_json(arguments), reason="Execute a reviewed community source step; not a model proposal.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("data/assembly-corpus"))
    parser.add_argument("--native-root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-parts", type=int, default=1)
    parser.add_argument("--exclude-delivery-targets", action="store_true")
    args = parser.parse_args()
    if args.limit < 1 or args.min_parts < 1:
        parser.error("limit and min-parts must be positive")
    if args.output.exists():
        parser.error("Use a new output directory")
    corpus = Corpus(args.corpus, snapshot=args.snapshot)
    if not args.snapshot:
        snapshot = corpus.freeze()["snapshot"]
        corpus.db.close()
        corpus = Corpus(args.corpus, snapshot=snapshot)
    manifest, runs = [], []
    try:
        exclusions = dict(models=set(), families=set(), hashes=set())
        if args.exclude_delivery_targets:
            from car2lego3d.delivery_targets import TARGETS
            from car2lego3d.assembly_website import vehicle_exclusions
            for target in TARGETS.values():
                for key, values in vehicle_exclusions(corpus, target).items():
                    exclusions[key].update(values)
        candidates = [r["id"] for r in corpus.db.execute(
            "SELECT id,metadata FROM modules WHERE part_count >= ? ORDER BY id", (args.min_parts,))
            if json.loads(r["metadata"]).get("training_eligible")
            and corpus.allowed(corpus.module(r["id"]), exclusions)][:args.limit]
        for identity in candidates:
            item = corpus.module(identity)
            assembly = Assembly.read(corpus.directory / item["metadata"]["file"])
            nodes = [n for n in assembly.graph()["instances"] if n["kind"] == "part"]
            operations = [dict(op="add", part=n["part"], color=n["color"], matrix=n["world"])
                          for n in sorted(nodes, key=lambda n: (n["step"], n["id"]))]
            directory = args.output / "runs" / identity
            tools = AssemblyTools(directory, args.native_root, corpus, exclude_models=exclusions["models"],
                exclude_families=exclusions["families"], exclude_hashes=exclusions["hashes"])
            mission = dict(request=dict(goal="Reconstruct the reviewed source module from its ordered native part placements.", module=identity),
                semantic=dict(operations=operations, source_module=identity, origin="reviewed_community_source", mechanism_inference="unverified"))
            state = AssemblyAgent(tools, SourceStepPlanner(operations), mission, max_steps=len(operations)*2+3).run()
            result = dict(module=identity, status=state["status"], digital=state["digital_status"], steps=state["steps"])
            print(json.dumps(result), flush=True)
            runs.append(result)
            if state["digital_status"] == "pass":
                manifest.append(dict(directory=str(directory.resolve()), source_modules=[identity]))
        write_json(args.output / "runs.json", manifest)
        write_json(args.output / "demonstrations.json", dict(origin="executed_source_step_demonstrations", snapshot=corpus.snapshot(), runs=runs))
        report = build(manifest, corpus, args.native_root, args.output / "dataset")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        corpus.db.close()


if __name__ == "__main__":
    main()
