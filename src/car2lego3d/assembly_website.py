"""Website assembly host supporting complete, file-backed vehicle resources."""
import argparse
import json
import os
from pathlib import Path

from .assembly_agent import PLANNERS, context_file, planner
from .assembly_corpus import Corpus
from .assembly_tools import AssemblyTools
from .common import ROOT, digest, write_json
from .references import load_references
from .vehicle_assembly_trials import (document_resource,
    scene_index, semantic_resource)


def vehicle_exclusions(corpus, request):
    """Keep target vehicle families and duplicate modules out of fresh creation."""
    from .official_library import identity_pattern
    from .vehicle_delivery import core_case
    case = core_case(request.get("brand", ""), request.get("model", ""))
    if not case:
        return corpus.exclusions()
    model = {"countach": "Countach", "f40": "F40", "bmw-m4": "M4", "lotus-for-me": "For Me",
        "nissan-r34": "R34", "bugatti-chiron-pur-sport": "Chiron Pur Sport"}[case]
    pattern = identity_pattern(model)
    targets = []
    for row in corpus.db.execute("SELECT id,metadata FROM models"):
        metadata = json.loads(row["metadata"])
        names = [metadata.get(key) or "" for key in ("model", "original_name", "name_zh")]
        brands = [name for item in metadata.get("brands", [])
            for name in (item.get("id", ""), item.get("en", ""), item.get("zh", ""))]
        if (any(core_case(name, model) == case for name in brands)
                and any(pattern.search(name.casefold()) for name in names)):
            targets.append(row["id"])
    return corpus.exclusions(exclude_models=targets)


def prepare_resources(output, request, semantic, specifications, references, shape_evidence=None):
    """Bind complete source files and derive navigable indexes without mesh reduction."""
    output, semantic, references = map(Path, (output, semantic, references))
    source = semantic_resource(semantic, digest(semantic))
    scene = source()
    manifest, images = load_references(references)
    if references.stat().st_size > 8 * 2**20:
        raise ValueError("Reference manifest exceeds host resource limit")
    indexes = output / "context-resources"
    index = scene_index(scene)
    write_json(indexes / "semantic-index.json", index)
    write_json(indexes / "reference-index.json", {name: {key: item[key] for key in
        ("file", "sha256", "modules", "state", "category", "camera", "synthetic") if key in item}
        for name, item in (manifest.get("shots") or manifest["views"]).items()})
    paths = dict(semantic=semantic, references=references,
        semantic_index=indexes / "semantic-index.json", reference_index=indexes / "reference-index.json")
    mission = dict(request=context_file(request), specifications=context_file(specifications))
    from .delivery_targets import TARGETS
    from .vehicle_delivery import core_case
    target = TARGETS.get(core_case(mission["request"]["data"].get("brand", ""), mission["request"]["data"].get("model", "")))
    if target:
        # Source archive parts/BOM never enter fresh planner inputs.
        mission["target_profile"] = {key: target[key] for key in ("brand", "model", "body_colors", "features")}
    from .assembly_requirements import vehicle_contract
    mission["vehicle_contract"] = vehicle_contract(mission["request"]["data"], digest(semantic),
        repairs=manifest.get("repair_tasks", []), source_index=index)
    mission["detail_standard"] = mission["vehicle_contract"]["detail_standard"]
    resources = {}
    for key, path in paths.items():
        mission[key] = dict(sha256=digest(path), storage="verified_json_file", bytes=path.stat().st_size)
        resources[key] = source if key == "semantic" else document_resource(path, mission[key]["sha256"], limit=8 * 2**20)
    mission["host"] = dict(sha256=digest(Path(__file__)), resources_sha256=digest(
        Path(__file__).with_name("vehicle_assembly_trials.py")))
    bound = {str(path.resolve()): digest(path) for path in [*paths.values(), Path(request), Path(specifications), *images.values()]}
    if shape_evidence is not None:
        from .shape_evidence import prepare
        mission["shape_evidence"], resources["shape_evidence"], shape_bound = prepare(
            shape_evidence, indexes, specifications, references)
        bound.update(shape_bound)
    return mission, resources, bound


def _run(*, output, request, semantic, specifications, references, backend, snapshot=None,
        corpus_root=None, native_root=None, max_steps=160, retry=False, publish_artifacts=None,
        context_profile="production", seed_from=None, checkpoint=None, shape_evidence=None):
    if context_profile not in {"production", "shared-text"}:
        raise ValueError("Unknown planner context profile")
    corpus = Corpus(corpus_root or ROOT / "data/assembly-corpus", snapshot=snapshot)
    try:
        if not snapshot:
            progress_path = corpus.directory / "progress.json"
            if progress_path.is_file() and json.loads(progress_path.read_text(encoding="utf-8")).get("status") == "running":
                raise ValueError("Corpus indexing is running; select a completed frozen snapshot")
            frozen = corpus.freeze()
            corpus.db.close()
            corpus = Corpus(corpus.directory, snapshot=frozen["snapshot"])
        mission, resources, bound = prepare_resources(output, request, semantic, specifications, references, shape_evidence)
        mission["comparison_context_profile"] = context_profile
        excluded = vehicle_exclusions(corpus, mission["request"]["data"])
        tools = AssemblyTools(output, native_root or os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"), corpus,
            exclude_models=excluded["models"], exclude_families=excluded["families"], exclude_hashes=excluded["hashes"])
        recovery = None
        if seed_from:
            from .assembly_recovery import seed_checkpoint
            recovery = seed_checkpoint(tools, seed_from, mission, backend, corpus.snapshot(), request)
        _, reference_images = load_references(references)
        tools.reference_images = {view: (path, digest(path)) for view, path in reference_images.items()}
        backend = planner(backend, Path(output) / "planner")
        if backend.name == "codex" and context_profile == "production":
            from .assembly_vehicle_context import prepare_vehicle_context
            backend.vehicle_context, tools.reference_images = prepare_vehicle_context(
                Path(output) / "context-resources", resources["semantic_index"](), references,
                mission["specifications"]["data"], modules=mission["vehicle_contract"]["modules"])
        tools.context_resources.update(resources)
        from .assembly_schedule import ModuleScheduler
        prior_path = tools.directory / "agent.json"
        prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.is_file() else {}
        if prior and prior.get("schema") != "assembly-scheduled-agent.v1":
            raise ValueError("Historical whole-car run is preserved; use a new artifact revision for module scheduling")
        scheduler = ModuleScheduler(tools, backend, mission, resources, max_steps=max_steps, checkpoint=checkpoint)
        if recovery:
            from .assembly_recovery import recheck_seed
            recheck_seed(scheduler, recovery)
        from .assembly_recovery import record_launch
        record_launch(dict(output=Path(output), request=Path(request), semantic=Path(semantic),
            specifications=Path(specifications), references=Path(references), backend=backend.name,
            snapshot=corpus.snapshot(), corpus_root=corpus.directory,
            native_root=Path(native_root or os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw")),
            max_steps=max_steps, publish_artifacts=Path(publish_artifacts) if publish_artifacts else None,
            context_profile=context_profile, shape_evidence=Path(shape_evidence) if shape_evidence else None), bound)
        tasks = scheduler.run(retry=retry)
        if checkpoint:
            checkpoint()
        check, motion = tools.call("check", {}), tools.call("motion", dict(samples=9))
        rendered = tools.call("render", {})
        modules = tools.call("module_progress", {})
        if modules.get("result", {}).get("structural_ready"):
            tools.review_vehicle_modules()
            modules = tools.call("module_progress", {})
        exported = tools.call("export", {})
        attempts = [tools.directory / item["directory"] / "agent.json"
            for task in tasks["tasks"].values() for item in task["attempts"]]
        executions = [json.loads(path.read_text(encoding="utf-8")) for path in attempts if path.is_file()]
        result = dict(schema="assembly-scheduled-agent.v1", planner=backend.name, input_hash=scheduler.input_hash,
            contract=scheduler.contract, status="draft" if exported["ok"] else "failed",
            steps=sum(row.get("steps", 0) for row in executions),
            elapsed_seconds=sum(row.get("elapsed_seconds", 0) for row in executions),
            stop_reason="modules_assembled" if tasks["complete"] == tasks["required"] else "module_blocked",
            checks=check, motion=motion, render=rendered, export=exported, module_progress=modules.get("result"),
            module_tasks_sha256=digest(scheduler.path), delivery_status="draft", appearance="unverified", physical="unverified")
        write_json(prior_path, result)
        # A replayable vehicle trace keeps each action's original scoped input and contract.
        records = []
        for task in tasks["tasks"].values():
            for attempt in task["attempts"]:
                folder = tools.directory / attempt["directory"]
                trace = folder / "agent-decisions.jsonl"
                if trace.is_file():
                    for row in trace.read_text(encoding="utf-8").splitlines():
                        record = json.loads(row)
                        record["input_file"] = str((folder / record["input_file"]).relative_to(tools.directory))
                        records.append(dict(record, module_task=task["id"], module_attempt=attempt["directory"]))
        trace = tools.directory / "agent-decisions.jsonl"
        trace.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
        if any(digest(Path(name)) != expected for name, expected in bound.items()):
            raise ValueError("Website assembly inputs changed during execution")
        if publish_artifacts and result.get("export", {}).get("ok"):
            if checkpoint:
                checkpoint()
            from .assembly_presentation import render
            from .assembly_desktop import publish
            publish(tools, publish_artifacts)
            if (tools.directory / "assembly.glb").is_file():
                import shutil
                presentation = render(tools.directory / "assembly.glb", Path(publish_artifacts) / "06-acceptance")
                for item in presentation["images"]:
                    shutil.copyfile(Path(publish_artifacts) / "06-acceptance" / item["file"],
                        Path(publish_artifacts) / "05-presentation" / ("render_" + item["view"] + ".png"))
            from .vehicle_delivery import single_report, core_case
            vehicle = mission["request"]["data"]
            case = core_case(vehicle.get("brand", ""), vehicle.get("model", ""))
            if case:
                write_json(Path(publish_artifacts) / "06-acceptance/delivery-report.json",
                    single_report(case, publish_artifacts, Path(publish_artifacts) / "06-acceptance/website-evidence.json"))
        return result
    finally:
        corpus.db.close()


def run(**arguments):
    from .job_runtime import register_assembly, finish_assembly, assembly_checkpoint, desktop_assembly_checkpoint
    request = Path(arguments["request"])
    publish = arguments.get("publish_artifacts")
    is_job = (request.name == "request.json" and len(request.parent.name) == 32
        and all(c in "0123456789abcdef" for c in request.parent.name))
    registration = register_assembly(request, arguments["output"], publish) if publish and is_job else None
    try:
        checkpoint = ((lambda: assembly_checkpoint(request, registration)) if registration
            else desktop_assembly_checkpoint(request) if publish and is_job else None)
        result = _run(**arguments, checkpoint=checkpoint)
    except Exception as error:
        finish_assembly(request, registration, error)
        raise
    finish_assembly(request, registration)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("request", "semantic", "specifications", "references", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--planner", choices=PLANNERS, required=True)
    parser.add_argument("--snapshot")
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--native-root", type=Path)
    parser.add_argument("--max-steps", type=int, default=160)
    parser.add_argument("--context-profile", choices=("production", "shared-text"), default="production",
        help="Use shared-text for controlled planner comparisons with identical navigable resources")
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--seed-from", type=Path,
        help="Carry a stopped same-planner native checkpoint into a new revision and recheck its completed modules")
    parser.add_argument("--publish-artifacts", type=Path)
    parser.add_argument("--shape-evidence", type=Path, help="Completed calibrated Hunyuan reconstruction directory")
    arguments = vars(parser.parse_args())
    arguments["backend"] = arguments.pop("planner")
    result = run(**arguments)
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
