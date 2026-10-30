"""Run staged modelling against frozen, explicitly unaccepted reference images."""
import argparse
import json
from pathlib import Path
import shutil

from car2lego3d.common import digest, write_json
from car2lego3d.references import load_references
from car2lego3d.staged_model import reconstruct


def run(views, specifications, output, reuse_phase_dialogues=None):
    views, specifications, output = map(lambda p: Path(p).resolve(), (views, specifications, output))
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new diagnostic directory; preserve earlier attempts")
    manifest, images = load_references(views)
    review_info = manifest.get("content_review", {})
    review_path = (views.parent / review_info.get("file", "")).resolve()
    if (not review_path.is_relative_to(views.parent) or not review_path.is_file()
            or digest(review_path) != review_info.get("sha256")):
        raise ValueError("Diagnostic requires a hash-bound reference content review")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    source_files = {views, specifications, review_path, *images.values()}
    anchor = views.parent / manifest.get("identity_anchor", "reference.png")
    if not anchor.resolve().is_relative_to(views.parent):
        raise ValueError("Identity anchor escaped reference directory")
    if anchor.is_file():
        source_files.add(anchor)
    sources = {str(path): digest(path) for path in source_files}
    inputs = output / "inputs"
    references = inputs / "references"
    references.mkdir(parents=True)
    for path in {*images.values(), *({anchor} if anchor.is_file() else set())}:
        target = references / path.relative_to(views.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    shutil.copyfile(specifications, inputs / "vehicle-specs.json")
    write_json(references / "input-review.json", review)
    rejected = {name: result for name, result in review["shots"].items() if result["status"] != "pass"}
    manifest["brief"] = {**manifest.get("brief", {}), "diagnostic_reference_issues": rejected,
        "diagnostic_instructions": "References are unaccepted. Use the documented vehicle variant to correct "
        "the listed visible discrepancies. Do not repeat them in geometry. Hidden structures remain inference."}
    manifest["content_review"] = dict(file="input-review.json", sha256=digest(references / "input-review.json"),
                                      status=review["status"])
    manifest["cross_view_consistency_verified"] = False
    write_json(references / "views.json", manifest)
    load_references(references / "views.json")
    result = dict(schema="vehicle-modelling-diagnostic.v1", sources=sources, status="running",
        quality_accepted=False, website_acceptance=False, fresh_creation_delivery=False,
        unresolved_reference_shots=sorted(rejected),
        inputs={p.relative_to(inputs).as_posix(): digest(p) for p in inputs.rglob("*") if p.is_file()})
    if reuse_phase_dialogues:
        cache_root = Path(reuse_phase_dialogues).resolve()
        reused = {}
        for path in cache_root.glob("*/attempt-1-*.json"):
            if path.name not in {"attempt-1-request.json", "attempt-1-response.json", "attempt-1-endpoint.json"}:
                continue
            target = output / "02-mesh/checkpoints" / path.parent.name / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            reused[str(path)] = digest(path)
        result["reusable_dialogue_files"] = reused
        result["reuse_policy"] = "Reuse only when ask_json matches the complete request, endpoint and completed response"
    write_json(output / "experiment.json", result)
    try:
        reconstruct(references / "views.json", output / "02-mesh", inputs / "vehicle-specs.json")
        result["status"] = "modelled_draft"
    except BaseException as error:
        result.update(status="interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
                      error=f"{type(error).__name__}: {error}")
        raise
    finally:
        result["source_files_unchanged"] = all(Path(name).is_file() and digest(Path(name)) == expected
                                               for name, expected in sources.items())
        result["frozen_inputs_unchanged"] = all((inputs / name).is_file() and digest(inputs / name) == expected
                                                for name, expected in result["inputs"].items())
        write_json(output / "experiment.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("views", "specifications", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--reuse-phase-dialogues", type=Path)
    print(json.dumps(run(**vars(parser.parse_args())), ensure_ascii=False))
