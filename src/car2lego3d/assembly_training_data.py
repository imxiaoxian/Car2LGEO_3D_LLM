"""Build leakage-isolated samples from replayed, source-reviewed agent actions."""
from __future__ import annotations

import argparse
from collections import Counter, deque
from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import numpy as np

from .assembly_agent import AssemblyAgent, execution_hash, parse_decision, wire_json
from .assembly_catalog import NativeCatalog, validate
from .assembly_corpus import Corpus, canonical_module, dependency_hashes
from .assembly_graph import Assembly
from .assembly_protocol import protocol_hash
from .assembly_tools import AssemblyTools
from .common import digest, write_json

MUTATIONS = {"edit", "graft", "snap", "attach", "build"}


def part_signatures(graph, matrix=None, inherited=16):
    frame = np.eye(4) if matrix is None else np.asarray(matrix)
    return Counter(wire_json([n["part"], inherited if n["color"] == 16 else n["color"],
                   np.round(frame @ np.asarray(n["world"]), 6).tolist()])
                   for n in graph["instances"] if n["kind"] == "part")


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Evidence path escapes run")
    return path


def grouped_splits(rows, seed=42):
    """Connected family/module components never cross dataset partitions."""
    parents = list(range(len(rows)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    owners = {}
    for i, row in enumerate(rows):
        source = row["source"]
        keys = ["family:" + f for f in source.get("families", [source["family"]])]
        keys += ["module:" + h for h in source["module_hashes"]]
        for key in keys:
            if key in owners:
                parents[find(i)] = find(owners[key])
            else:
                owners[key] = i
    groups = {}
    for i in range(len(rows)):
        groups.setdefault(find(i), []).append(i)
    result = dict(train=[], validation=[], test=[])
    components = []
    for indices in groups.values():
        identity = sorted({k for i in indices for k in rows[i]["source"]["module_hashes"]})
        token = sha256(wire_json([seed, identity]).encode()).hexdigest()
        bucket = int(token[:8], 16) % 10
        split = "train" if bucket < 8 else "validation" if bucket == 8 else "test"
        result[split].extend(rows[i] for i in indices)
        components.append(dict(hash=token, split=split, samples=len(indices)))
    return result, components


def replay_run(item, corpus, native_root, output):
    """Re-execute decisions; saved success booleans alone cannot create samples."""
    directory = Path(item["directory"]).resolve()
    modules = [corpus.module(m) for m in item.get("source_modules", [])]
    if not modules or any(m["check_status"] != "pass" or
            m["metadata"].get("source_review_status") != "approved" or
            not m["metadata"].get("training_eligible") for m in modules):
        raise ValueError("No source-approved, structure-passing modules")
    state_path, trajectory = directory / "agent.json", directory / "trajectory.jsonl"
    trace = directory / "agent-decisions.jsonl"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("schema") == "assembly-scheduled-agent.v1":
        raise ValueError("Scheduled vehicle traces need scoped module replay and independent source approval; aggregated traces are not training data")
    contract = state["contract"]
    if (contract["snapshot"] != corpus.snapshot() or contract["protocol_hash"] != protocol_hash()
            or contract["execution_hash"] != execution_hash()):
        raise ValueError("Run uses a different snapshot, protocol or execution implementation")
    initial_path = directory / "initial-assembly.json"
    if digest(initial_path) != contract["initial_assembly_sha256"]:
        raise ValueError("Initial assembly hash differs")
    if state["input_hash"] != sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest():
        raise ValueError("Run input contract differs")
    records = [json.loads(s) for s in trace.read_text(encoding="utf-8").splitlines() if s.strip()]
    executed = [json.loads(s) for s in trajectory.read_text(encoding="utf-8").splitlines() if s.strip()]
    source_graphs = [Assembly.read(inside(corpus.directory, m["metadata"]["file"])).graph() for m in modules]
    reviewed = {m["id"]: g for m, g in zip(modules, source_graphs)}
    hashes = sorted({m["hash"] for m in modules})
    families = sorted({corpus.db.execute("SELECT family FROM models WHERE id=?", (m["model_id"],)).fetchone()[0] for m in modules})
    # Include actual descendants, not unrelated siblings from the same source car.
    for module in modules:
        source = json.loads(corpus.db.execute("SELECT metadata FROM models WHERE id=?", (module["model_id"],)).fetchone()[0])
        dependencies = dependency_hashes(source.get("package_manifest", []))
        assembly = Assembly.read(inside(corpus.directory, module["metadata"]["file"]))
        hashes.extend(canonical_module(assembly, name, dependencies=dependencies)
                      for name in assembly.sections if assembly.is_module(name))
    hashes = sorted(set(hashes))
    exclusions = contract["exclusions"]
    samples = []
    with TemporaryDirectory(prefix="assembly-replay-") as temporary:
        tools = AssemblyTools(temporary, native_root, corpus, exclude_models=exclusions["models"],
            exclude_families=exclusions["families"], exclude_hashes=exclusions["hashes"])
        initial = json.loads(initial_path.read_text(encoding="utf-8"))
        tools.assembly = Assembly(initial["text"], initial["source_sha256"])
        tools.assembly.revision, tools.assembly.joints = initial["revision"], initial["joints"]
        signatures = part_signatures(tools.assembly.graph())
        approved = Counter()
        for graph in source_graphs:
            approved.update(part_signatures(graph))
        if signatures:
            matches = [part_signatures(g) for g in source_graphs if part_signatures(g) == signatures]
            if not matches:
                raise ValueError("Initial assembly is unrelated to the reviewed sources")
        packages = []
        for index, relative in enumerate(initial["packages"]):
            source = inside(directory, relative)
            destination = tools.directory / "packages" / str(index)
            for path in source.rglob("*"):
                inside(source, path.relative_to(source))
                if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                    raise ValueError("Linked dependency directories are not replayable")
            shutil.copytree(source, destination)
            packages.append(destination)
        tools.catalog = NativeCatalog(native_root, packages)
        if {name: sha256(data).hexdigest() for name, data in tools.dependencies().items()} != initial["dependencies"]:
            raise ValueError("Initial dependency hashes differ")
        backend = type("ReplayPlanner", (), {"name": state["planner"]})()
        agent = AssemblyAgent(tools, backend, contract["mission"])
        history = deque(maxlen=8)
        cursor = 0
        for record in records:
            if record["input_hash"] != state["input_hash"] or record["planner"] != state["planner"]:
                raise ValueError("Decision belongs to a different run")
            decision = record["decision"]
            input_path = inside(directory, record["input_file"])
            if digest(input_path) != record["input_sha256"]:
                raise ValueError("Actual planner input file differs")
            input_text, decision_text = record["input_text"], record["decision_text"]
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            if (input_text != wire_json(payload) or decision_text != wire_json(decision)
                    or payload != agent.context(history, payload["remaining_steps"])):
                raise ValueError("Exact planner input/output differs from replayed context")
            # Recreate the host's resource/notes state as well as assembly state.
            observation = record["observation"]
            if decision["tool"] not in {"finish", "planner_error"}:
                name, arguments = parse_decision(decision)
                if observation.get("ok") and name in {"remember", "context"}:
                    if tools.call(name, arguments) != observation:
                        raise ValueError("Context or work note differs on replay")
            history.append(record)
            tools.context_resources["observations"][str(record["step"])] = observation
            if decision["tool"] in {"finish", "planner_error"}:
                continue
            if name != "context":
                tools.context_resources["observation"] = observation
            while cursor < len(executed) and executed[cursor] != observation:
                cursor += 1
            if cursor == len(executed):
                raise ValueError("Decision has no matching executed trajectory")
            cursor += 1
            if not observation.get("ok"):
                continue
            if name not in MUTATIONS:
                continue
            if name == "graft" and arguments["module_id"] not in {m["id"] for m in modules}:
                raise ValueError("Graft source was not reviewed")
            actual = tools.call(name, arguments)
            if not actual["ok"] or actual != observation:
                raise ValueError("Successful mutation cannot be replayed")
            if name == "graft":
                if arguments.get("parent", "root") != "root":
                    parent = next(n for n in tools.assembly.graph()["instances"] if n["id"] == arguments["parent"])
                    frame = np.asarray(parent["world"]) @ np.asarray(arguments["matrix"])
                else:
                    frame = arguments["matrix"]
                approved.update(part_signatures(reviewed[arguments["module_id"]], frame, arguments.get("color", 16)))
            check = validate(tools.assembly.graph(), tools.catalog)
            if check["status"] != "pass" or part_signatures(tools.assembly.graph()) - approved:
                continue
            evidence = dict(schema="assembly-training-evidence.v1", execution_succeeded=True,
                structure_passed=True, source_review_approved=True, step=record["step"],
                input_sha256=sha256(input_text.encode()).hexdigest(),
                decision_sha256=sha256(decision_text.encode()).hexdigest(),
                trajectory_sha256=digest(trajectory), trace_sha256=digest(trace),
                initial_sha256=digest(initial_path), check=check,
                run=str(directory), graph_sha256=sha256(wire_json(tools.assembly.graph()).encode()).hexdigest())
            key = sha256(wire_json(evidence).encode()).hexdigest()
            samples.append(dict(messages=[dict(role="user", content=input_text),
                dict(role="assistant", content=decision_text)],
                source=dict(model_id=modules[0]["model_id"], family=families[0], families=families,
                    module_hashes=hashes, snapshot=corpus.snapshot(), protocol_hash=protocol_hash(),
                    trajectory_sha256=digest(trajectory)),
                _evidence=evidence, _evidence_key=key))
    # Publish evidence only after the entire replay has passed integrity checks.
    for sample in samples:
        evidence_path = output / "evidence" / (sample.pop("_evidence_key") + ".json")
        write_json(evidence_path, sample.pop("_evidence"))
        sample["evidence"] = dict(path=evidence_path.relative_to(output).as_posix(), sha256=digest(evidence_path))
    return samples


def build(manifest, corpus, native_root, output):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Dataset output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    if not corpus.frozen_snapshot:
        raise ValueError("Training requires a frozen corpus snapshot")
    rows, rejected = [], []
    for item in manifest:
        try:
            rows.extend(replay_run(item, corpus, native_root, output))
        except (ValueError, KeyError, OSError) as exc:
            rejected.append(dict(directory=item.get("directory"), error=f"{type(exc).__name__}: {exc}"))
    # Repeated recovery runs must not increase a sample's weight.
    unique = {}
    for row in rows:
        unique.setdefault(wire_json([row["messages"], row["source"]["module_hashes"]]), row)
    splits, groups = grouped_splits(list(unique.values()))
    for name, samples in splits.items():
        (output / (name + ".jsonl")).write_text("".join(wire_json(r)+"\n" for r in samples), encoding="utf-8")
    report = dict(schema="assembly-dataset.v1", snapshot=corpus.snapshot(), protocol_hash=protocol_hash(),
        status="ready_for_token_validation" if splits["train"] else "no_eligible_training_samples",
        samples={k:len(v) for k,v in splits.items()}, duplicate_samples=len(rows)-len(unique),
        groups=groups, rejected=rejected, corpus=corpus.summary(), training_started=False)
    write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True, help="JSON list of directory/source_modules")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus = Corpus(args.corpus, snapshot=args.snapshot)
    try:
        print(json.dumps(build(json.loads(args.runs.read_text(encoding="utf-8")), corpus, args.native_root, args.output), ensure_ascii=False))
    finally:
        corpus.db.close()


if __name__ == "__main__":
    main()
