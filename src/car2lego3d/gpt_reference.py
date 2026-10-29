"""Read-only subprocess bridge to Car2LEGO's actual reference image service."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from dotenv import dotenv_values
from PIL import Image

from .common import ROOT, digest, stage, write_json
from .vehicle import load_spec, spec_record
from .reference_plan import reference_plan


def configuration():
    local = dotenv_values(ROOT / ".env")
    def value(name, default=""):
        return os.environ.get(name) or local.get(name) or default
    source = Path(value("CAR2LEGO_SOURCE_ROOT", "D:/lego")).resolve()
    python = value("CAR2LEGO_SOURCE_PYTHON", sys.executable)
    return source, python


def _generate_once(prompt, directory, vehicle_spec=None, reference_image=None, corrections=None):
    directory = Path(directory).resolve()
    source, python = configuration()
    upstream = source / "backend/app/services/reference_images.py"
    if not upstream.is_file():
        raise RuntimeError("Car2LEGO reference service missing. Configure CAR2LEGO_SOURCE_ROOT in .env.")
    if directory.is_relative_to(source):
        raise ValueError("Reference outputs must be outside the original Car2LEGO project.")
    spec = load_spec(vehicle_spec) if vehicle_spec else None
    make, _, model = (spec["model"] if spec else prompt).partition(" ")
    state = {"design_id": "car2lego", "storage_path": str(directory),
             "make": make, "model": model or make, "year": None,
             "vehicle_features": {"variant": spec["variant"]} if spec else {},
             "custom_request": prompt, "mod_ids": [], "physical_specifications": spec or {}}
    state['reference_corrections'] = corrections or {}
    if reference_image:
        anchor = Path(reference_image).resolve()
        with Image.open(anchor) as image:
            image.verify()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "identity-anchor.png"
        with Image.open(anchor) as image:
            if image.format == "PNG":
                if anchor != target.resolve():
                    shutil.copyfile(anchor, target)
            else:
                image.save(target, format="PNG")
        state["source_image_path"] = str(target)
    inputs = {"prompt": prompt, "provider": "Car2LEGO ReferenceImageService", "source_sha256": digest(upstream),
              "worker_sha256": digest(ROOT / "scripts/car2lego_reference_worker.py"),
              "identity_contract_sha256": digest(Path(__file__).with_name("reference_identity.py")),
              "content_review_sha256": digest(Path(__file__).with_name("reference_review.py")),
              "reference_gate_sha256": digest(Path(__file__).with_name("reference_gate.py")),
              "shots": [s.record() for s in reference_plan(state['make'] + ' ' + state['model'])],
              "source_root": str(source)}
    with stage(directory, "image", inputs) as record:
        if spec:
            record.update(vehicle_spec=spec_record(vehicle_spec), specifications=spec)
        if reference_image:
            record["reference_image"] = {"file": "identity-anchor.png", "sha256": digest(target)}
        request = directory / "reference-request.json"
        write_json(request, state)
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        with (directory / "reference-service.log").open("a", encoding="utf-8") as log:
            with subprocess.Popen([python, "-u", "-B", str(ROOT / "scripts/car2lego_reference_worker.py"),
                                   "--source-root", str(source), "--request", str(request)],
                                  env=environment, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT) as process:
                returncode = process.wait()
        if returncode:
            raise RuntimeError("Car2LEGO GPT reference generation failed; inspect reference-service.log. No local-model fallback.")
        manifest = json.loads((directory / "car2lego/references/manifest.json").read_text(encoding="utf-8"))
        overview = next(item for item in manifest["images"] if item["view"] == "overview")
        if not overview["model"].startswith("gpt-image-"):
            raise RuntimeError("The reused service did not report a GPT Image model.")
        generated = directory / "car2lego" / overview["path"]
        if digest(generated) != overview["sha256"]:
            raise ValueError("Generated reference hash mismatch.")
        output = directory / "reference.png"
        shutil.copyfile(generated, output)
        shots, views = {}, {}
        for item in manifest['images']:
            view = item['view']
            origin = directory / "car2lego" / item["path"]
            if not item["model"].startswith("gpt-image-") or digest(origin) != item["sha256"]:
                raise ValueError(f"Invalid GPT multiview provenance for {view}.")
            destination = directory / 'shots' / f"{view}.png"
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(origin, destination)
            shots[view] = {**item, 'file': f'shots/{view}.png'}
            if view in {'front', 'back', 'left', 'right'}:
                views[view] = shots[view]
        view_manifest = directory / "views.json"
        write_json(view_manifest, {"schema_version": 2, "views": views, 'shots': shots,
                   'expected_shots': manifest['expected_shots'],
                   "camera_contract": "labelled single-camera whole-car and module details",
                   "identity_anchor": "reference.png", "brief": manifest["brief"],
                   "identity_lock": manifest.get("identity_lock"),
                   "synthetic": True, "cross_view_consistency_verified": False})
        with Image.open(output) as image:
            size = list(image.size)
        record.update(model=overview["model"], output=output.name, sha256=digest(output), size=size,
                      synthetic=True, physical_proportions_guaranteed=False,
                      generation_logic="Car2LEGO GPT service with dependency-aware camera batches of <=5; wait for all images before the next batch",
                      appearance_accepted=False, seed_supported=False,
                      reconstruction_input="semantic module reference set", views=views, shots=shots,
                      outputs={"reference.png": digest(output), "views.json": digest(view_manifest),
                               "car2lego/references/manifest.json": digest(directory / "car2lego/references/manifest.json")})
    return view_manifest


def load_corrections(directory, shots):
    path = directory / 'reference-corrections.json'
    corrections = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    previous = directory / 'car2lego/references/manifest.json'
    if corrections and previous.is_file():
        manifest = json.loads(previous.read_text(encoding='utf-8'))
        contracts = {shot['id']: shot for shot in manifest.get('expected_shots', [])}
        current = {shot.id: json.loads(json.dumps(shot.record())) for shot in shots}
        obsolete = {name for name in corrections if name in contracts and contracts[name] != current.get(name)}
        if obsolete:
            history = directory / 'correction-history' / (digest(path) + '.json')
            history.parent.mkdir(exist_ok=True)
            if not history.exists():
                shutil.copyfile(path, history)
            corrections = {name: text for name, text in corrections.items() if name not in obsolete}
            write_json(path, corrections)
    return corrections


def generate(prompt, directory, vehicle_spec=None, reference_image=None, *, review=True, max_reference_repairs=3):
    from .reference_review import review_references, repair_corrections
    from .reference_gate import assess_reference_gate
    directory = Path(directory).resolve()
    correction_path = directory / 'reference-corrections.json'
    identity = load_spec(vehicle_spec)['model'] if vehicle_spec else prompt
    corrections = load_corrections(directory, reference_plan(identity))
    review_root = directory / 'content-review' / ('run-' + uuid4().hex) if review else None
    for attempt in range(max_reference_repairs + 1):
        manifest_path = _generate_once(prompt, directory, vehicle_spec, reference_image, corrections)
        if not review:
            return manifest_path
        review_directory = review_root / f'attempt-{attempt + 1}'
        report = review_references(manifest_path, review_directory)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['content_review'] = dict(file=str((review_directory / 'report.json').relative_to(directory)),
            sha256=digest(review_directory / 'report.json'), status=report['status'])
        for name, result in report['shots'].items():
            manifest['shots'][name]['consistency'] = result
        manifest['views'] = {name: manifest['shots'][name] for name in manifest['views']}
        manifest['cross_view_consistency_verified'] = report['status'] == 'pass'
        gate = assess_reference_gate(report['shots'])
        manifest['reference_gate'] = gate
        manifest['repair_tasks'] = gate['repair_tasks']
        write_json(directory / 'reference-gate.json', gate)
        write_json(directory / 'repair-tasks.json', dict(schema='reference-repairs.v1', tasks=gate['repair_tasks']))
        write_json(manifest_path, manifest)
        image_record = json.loads((directory / 'image.json').read_text(encoding='utf-8'))
        image_record['outputs']['views.json'] = digest(manifest_path)
        image_record['content_review'] = manifest['content_review']
        write_json(directory / 'image.json', image_record)
        if gate['allowed']:
            return manifest_path
        failed = {name: result for name, result in report['shots'].items() if result['status'] != 'pass'}
        if attempt == max_reference_repairs:
            raise RuntimeError('Reference content review still needs revision after bounded repairs: ' + ', '.join(failed))
        corrections = repair_corrections(report, corrections)
        write_json(correction_path, corrections)
    raise RuntimeError('Reference repair loop did not complete')
