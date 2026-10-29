"""Test a parent-local joint correction without modifying the source run."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from car2lego3d.assembly_catalog import NativeCatalog, validate
from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_motion import evaluate_motion
from car2lego3d.common import digest, write_json


def run(args):
    if args.output.exists():
        raise ValueError("Use a new output directory")
    state_path = args.source / "state.json"
    source_hash = digest(state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assembly = Assembly(state["text"], state["source_sha256"])
    assembly.revision, assembly.joints = state["revision"], state["joints"]
    graph = assembly.graph()
    original = deepcopy(graph["joints"][args.instance])
    graph["joints"] = {args.instance: {**original, "origin": args.origin, "axis": args.axis}}
    catalog = NativeCatalog(args.native, [args.source / p for p in state["packages"]])
    report = evaluate_motion(graph, lambda posed: validate(posed, catalog), samples=9)
    if digest(state_path) != source_hash:
        raise ValueError("Live source changed during experiment; retry against a stable snapshot")
    write_json(args.output / "motion.json", report)
    summary = dict(source_sha256=source_hash, source_revision=state["revision"],
        original_joint=original, proposed_joint=graph["joints"][args.instance],
        status=report["status"], joints=[dict(instance=j["instance"],
            poses=len(j["poses"]), failed=sum(p["status"] != "pass" for p in j["poses"]))
            for j in report["joints"]],
        scope="Isolated engineering hypothesis; no source edits, planner credit or vehicle acceptance.")
    write_json(args.output / "diagnosis.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--origin", nargs=3, type=float, required=True)
    parser.add_argument("--axis", nargs=3, type=float, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False))
