"""Freeze shared vehicle inputs and execute independent assembly planner trials."""
from __future__ import annotations

import argparse
from functools import cache
from hashlib import sha256
import json
from pathlib import Path
import shutil

from .assembly_agent import (AssemblyAgent, CONTEXT_VERSION, DECISION_TOKENS,
    PLANNERS, context_file, execution_hash, planner, skill_bundle, wire_json)
from .assembly_corpus import Corpus
from .assembly_evaluation import SUPPORTED_CASES
from .assembly_protocol import protocol_hash
from .assembly_tools import AssemblyTools
from .common import digest, write_json
from .desktop import assembly_mission
from .references import load_references
from .semantic import validate_scene

SCHEMA = "vehicle-assembly-trial.v1"
MAX_SEMANTIC_BYTES = 128 * 2**20


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def identity(value):
    return sha256(wire_json(value).encode()).hexdigest()


def implementation():
    return dict(execution_hash=execution_hash(), protocol_hash=protocol_hash(),
        skill_hash=skill_bundle()["sha256"], context_version=CONTEXT_VERSION,
        decision_tokens=DECISION_TOKENS, trial_hash=digest(Path(__file__)))


def document_resource(path, expected_hash, *, validator=None, limit=MAX_SEMANTIC_BYTES):
    """Keep complete documents available without embedding them in checkpoints."""
    path = Path(path)

    @cache
    def load():
        if path.stat().st_size > limit or digest(path) != expected_hash:
            raise ValueError("Context resource differs from frozen input")
        data = read(path)
        if validator:
            validator(data)
        return data

    return load


def semantic_resource(path, expected_hash):
    return document_resource(path, expected_hash, validator=validate_scene)


def scene_index(scene):
    """Index exact source nodes; geometry remains in the original semantic file."""
    import numpy as np
    from .semantic import world_vertices

    nodes = validate_scene(scene)
    children = {name: [] for name in nodes}
    for node in nodes.values():
        if node.get("parent"):
            children[node["parent"]].append(node["id"])
    indexed = {}
    for position, node in enumerate(scene["nodes"]):
        item = dict(role=node["role"], parent=node.get("parent"),
            pointer=f"semantic:/nodes/{position}", children=children[node["id"]])
        if node.get("geometry"):
            points = world_vertices(node)
            item.update(world_bounds_m=np.array([points.min(axis=0), points.max(axis=0)]).tolist(),
                vertices=len(points), triangles=len(node["geometry"]["triangles"]))
        if node.get("joint"):
            item["joint"] = node["joint"]
        indexed[node["id"]] = item
    return dict(schema="semantic-navigation.v1", units="metres",
        source="semantic", roots=[n["id"] for n in nodes.values() if not n.get("parent")],
        node_count=len(nodes), nodes=indexed)


def empty_output(directory):
    directory = Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Trial output must be new or empty; preserve previous evidence")
    return directory


def freeze_case(*, case, request, semantic, specifications, references, corpus_root,
                snapshot, native_root, output, exclude_models=(), exclude_families=(),
                exclude_hashes=(), max_steps=160, max_seconds=14400):
    """Copy once; subsequent planners consume the exact same immutable files."""
    if case not in SUPPORTED_CASES:
        raise ValueError("Unsupported vehicle trial")
    if not snapshot:
        raise ValueError("A frozen corpus snapshot is required")
    if (type(max_steps) is not int or not 1 <= max_steps <= 2000
            or type(max_seconds) is not int or not 1 <= max_seconds <= 86400):
        raise ValueError("Invalid shared trial budget")
    output, native_root = empty_output(output), Path(native_root).resolve()
    if not native_root.is_dir():
        raise ValueError("Native part library is missing")
    request, semantic, specifications, references = map(Path, (request, semantic, specifications, references))
    mission = assembly_mission(read(request))
    if not mission.get("model"):
        raise ValueError("Vehicle model is required")
    semantic_data = context_file(semantic, limit=MAX_SEMANTIC_BYTES)
    validate_scene(semantic_data["data"])
    context_file(specifications)
    context_file(references, limit=8 * 2**20)
    _, images = load_references(references)
    corpus = Corpus(corpus_root, snapshot=snapshot)
    try:
        exclusions = corpus.exclusions(exclude_models=exclude_models,
            exclude_families=exclude_families, exclude_hashes=exclude_hashes)
        known_models = {row[0] for row in corpus.db.execute("SELECT id FROM models")}
        if (not exclusions["models"] or not set(exclusions["models"]) <= known_models):
            raise ValueError("Identify actual held-out target models or families")
        frozen_snapshot = corpus.snapshot()
    finally:
        corpus.db.close()
    inputs = output / "inputs"
    write_json(inputs / "request.json", mission)
    write_json(inputs / "semantic-index.json", scene_index(semantic_data["data"]))
    reference_data = read(references)
    write_json(inputs / "reference-index.json", {name: {key: item[key] for key in
        ("file", "sha256", "modules", "state", "category", "camera", "synthetic") if key in item}
        for name, item in (reference_data.get("shots") or reference_data["views"]).items()})
    for name, source in (("semantic.json", semantic), ("specifications.json", specifications)):
        shutil.copyfile(source, inputs / name)
    reference_root = inputs / "references"
    reference_root.mkdir(parents=True)
    shutil.copyfile(references, reference_root / "views.json")
    for source in images.values():
        target = reference_root / source.relative_to(references.resolve().parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    anchor = references.resolve().parent / "reference.png"
    if anchor.is_file() and anchor not in images.values():
        shutil.copyfile(anchor, reference_root / "reference.png")
    manifest = dict(schema=SCHEMA, case=case, implementation=implementation(),
        corpus_root=str(Path(corpus_root).resolve()), snapshot=frozen_snapshot,
        native_root=str(native_root), exclusions=exclusions,
        budgets=dict(max_steps=max_steps, max_seconds=max_seconds),
        inputs=dict(request="inputs/request.json", semantic="inputs/semantic.json",
            specifications="inputs/specifications.json", references="inputs/references/views.json",
            semantic_index="inputs/semantic-index.json", reference_index="inputs/reference-index.json"),
        files={p.relative_to(output).as_posix(): digest(p) for p in sorted(inputs.rglob("*")) if p.is_file()},
        limitations=["Native library dependencies are recorded per execution, not fully snapshotted",
            "Training family/module isolation requires an independent dataset audit",
            "Codex output token cap parity is unverified"],
        quality_accepted=False, winner=None)
    write_json(output / "case.json", manifest)
    return manifest


def validate_case(path):
    path = Path(path).resolve()
    manifest = read(path)
    if manifest.get("schema") != SCHEMA or manifest["implementation"] != implementation():
        raise ValueError("Vehicle trial implementation differs from frozen inputs")
    for name, expected in manifest["files"].items():
        source = (path.parent / name).resolve()
        if not source.is_relative_to(path.parent) or digest(source) != expected:
            raise ValueError("Vehicle trial input changed: " + name)
    for name in manifest["inputs"].values():
        if name not in manifest["files"]:
            raise ValueError("Unpinned vehicle trial input")
    load_references(path.parent / manifest["inputs"]["references"])
    corpus = Corpus(manifest["corpus_root"], snapshot=manifest["snapshot"])
    try:
        excluded = manifest["exclusions"]
        if corpus.exclusions(exclude_models=excluded["models"],
                exclude_families=excluded["families"], exclude_hashes=excluded["hashes"]) != excluded:
            raise ValueError("Vehicle trial exclusions changed")
    finally:
        corpus.db.close()
    return manifest


def execute_trial(case_path, backend, output):
    """One selected planner, one fresh graph, no fallback to another planner."""
    if backend not in PLANNERS:
        raise ValueError("Unknown independent planner")
    case_path, output = Path(case_path).resolve(), empty_output(output)
    manifest = validate_case(case_path)
    case_hash = digest(case_path)
    mission = {key: context_file(case_path.parent / manifest["inputs"][key])
        for key in ("request", "specifications")}
    resources = {}
    for key in ("semantic", "references", "semantic_index", "reference_index"):
        name = manifest["inputs"][key]
        source = case_path.parent / name
        mission[key] = dict(sha256=manifest["files"][name],
            storage="verified_json_file", bytes=source.stat().st_size)
        resources[key] = semantic_resource(source, mission[key]["sha256"]) if key == "semantic" else document_resource(
            source, mission[key]["sha256"], limit=8 * 2**20)
    from .assembly_requirements import vehicle_contract
    mission["vehicle_contract"] = vehicle_contract(mission["request"]["data"],
        manifest["files"][manifest["inputs"]["semantic"]],
        repairs=resources["references"]().get("repair_tasks", []), source_index=resources["semantic_index"]())
    record = dict(schema=SCHEMA, case=manifest["case"], case_sha256=case_hash,
        planner=backend, mission_sha256=identity(mission), status="running",
        quality_accepted=False, winner=None, limitations=manifest["limitations"])
    write_json(output / "trial.json", record)
    corpus = None
    try:
        corpus = Corpus(manifest["corpus_root"], snapshot=manifest["snapshot"])
        excluded = manifest["exclusions"]
        tools = AssemblyTools(output, manifest["native_root"], corpus,
            exclude_models=excluded["models"], exclude_families=excluded["families"],
            exclude_hashes=excluded["hashes"])
        _, reference_images = load_references(case_path.parent / manifest["inputs"]["references"])
        tools.reference_images = {name: (path, digest(path)) for name, path in reference_images.items()}
        selected = planner(backend, output / "planner")
        if backend == "codex":
            from .assembly_vehicle_context import prepare_vehicle_context
            selected.vehicle_context, tools.reference_images = prepare_vehicle_context(
                output / "context-resources", resources["semantic_index"](),
                case_path.parent / manifest["inputs"]["references"], mission["specifications"]["data"])
            record["limitations"] = [*record["limitations"],
                "Codex uses vehicle-rich.v1 vision context; local models retain compact text context; not a controlled context comparison"]
        agent = AssemblyAgent(tools, selected, mission,
            **manifest["budgets"])
        tools.context_resources.update(resources)
        state = agent.run()
        validate_case(case_path)
        if digest(case_path) != case_hash:
            raise ValueError("Vehicle trial manifest changed during execution")
        record.update(status="executed", agent_status=state["status"],
            input_hash=state["input_hash"], stop_reason=state["stop_reason"],
            native_dependencies=tools.catalog.library.dependency_record(),
            artifacts={p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob("*"))
                if p.is_file() and p.name != "trial.json"})
        write_json(output / "trial.json", record)
        return record
    except BaseException as error:
        record.update(status="interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
            error=f"{type(error).__name__}: {error}")
        write_json(output / "trial.json", record)
        raise
    finally:
        if corpus is not None:
            corpus.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--case", choices=sorted(SUPPORTED_CASES), required=True)
    for name in ("request", "semantic", "specifications", "references", "corpus-root", "native-root", "output"):
        freeze.add_argument("--" + name, type=Path, required=True)
    freeze.add_argument("--snapshot", required=True)
    for name in ("model", "family", "hash"):
        freeze.add_argument("--exclude-" + name, action="append", default=[])
    freeze.add_argument("--max-steps", type=int, default=160)
    freeze.add_argument("--max-seconds", type=int, default=14400)
    execute = commands.add_parser("run")
    execute.add_argument("--case-file", type=Path, required=True)
    execute.add_argument("--planner", choices=PLANNERS, required=True)
    execute.add_argument("--output", type=Path, required=True)
    arguments = vars(parser.parse_args())
    command = arguments.pop("command")
    if command == "freeze":
        for source, target in (("exclude_model", "exclude_models"),
                               ("exclude_family", "exclude_families"),
                               ("exclude_hash", "exclude_hashes")):
            arguments[target] = arguments.pop(source)
        result = freeze_case(**arguments)
    else:
        result = execute_trial(arguments["case_file"], arguments["planner"], arguments["output"])
    print(wire_json(result))


if __name__ == "__main__":
    main()
