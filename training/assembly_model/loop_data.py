"""Replay successful source actions and retain the complete planner lifecycle.

These are executed source demonstrations, never model-quality results. All
evidence is derived from the unchanged website executor and reviewed corpus.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from car2lego3d.assembly_agent import AssemblyAgent, CONTEXT_VERSION, execution_hash, parse_decision, skill_bundle, wire_json
from car2lego3d.assembly_catalog import validate
from car2lego3d.assembly_corpus import Corpus
from car2lego3d.assembly_graph import Assembly, IOArchive
from car2lego3d.assembly_protocol import protocol_hash
from car2lego3d.assembly_training_data import inside, part_signatures
from car2lego3d.assembly_tools import AssemblyTools
from common import file_hash, load_samples, read_json, write_json
from dataset_audit import audit_splits


def relocate_evidence_paths(observation, replay_root, source_root):
    """Compare run-local evidence identities without changing their hashes."""
    result = deepcopy(observation)
    files = result.get("result", {}).get("module_progress", {}).get("evidence_files", [])
    for item in files:
        path = Path(item["file"])
        if path.is_absolute() and path.is_relative_to(Path(replay_root).resolve()):
            item["file"] = str(Path(source_root).resolve() / path.relative_to(Path(replay_root).resolve()))
    return result


def replay(directory, source, corpus, native_root, output):
    directory = Path(directory).resolve()
    state = read_json(directory / "agent.json")
    contract = state["contract"]
    if (contract["execution_hash"] != execution_hash()
            or contract["protocol_hash"] != protocol_hash()
            or contract["snapshot"] != corpus.snapshot()):
        raise ValueError("Frozen source contract differs")
    if state["stop_reason"] != "planner_finished" or state["digital_status"] != "pass":
        raise ValueError("Source did not finish with a passing digital assembly")
    module = corpus.module(contract["mission"]["semantic"]["source_module"])
    if (module["check_status"] != "pass"
            or module["metadata"].get("source_review_status") != "approved"
            or not module["metadata"].get("training_eligible")):
        raise ValueError("Source is not approved for training")
    reviewed = Assembly.read(inside(corpus.directory, module["metadata"]["file"]))
    expected = part_signatures(reviewed.graph())
    initial_path = directory / "initial-assembly.json"
    if file_hash(initial_path) != contract["initial_assembly_sha256"]:
        raise ValueError("Initial source hash differs")
    initial = read_json(initial_path)
    if part_signatures(Assembly(initial["text"]).graph()) or initial["packages"]:
        raise ValueError("Full lifecycle source must start empty")
    exclusions = contract["exclusions"]
    tools = AssemblyTools(output, native_root, corpus, exclude_models=exclusions["models"],
        exclude_families=exclusions["families"], exclude_hashes=exclusions["hashes"])
    tools.assembly = Assembly(initial["text"], initial["source_sha256"])
    tools.assembly.revision, tools.assembly.joints = initial["revision"], initial["joints"]
    backend = type("ReplayPlanner", (), {"name": state["planner"]})()
    agent = AssemblyAgent(tools, backend, contract["mission"])
    if agent.input_hash != state["input_hash"]:
        raise ValueError("Source input contract differs")
    records = [json.loads(line) for line in (directory / "agent-decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    executed = [json.loads(line) for line in (directory / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
    cursor = 0
    history = deque(maxlen=8)
    exported_revision, completion = None, None
    successful = []
    for record in records:
        if record["input_hash"] != state["input_hash"] or record["planner"] != state["planner"]:
            raise ValueError("Decision belongs to a different source run")
        payload = read_json(inside(directory, record["input_file"]))
        if (file_hash(inside(directory, record["input_file"])) != record["input_sha256"]
                or wire_json(payload) != record["input_text"]
                or payload != agent.context(history, payload["remaining_steps"])
                or wire_json(record["decision"]) != record["decision_text"]):
            raise ValueError("Exact website context differs on replay")
        tool, arguments = parse_decision(record["decision"])
        if tool == "finish":
            actual = dict(ok=exported_revision is not None and exported_revision == tools.assembly.revision,
                          result=dict(requested_finish=True))
            if not actual["ok"]:
                actual["error"] = "Export successfully before requesting finish"
        else:
            actual = tools.call(tool, arguments)
            actual = relocate_evidence_paths(actual, output, directory)
            if cursor >= len(executed) or actual != executed[cursor]:
                raise ValueError("Decision has no matching executed trajectory")
            cursor += 1
        if actual != record["observation"] or not actual["ok"]:
            raise ValueError(f"Source tool cannot be replayed: {tool}")
        if tool == "export":
            exported_revision = actual["revision_after"]
            completion = dict(revision=tools.assembly.revision, export_revision=exported_revision,
                archive_roundtrip_verified=actual["result"]["archive"]["archive_roundtrip_verified"],
                planner_finished=False)
        if part_signatures(tools.assembly.graph()) - expected:
            raise ValueError("Replay added unreviewed geometry")
        history.append(record)
        tools.context_resources["observations"][str(record["step"])] = actual
        if tool != "context":
            tools.context_resources["observation"] = actual
        successful.append(record)
    check = validate(tools.assembly.graph(), tools.catalog)
    if (check["status"] != "pass" or part_signatures(tools.assembly.graph()) != expected
            or not records or records[-1]["decision"]["tool"] != "finish"
            or part_signatures(IOArchive(output / "model.io").assembly.graph()) != expected):
        raise ValueError("Complete replay did not reproduce reviewed source")
    completion["planner_finished"] = True
    evidence_rows = []
    for record in successful:
        evidence = dict(schema="assembly-training-evidence.v2", execution_succeeded=True,
            structure_passed=True, structure_scope="completed_source_module",
            source_review_approved=True, step=record["step"],
            input_sha256=sha256(record["input_text"].encode()).hexdigest(),
            decision_sha256=sha256(record["decision_text"].encode()).hexdigest(),
            trajectory_sha256=file_hash(directory / "trajectory.jsonl"),
            trace_sha256=file_hash(directory / "agent-decisions.jsonl"),
            replay_trajectory_sha256=file_hash(output / "trajectory.jsonl"),
            run=str(directory), replay=str(output), check=check, completion=completion,
            finish_verified=record["decision"]["tool"] == "finish")
        evidence_rows.append((record, evidence, {**source, "trajectory_sha256": evidence["trajectory_sha256"]}))
    return evidence_rows


class LifecycleDemonstrator:
    name = "executed_source_lifecycle_demonstration"

    def __init__(self, operations):
        self.actions = [("context", dict(resource="guide", pointer="/start", limit=400)),
            ("context", dict(resource="guide", pointer="/tools/inspect")),
            ("inspect", dict(limit=1)),
            ("context", dict(resource="guide", pointer="/tools/remember")),
            ("remember", dict(text=f"{len(operations)} source placements; next index=state.instances; check at count, then export and finish.")),
            ("context", dict(resource="guide", pointer="/tools/edit"))]
        self.actions.append(("context", dict(resource="guide", pointer="/operations/add")))
        for index, operation in enumerate(operations):
            self.actions.extend([("context", dict(resource="semantic", pointer=f"/operations/{index}")),
                ("edit", dict(revision=index, operations=[operation]))])
        self.actions.extend([("context", dict(resource="guide", pointer="/tools/check")), ("check", {}),
            ("context", dict(resource="guide", pointer="/tools/export")), ("export", {}), ("finish", {})])
        self.cursor = 0

    def propose(self, payload, step):
        previous = payload.get("last") or {}
        page = previous.get("data") or {}
        if previous.get("tool") == "context" and page.get("next_offset") is not None:
            tool = "context"
            arguments = dict(resource=page["resource"], pointer=page["pointer"],
                             offset=page["next_offset"], limit=400)
        else:
            tool, arguments = self.actions[self.cursor]
            self.cursor += 1
        return dict(tool=tool, arguments_json=wire_json(arguments),
            reason="Replay a reviewed source module lifecycle; no vehicle quality claim.")


def unused_attempt(target):
    target = Path(target)
    if not target.exists() or not any(target.iterdir()):
        return target
    index = 1
    while target.with_name(target.name + f"-retry-{index}").exists():
        index += 1
    return target.with_name(target.name + f"-retry-{index}")


def verify_contract(path, snapshot):
    frozen = read_json(path)
    actual = dict(context_version=CONTEXT_VERSION, execution_hash=execution_hash(),
        protocol_hash=protocol_hash(), skill_hash=skill_bundle()["sha256"], snapshot=snapshot)
    if any(frozen.get(key) != value for key, value in actual.items()):
        raise ValueError("Runtime differs from the frozen training contract")
    root = Path(__file__).resolve().parents[2]
    for relative, expected in frozen.get("implementation", {}).items():
        dependency = (root / relative).resolve()
        if not dependency.is_relative_to(root) or file_hash(dependency) != expected:
            raise ValueError("Frozen implementation differs: " + relative)
    return dict(path=str(Path(path).resolve()), sha256=file_hash(path), **actual)


def ordered_sources(provenance, defer_slow):
    sources = list(enumerate(sorted(provenance.items())))
    if defer_slow:
        sources.sort(key=lambda item: read_json(Path(item[1][0]) / "agent.json").get("elapsed_seconds", 0))
    return sources


def reusable_demonstration(target, mission, snapshot):
    saved = read_json(target / "agent.json") if (target / "agent.json").is_file() else {}
    if saved.get("stop_reason") != "planner_finished" or saved.get("digital_status") != "pass":
        return None
    contract = saved["contract"]
    if (contract["execution_hash"] != execution_hash() or contract["protocol_hash"] != protocol_hash()
            or contract["snapshot"] != snapshot or contract["mission"] != mission
            or contract["skill_hash"] != skill_bundle()["sha256"]
            or contract["context_version"] != CONTEXT_VERSION):
        raise ValueError("Completed demonstration has a different frozen contract")
    return saved


def build(args):
    contract = verify_contract(args.contract, args.snapshot) if args.contract else None
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise ValueError("Output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    original = {name: load_samples(args.dataset / f"{name}.jsonl") for name in ("train", "validation", "test")}
    provenance = {}
    for name, rows in original.items():
        for row in rows:
            evidence = read_json(args.dataset / row["evidence"]["path"])
            provenance.setdefault(evidence["run"], (name, row["source"]))
    corpus = Corpus(args.corpus, snapshot=args.snapshot)
    partitions = {name: [] for name in original}
    report = dict(schema="assembly-lifecycle-data.v1", snapshot=args.snapshot,
        protocol_hash=protocol_hash(), origin="independently_executed_reviewed_source_demonstrations",
        execution_hash=execution_hash(), contract=contract, expected_sources=len(provenance),
        sources=[], rejected=[], quality_accepted=False)
    try:
        sources = ordered_sources(provenance, args.defer_slow)
        for index, (directory, (split, source)) in sources:
            try:
                if args.contract:
                    verify_contract(args.contract, args.snapshot)
                if not args.regenerate:
                    replayed = replay(directory, source, corpus, args.native_root,
                        unused_attempt(output / "replays" / f"{index:04d}"))
                    partitions[split].extend(replayed)
                    report["sources"].append(dict(run=directory, split=split, samples=len(replayed)))
                else:
                    replayed = []
                if (split == "train" or args.regenerate) and not args.audit_only:
                    original_state = read_json(Path(directory) / "agent.json")
                    mission = deepcopy(original_state["contract"]["mission"])
                    mission["request"]["goal"] += " Module " + mission["semantic"]["source_module"]
                    operations = mission["semantic"]["operations"]
                    demo = LifecycleDemonstrator(operations)
                    target = output / "demonstrations" / f"{index:04d}"
                    saved = reusable_demonstration(target, mission, corpus.snapshot()) if args.resume else None
                    if saved:
                        state = saved
                    else:
                        target = unused_attempt(target)
                        tools = AssemblyTools(target, args.native_root, corpus)
                        agent = AssemblyAgent(tools, demo, mission, max_steps=max(32, len(demo.actions) + 8))
                        if "guide" not in tools.context_resources:
                            raise ValueError("Lifecycle generation requires the frozen guide resource")
                        state = agent.run()
                    if state["stop_reason"] != "planner_finished" or state["digital_status"] != "pass":
                        raise ValueError("New lifecycle demonstration failed")
                    generated = replay(target, {**source, "protocol_hash": protocol_hash()}, corpus,
                        args.native_root, unused_attempt(output / "verified-demonstrations" / f"{index:04d}"))
                    partitions[split].extend(generated)
                    report["sources"].append(dict(run=str(target), split=split, samples=len(generated),
                        note="Scripted source demonstration; not autonomous planner validation"))
                print(json.dumps(dict(index=index, split=split, replayed=len(replayed),
                    generated=len(generated) if not args.audit_only and (split == "train" or args.regenerate) else 0)), flush=True)
            except (ValueError, KeyError, OSError) as exc:
                report["rejected"].append(dict(run=directory, error=f"{type(exc).__name__}: {exc}"))
                print(json.dumps(dict(index=index, error=f"{type(exc).__name__}: {exc}")), flush=True)
            write_json(output / "report.partial.json", {**report,
                "status": "in_progress_not_training_ready", "samples": {name: len(rows) for name, rows in partitions.items()}})
        if report["rejected"]:
            report.update(status="source_failures_not_training_ready", training_data_created=False)
            write_json(output / "report.json", report)
            raise ValueError("Source lifecycle failures; no training dataset published")
        if args.contract:
            verify_contract(args.contract, args.snapshot)
        if args.audit_only:
            report.update(status="replay_audit_only", training_data_created=False,
                tool_counts={name: dict(Counter(record["decision"]["tool"] for record, _, _ in rows))
                             for name, rows in partitions.items()})
            write_json(output / "report.json", report)
            return report
        # Generic lifecycle contexts can be identical between families. Keep held-out
        # evidence and remove matching training prompts rather than leaking them.
        heldout = {sha256(record["input_text"].encode()).hexdigest() for name in ("validation", "test")
                   for record, _, _ in partitions[name]}
        removed = Counter()
        seen = {}
        for split in ("test", "validation", "train"):
            kept = []
            for record, evidence, source in partitions[split]:
                key = evidence["input_sha256"]
                if (split == "train" and key in heldout) or (key in seen and seen[key] != split):
                    removed[split] += 1
                    continue
                seen[key] = split
                target = output / "evidence" / (sha256(wire_json(evidence).encode()).hexdigest() + ".json")
                write_json(target, evidence)
                kept.append(dict(messages=[dict(role="user", content=record["input_text"]),
                    dict(role="assistant", content=record["decision_text"])], source=source,
                    evidence=dict(path=target.relative_to(output).as_posix(), sha256=file_hash(target))))
            partitions[split] = kept
            (output / f"{split}.jsonl").write_text("".join(wire_json(row)+"\n" for row in kept), encoding="utf-8")
        checked = {name: load_samples(output / f"{name}.jsonl") for name in partitions}
        report.update(status="executed_replayed_not_quality_accepted", training_data_created=True,
            audit=audit_splits(checked, expected_snapshot=args.snapshot, expected_protocol=protocol_hash()),
            excluded_cross_partition_inputs=dict(removed),
            tool_counts={name: dict(Counter(json.loads(row["messages"][1]["content"])["tool"] for row in rows))
                         for name, rows in checked.items()})
        write_json(output / "report.json", report)
        return report
    finally:
        corpus.db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=Path("data/assembly-corpus"))
    parser.add_argument("--native-root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract", type=Path, help="Verify the explicit frozen runtime and implementation before each source")
    parser.add_argument("--audit-only", action="store_true", help="Replay source traces without generating a training dataset")
    parser.add_argument("--regenerate", action="store_true", help="Execute current-context lifecycle demonstrations from approved old source placements")
    parser.add_argument("--resume", action="store_true", help="Reuse finished demonstrations and preserve interrupted attempts")
    parser.add_argument("--defer-slow", action="store_true", help="Process expensive source modules last without changing split or source indices")
    print(json.dumps(build(parser.parse_args()), ensure_ascii=False))


if __name__ == "__main__":
    main()
