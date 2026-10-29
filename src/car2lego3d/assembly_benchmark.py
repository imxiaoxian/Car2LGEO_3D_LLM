"""Executed, held-out module comparisons. Never promotes a whole-car planner."""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from .assembly_agent import (AssemblyAgent, CodexPlanner, CONTEXT_VERSION, DECISION_TOKENS, LocalPlanner,
                             execution_hash, skill_bundle, wire_json)
from .assembly_catalog import NativeCatalog, validate
from .assembly_corpus import Corpus
from .assembly_graph import Assembly, IOArchive
from .assembly_protocol import protocol_hash
from .assembly_tools import AssemblyTools
from .assembly_training_data import inside, part_signatures, replay_run
from .common import digest, write_json

PLANNERS = ("codex", "local_base", "local_finetuned")
SCOPE = ("Held-out source-placement module reconstruction from supplied native placements. "
         "Not autonomous image-to-car creation, appearance or full-car acceptance.")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def split_isolation(splits):
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        for key in ("families", "module_hashes"):
            def values(name):
                return {v for row in splits[name] for v in row["source"].get(
                    key, [row["source"]["family"]] if key == "families" else [])}
            if values(left) & values(right):
                raise ValueError(f"Held-out leakage: {left}/{right} {key}")


def native_identity(root, expected):
    catalog = NativeCatalog(root)
    parts = sorted({json.loads(signature)[0] for signature in expected})
    records = {}
    for name in parts:
        record = catalog.part(name)
        catalog.library.triangles(name)
        records[name] = {k: record[k] for k in ("status", "hashes", "errors")}
    return dict(parts=records, geometry_dependencies=catalog.library.dependency_record()["files"])


def freeze_suite(dataset, corpus, native_root, output, *, max_steps=32, max_seconds=900):
    """Re-execute source evidence and deduplicate decision rows into actual tasks."""
    if not corpus.frozen_snapshot:
        raise ValueError("Benchmark requires a frozen corpus")
    if type(max_steps) is not int or not 1 <= max_steps <= 2000 or not 1 <= max_seconds <= 86400:
        raise ValueError("Invalid benchmark budget")
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Suite output must be new or empty")
    splits = {name: [json.loads(line) for line in (dataset / f"{name}.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()] for name in ("train", "validation", "test")}
    split_isolation(splits)
    if not splits["test"]:
        raise ValueError("No held-out test tasks")
    grouped = {}
    for row in splits["test"]:
        evidence_path = inside(dataset, row["evidence"]["path"])
        if digest(evidence_path) != row["evidence"]["sha256"]:
            raise ValueError("Dataset evidence hash differs")
        evidence = read(evidence_path)
        run = Path(evidence["run"]).resolve()
        grouped.setdefault(run, []).append((row, evidence))
    tasks, source_files = [], {}
    for run, rows in sorted(grouped.items()):
        state = read(run / "agent.json")
        contract = state["contract"]
        if contract.get("skill_hash") != skill_bundle()["sha256"]:
            raise ValueError("Source skill changed")
        module_id = contract["mission"].get("semantic", {}).get("source_module")
        if module_id:
            modules = [corpus.module(module_id)]
        else:
            ids = {row["source"]["model_id"] for row, _ in rows}
            hashes = {h for row, _ in rows for h in row["source"]["module_hashes"]}
            modules = [corpus.module(r["id"]) for r in corpus.db.execute("SELECT id,model_id,hash FROM modules")
                       if r["model_id"] in ids and r["hash"] in hashes]
            if len(modules) != 1:
                raise ValueError("Source task must identify one complete module")
        with TemporaryDirectory(prefix="assembly-benchmark-source-") as temporary:
            replayed = replay_run(dict(directory=str(run), source_modules=[m["id"] for m in modules]),
                                  corpus, native_root, Path(temporary))
            verified = {(wire_json(s["messages"]), wire_json(s["source"]),
                         fingerprint(read(Path(temporary) / s["evidence"]["path"]))) for s in replayed}
            for row, evidence in rows:
                if (wire_json(row["messages"]), wire_json(row["source"]), fingerprint(evidence)) not in verified:
                    raise ValueError("Held-out row differs from executed source evidence")
        initial = read(run / "initial-assembly.json")
        if (part_signatures(Assembly(initial["text"]).graph()) or initial["packages"]
                or initial["dependencies"] or initial["joints"] or initial["revision"]):
            raise ValueError("Source-placement benchmark requires an empty initial assembly")
        expected = Counter()
        for module in modules:
            expected.update(part_signatures(Assembly.read(inside(corpus.directory, module["metadata"]["file"])).graph()))
        if not expected:
            raise ValueError("Empty expected assembly")
        source_graph = IOArchive(run / "model.io").assembly.graph()
        if part_signatures(source_graph) != expected:
            raise ValueError("Source export is not the complete reviewed module")
        model_ids = sorted({m["model_id"] for m in modules})
        exclusions = corpus.exclusions(exclude_models=model_ids)
        if any(corpus.allowed(m, exclusions) for m in modules):
            raise ValueError("Complete target remains in retrieval")
        for name in ("agent.json", "agent-decisions.jsonl", "trajectory.jsonl", "initial-assembly.json", "model.io"):
            source_files[str(run / name)] = digest(run / name)
        for module in modules:
            path = inside(corpus.directory, module["metadata"]["file"])
            source_files[str(path)] = digest(path)
        tasks.append(dict(id=fingerprint([m["id"] for m in modules])[:24],
            modules=[m["id"] for m in modules], source_run=str(run), decision_rows=len(rows),
            mission=contract["mission"], initial=initial, expected=dict(sorted(expected.items())),
            exclusions=exclusions, native=native_identity(native_root, expected)))
    suite = dict(schema="assembly-executed-benchmark.v1", scope=SCOPE, snapshot=corpus.snapshot(),
        protocol_hash=protocol_hash(), execution_hash=execution_hash(), skill_hash=skill_bundle()["sha256"],
        benchmark_hash=digest(Path(__file__)),
        budgets=dict(max_steps=max_steps, max_seconds=max_seconds, local_output_tokens=DECISION_TOKENS,
                     codex_output_token_cap="unverified", context_version=CONTEXT_VERSION),
        source_files={**source_files, **{str(dataset / f"{n}.jsonl"): digest(dataset / f"{n}.jsonl") for n in splits},
                      **{str(inside(dataset, row["evidence"]["path"])): row["evidence"]["sha256"]
                         for row in splits["test"]}}, tasks=tasks, quality_accepted=False)
    write_json(output / "suite.json", suite)
    return suite


def validate_suite(suite, corpus, native_root):
    current = dict(protocol_hash=protocol_hash(), execution_hash=execution_hash(), skill_hash=skill_bundle()["sha256"],
                   benchmark_hash=digest(Path(__file__)))
    if suite.get("schema") != "assembly-executed-benchmark.v1" or any(suite.get(k) != v for k, v in current.items()):
        raise ValueError("Benchmark implementation changed")
    if corpus.snapshot() != suite["snapshot"]:
        raise ValueError("Benchmark corpus changed")
    for path, expected in suite["source_files"].items():
        if digest(Path(path)) != expected:
            raise ValueError("Benchmark source changed: " + path)
    for task in suite["tasks"]:
        if native_identity(native_root, task["expected"]) != task["native"]:
            raise ValueError("Benchmark native parts changed")
        if corpus.exclusions(exclude_models=task["exclusions"]["models"],
                exclude_families=task["exclusions"]["families"], exclude_hashes=task["exclusions"]["hashes"]) != task["exclusions"]:
            raise ValueError("Benchmark exclusions changed")
        if any(corpus.allowed(corpus.module(m), task["exclusions"]) for m in task["modules"]):
            raise ValueError("Target retrieval is not excluded")


def measure(expected, tools, state):
    actual = part_signatures(tools.assembly.graph())
    expected = Counter(expected)
    model = tools.directory / "model.io"
    archive, error = False, None
    if model.is_file():
        try:
            archive = part_signatures(IOArchive(model).assembly.graph()) == actual
        except (ValueError, KeyError, OSError) as exc:
            error = str(exc)
    trace = tools.directory / "agent-decisions.jsonl"
    records = [json.loads(r) for r in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
    failed = [r for r in records if not r["observation"].get("ok")]
    digital = state.get("checks", {}).get("result", {}).get("status", "unverified")
    exact = actual == expected
    finished = state.get("stop_reason") == "planner_finished"
    return dict(expected_parts=sum(expected.values()), actual_parts=sum(actual.values()),
        missing=dict(expected-actual), extra=dict(actual-expected), exact_parts_and_transforms=exact,
        digital=digital, archive_roundtrip=archive, archive_error=error, planner_finished=finished,
        execution_pass=exact and digital == "pass" and archive and finished,
        decisions=len(records), tool_errors=len(failed),
        planner_errors=sum(r["decision"]["tool"] == "planner_error" for r in records),
        successful_mutations=sum(r["decision"]["tool"] in {"edit", "graft", "snap", "attach", "build"} and r["observation"].get("ok", False) for r in records),
        seconds=state["elapsed_seconds"], appearance="unverified", studio="unverified", physical="unverified")


def service_identity(backend):
    if backend.name == "codex":
        return dict(planner=backend.name, executable=str(Path(backend.executable).resolve()),
                    executable_sha256=digest(Path(backend.executable)), model=backend.model, output_token_cap="unverified")
    import httpx
    response = httpx.get(backend.url.rstrip("/") + "/health", trust_env=False, timeout=10)
    response.raise_for_status()
    identity = response.json()
    if identity.get("model") != backend.name or not identity.get("revision"):
        raise ValueError("Service identity does not match requested planner")
    return identity


def execute(suite, task, corpus, native_root, backend, output):
    validate_suite(suite, corpus, native_root)
    output = Path(output)
    if output.exists() and any(p.is_file() for p in output.rglob("*")):
        raise ValueError("Benchmark run output must be new or empty")
    if backend.name == "codex":
        backend.directory = output / "planner"
        backend.directory.mkdir(parents=True, exist_ok=True)
    before = service_identity(backend)
    exclusions = task["exclusions"]
    tools = AssemblyTools(output, native_root, corpus, exclude_models=exclusions["models"],
                          exclude_families=exclusions["families"], exclude_hashes=exclusions["hashes"])
    initial = task["initial"]
    tools.assembly = Assembly(initial["text"], initial["source_sha256"])
    tools.assembly.revision, tools.assembly.joints = initial["revision"], initial["joints"]
    tools.save()
    agent = AssemblyAgent(tools, backend, task["mission"], max_steps=suite["budgets"]["max_steps"],
                          max_seconds=suite["budgets"]["max_seconds"])
    input_contract = dict(suite_sha256=fingerprint(suite), task=task["id"], agent=agent.contract, budgets=suite["budgets"])
    write_json(output / "benchmark-input.json", input_contract)
    state = agent.run()
    metrics = measure(task["expected"], tools, state)
    after = service_identity(backend)
    validate_suite(suite, corpus, native_root)
    artifacts = {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()}
    result = dict(schema="assembly-executed-result.v1", scope=SCOPE, task=task["id"], planner=backend.name,
        suite_sha256=fingerprint(suite), input_hash=fingerprint(input_contract), identity_before=before, identity_after=after,
        identity_unchanged=before == after, metrics=metrics, artifacts=artifacts,
        output=str(output.resolve()), quality_accepted=False, winner=None)
    write_json(output / "benchmark-result.json", result)
    return result


def compare_results(suite, results, corpus, native_root):
    validate_suite(suite, corpus, native_root)
    rows, reasons = {}, []
    tasks = {t["id"]: t for t in suite["tasks"]}
    for result in results:
        key = (result["task"], result["planner"])
        if (key in rows or key[0] not in tasks or key[1] not in PLANNERS
                or result["suite_sha256"] != fingerprint(suite)):
            raise ValueError("Duplicate or mismatched benchmark result")
        root = Path(result["output"])
        required = {"agent.json", "benchmark-input.json", "state.json", "agent-decisions.jsonl"}
        if not required.issubset(result["artifacts"]):
            raise ValueError("Missing benchmark execution artifacts")
        for name, expected in result["artifacts"].items():
            if digest(inside(root, name)) != expected:
                raise ValueError("Executed benchmark artifact changed")
        contract, state, saved = (read(root / n) for n in ("benchmark-input.json", "agent.json", "state.json"))
        if (fingerprint(contract) != result["input_hash"] or contract["agent"] != state["contract"]
                or state["planner"] != key[1] or contract["suite_sha256"] != fingerprint(suite)
                or contract["task"] != key[0] or contract["budgets"] != suite["budgets"]):
            raise ValueError("Executed benchmark contract differs")
        assembly = Assembly(saved["text"], saved["source_sha256"])
        assembly.revision, assembly.joints = saved["revision"], saved["joints"]
        metrics = measure(tasks[key[0]]["expected"], SimpleNamespace(directory=root, assembly=assembly), state)
        if metrics != result["metrics"]:
            raise ValueError("Reported benchmark metrics differ from execution artifacts")
        catalog = NativeCatalog(native_root, [inside(root, p) for p in saved.get("packages", [])])
        if validate(assembly.graph(), catalog)["status"] != metrics["digital"]:
            raise ValueError("Digital check differs from current geometry and connections")
        if result["identity_unchanged"] != (result["identity_before"] == result["identity_after"]):
            raise ValueError("Reported planner identity differs")
        rows[key] = result
    for task in suite["tasks"]:
        group = [rows.get((task["id"], p)) for p in PLANNERS]
        if any(r is None for r in group):
            reasons.append("Missing independent planner result: " + task["id"])
            continue
        if len({r["input_hash"] for r in group}) != 1:
            reasons.append("Planner input contracts differ: " + task["id"])
        if any(not r["identity_unchanged"] for r in group):
            reasons.append("Planner identity changed: " + task["id"])
    reasons += ["Codex output token cap parity is unverified",
                "Module placement tasks cannot select a whole-car production planner"]
    return dict(schema="assembly-executed-comparison.v1", scope=SCOPE, suite_sha256=fingerprint(suite),
        tasks=len(suite["tasks"]), results=list(rows.values()), reasons=reasons, winner=None,
        quality_accepted=False, default_changed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--snapshot")
    parser.add_argument("--max-steps", type=int, default=32)
    parser.add_argument("--max-seconds", type=int, default=900)
    parser.add_argument("--planner", choices=PLANNERS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--codex")
    parser.add_argument("--model")
    parser.add_argument("--task-index", type=int, default=0)
    args = parser.parse_args()
    if args.dataset:
        if not args.snapshot or args.suite.name != "suite.json":
            parser.error("Freeze requires --snapshot and a suite.json destination")
        corpus = Corpus(args.corpus, snapshot=args.snapshot)
        try:
            suite = freeze_suite(args.dataset, corpus, args.native_root, args.suite.parent,
                                 max_steps=args.max_steps, max_seconds=args.max_seconds)
            print(wire_json(dict(suite=str(args.suite), tasks=len(suite["tasks"]), scope=SCOPE)))
        finally:
            corpus.db.close()
        return
    if not args.planner or not args.output:
        parser.error("Execution requires --planner and --output")
    suite = read(args.suite)
    corpus = Corpus(args.corpus, snapshot=suite["snapshot"])
    try:
        backend = (CodexPlanner(args.output / "planner", executable=args.codex, model=args.model, timeout=180)
                   if args.planner == "codex" else LocalPlanner(args.planner, url=args.url, timeout=180))
        result = execute(suite, suite["tasks"][args.task_index], corpus, args.native_root, backend, args.output)
        print(wire_json(dict(planner=result["planner"], metrics=result["metrics"], output=result["output"])))
    finally:
        corpus.db.close()


if __name__ == "__main__":
    main()
