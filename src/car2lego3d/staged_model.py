"""Checkpointed Astra/Blender reconstruction using module-specific reference sets."""
import hashlib
import json
from pathlib import Path
import shutil

from .common import ROOT, digest, stage, write_json
from .dialogue import ask_json, image_content
from .references import load_references
from .reference_gate import phase_modules as repair_phase_modules
from .semantic import load_scene, ownership_report
from .staged_geometry import phase_geometry
from .vehicle import load_spec, spec_record


PHASES = (
    ("running-gear", ("wheels", "steering", "suspension"),
     "Create the four correctly named tyres/rims, chassis rails, crossmembers and wheel mounts. "
     "Include steering and suspension only as required by the detail standard. "
     "Plan axle and cockpit hardpoints. Other phases add bodywork. No body shell in this phase."),
    ("body", ("body",),
     "Create the modular body shell and wheel arches, sills and shared front/cabin/rear boundary coordinates. "
     "Leave real door, glass and cover openings; no opaque cabin block. Preserve running gear."),
    ("cabin", ("interior", "cabin"),
     "Create the cabin: floor, roof, pillars, cowl, rear buttresses, seats, dashboard, steering wheel "
     "and console at baseline IO detail level. Hidden rails and pedal hardware are not mandatory. "
     "Fit to existing body boundaries."),
    ("doors-glass", ("doors", "glazing", "hinges"),
     "Create every door assembly with its panel, frame and hinge at baseline IO detail level. "
     "Include glass, seals, trim and latches only where the standard requires them. Any included door glass moves with its door. "
     "Fixed quarter glass belongs to the body. Fit windscreen and rear glass to actual openings. "
     "Set realistic door pivot axes and ranges; all assemblies are initially CLOSED."),
    ("front", ("front", "lights"),
     "Complete nose, fenders, hood, bumper and baseline-style front lamp modules. Match the cowl and A-pillar "
     "boundaries; include hood hinge, compartment and pop-up lamp mechanisms when represented in the baseline."),
    ("rear", ("rear", "powertrain", "hinges"),
     "Complete rear deck, fenders, fascia and taillamps at baseline IO detail level. Include the represented "
     "powertrain/cargo bay and cover mechanism without inventing additional hidden internals. "
     "Preserve shared rear glass/buttress boundaries. F40 rear clamshell includes wing and rear fenders."),
    ("details", ("body", "doors", "lights"),
     "Inspect and finish all actual module connections and styling details, intakes, grilles, mirrors, "
     "wipers, handles, trim, exhaust and variant-specific features. Fix glass seating and front/rear cabin "
     "transitions. Preserve existing useful geometry and the baseline's features; do not replace the car with a generic body."),
)

CONTRACT = """
This request is ONE phase in a staged build. Return {analysis, code} with a PATCH that modifies
the existing scene. Only the first phase may clear the scene. Preserve prior working geometry.
Each object needs a stable descriptive name and car_role. Use EMPTY parents for modules and
each moving assembly. Every opening door, cover, steerable hub and suspension carrier must have
car_joint with a dict: {type:'revolute'|'prismatic', frame:'parent', origin_m:[x,y,z], axis:[unit_x,unit_y,unit_z],
limits:[lower,upper], purpose:'door'|'hood'|'rear_cover'|'steering'|'suspension'|'rolling'|'lamp'}.
Revolute limits are radians, prismatic limits metres; origin and axis are in the PARENT frame.
Set the closed/rest transform as the object's matrix; no animation is required. Tyres roll on hubs.
Set car_ports to a list of {id, position_m, axis, kind, provenance:'design-inference'} for real planned
attachment interfaces. Do not claim these are measured or standard LEGO connections.
Any included glass, lamp lenses, curved panels and cabin trim remain distinct meshes. Do not merge the scene.
Door-panel and glass seating is checked over the FULL raw mesh perimeter, not only declared seams
or lower anchors. Use shared opening control lines for the front, rear, sill and frame. Keep a
measurable open mid-surface with finite-thickness modifiers so the source perimeter is unambiguous.
Use the SAME control curve on both adjoining surfaces, including the body-side jamb/fender return.
Independent jamb and fender rails can disagree even when all door vertices project to the body.
After repeated projection failure, diagnose both opening surfaces and refit the inconsistent local
returns from the original sill/pillar controls. Preserve the designed crease and all other detail;
do not increase tolerances, inflate seals or add a broad cover strip to conceal the mismatch.
For a small existing boundary mismatch you may use host helper car2lego_fit_boundary(object_name,
group_name,target_names,max_distance=.05,tolerance=.006,refinements=3). It projects only the actual
open source boundary group and adaptively adds edge vertices where straight chords leave a curved
target surface. It preserves the source on failure; never apply it to native standard LEGO parts.
Use plain Python dict/list ID properties (bpy supports them); no file access or dynamic code.
Host saves evaluated geometry, materials, hierarchy, boundaries and joints after each phase.
"""

EXPORT_NAMES = {"semantic-scene.json", "scene-metrics.json", "model-quality.json"}


def exported_paths(directory):
    return [p for p in Path(directory).iterdir() if p.is_file() and (
        p.suffix in {".blend", ".glb", ".obj", ".mtl", ".png"} or p.name in EXPORT_NAMES)]


def validate_response(response):
    if (not isinstance(response, dict) or not isinstance(response.get("code"), str)
            or not isinstance(response.get("analysis"), str)):
        raise ValueError("Modelling response requires string analysis and code fields")
    return response


def preserve_objects(before, after):
    previous = {n["id"]: n for n in before["nodes"]} if before else {}
    current = {n["id"]: n for n in after["nodes"]}
    missing = set(previous) - set(current)
    if missing:
        raise ValueError("Phase removed existing semantic objects: " + ", ".join(sorted(missing)))
    emptied = [name for name, node in previous.items() if node.get("geometry") and not current[name].get("geometry")]
    if emptied:
        raise ValueError("Phase removed existing geometry: " + ", ".join(sorted(emptied)))


def review_phase(blender, output, images, spec, model, prompt, semantic, quality, provenance):
    from .blender_mcp import MODEL_TIMEOUT, render_code, validate_code
    from .model_quality import assess_model, new_failures
    before = output / "before-review"
    before.mkdir(exist_ok=True)
    original_paths = exported_paths(output)
    for path in original_paths:
        shutil.copyfile(path, before / path.name)
    report = dict(status="unavailable", appearance_accepted=False, physical_verified=False)
    try:
        response = validate_response(ask_json([
            {"role": "system", "content": prompt + "\nReview ONLY the current phase and completed modules. "
             "Compare references, actual current renders and scene diagnostics. Return {analysis, code}. "
             "Code is a corrective PATCH, or an empty string if no concrete correction is needed. "
             "Do not rebuild or remove previous modules. Planned later modules may be absent."},
            {"role": "user", "content": [{"type": "text", "text": json.dumps(dict(
                vehicle=spec, phase=provenance['phase'], geometry_checks=quality,
                nodes=[{k: n.get(k) for k in ('id', 'parent', 'role', 'joint')} for n in semantic['nodes']]))},
                *image_content(images), *image_content({"CURRENT_" + view: output / f"render-{view}.png"
                    for view in ('front', 'back', 'left', 'right', 'three-quarter', 'rear-quarter')})]}],
            output, "visual-review", max_tokens=18000, timeout=MODEL_TIMEOUT, model=model,
            stream=True, reasoning_effort="medium", transient_retries=2, reuse_completed=True))
        report.update(analysis=response['analysis'], status="reviewed", patched=bool(response['code'].strip()))
        if response['code'].strip():
            code = validate_code(response['code'])
            (output / 'visual-refine.py').write_text(code, encoding='utf-8')
            blender.execute(code, 'visual-refine-' + provenance['phase'])
            blender.execute(render_code(output, provenance={**provenance,
                'visual_patch_sha256': digest(output / 'visual-refine.py')}), 'review-export-' + provenance['phase'])
            refined_scene = load_scene(output / 'semantic-scene.json')
            preserve_objects(semantic, refined_scene)
            if not ownership_report(refined_scene)['passed']:
                raise ValueError('Visual patch detached a moving window from its door')
            refined = assess_model(output, spec)
            regressions = new_failures(quality, refined)
            if regressions:
                raise ValueError('Visual patch introduced geometry failures: ' + ', '.join(regressions))
            semantic, quality = refined_scene, refined
    except (RuntimeError, ValueError, SyntaxError, KeyError, TypeError) as error:
        rejected = output / 'rejected-review'
        rejected.mkdir(exist_ok=True)
        for path in exported_paths(output):
            shutil.copyfile(path, rejected / path.name)
        for path in original_paths:
            shutil.copyfile(before / path.name, path)
        blender.execute('import bpy\nbpy.ops.wm.open_mainfile(filepath=' + repr(str(output / 'model.blend')) + ')',
                        'restore-review-' + provenance['phase'])
        report.update(status="rejected", reason=str(error), scene_restored=True)
    write_json(output / 'visual-review-report.json', report)
    return semantic, quality, report


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def checkpoint_identity(checkpoint):
    return fingerprint(dict(key=checkpoint["key"], outputs=checkpoint["outputs"]))


def phase_references(manifest_path, modules):
    manifest, selected = load_references(manifest_path, modules)
    _, all_images = load_references(manifest_path)
    images = {**{n: p for n, p in all_images.items()
        if n in {"overview", "front", "back", "left", "right", "top"}}, **selected}
    repairs = [r for r in manifest.get("repair_tasks", [])
        if r.get("status") != "closed" and set(repair_phase_modules(r)) & set(modules)]
    context = dict(brief=manifest.get("brief"),
        images={name: dict(sha256=digest(path), state=manifest["shots"][name].get("state"))
                for name, path in images.items()}, reference_repairs=repairs)
    return images, context


def phase_checkpoint_key(inputs, phase, reference_context, previous):
    # The full manifest remains provenance; unrelated image/audit changes do not
    # invalidate a phase that consumes identical images, states and repairs.
    execution = {key: value for key, value in inputs.items() if key != "manifest_sha256"}
    return fingerprint(dict(inputs=execution, phase=phase, references=reference_context, previous=previous))


def phase_quality_issues(quality, phase):
    position = [name for name, _, _ in PHASES].index(phase)
    deferred = set()
    if position == 0:
        deferred.update({"overall_length", "front_origin", "body_role", "body_width_excluding_mirrors"})
    if position < 2:
        deferred.update({"roof_role", "roof_height"})
    if position < 3:
        deferred.add("door_window_assemblies")
    # Shared edges can target modules introduced by later phases.
    return [check for check in quality["checks"] if not check["passed"]
        and check["name"] not in deferred
        and not (position < len(PHASES) - 1 and ".connection." in check["name"])]


def require_phase_quality(quality, phase):
    issues = phase_quality_issues(quality, phase)
    if issues:
        raise ValueError("Phase geometry checks require repair: " + json.dumps(dict(
            phase=phase, failed_count=len(issues), issues=issues[:12],
            additional_issues=len(issues) - min(12, len(issues)), full_report="model-quality.json")))


def reconstruct(manifest_path, directory, vehicle_spec, review=True):
    from .blender_mcp import BlenderSession, MODEL_TIMEOUT, modelling_prompt, modelling_model, render_code, validate_code
    from .baseline_detail import detail_standard, scope_repairs
    from .model_quality import assess_model
    directory = Path(directory).resolve()
    manifest, _ = load_references(manifest_path)
    spec, model = load_spec(vehicle_spec), modelling_model()
    standard = detail_standard(spec)
    prompt = modelling_prompt(standard)
    inputs = dict(backend="gpt-blender-staged", model=model, manifest_sha256=digest(manifest_path),
                  vehicle_spec=spec_record(vehicle_spec), phase_contract=fingerprint([PHASES, CONTRACT, prompt]),
                  detail_standard=standard,
                  visual_review_requested=review, checkpoint_version=5,
                  exporter_sha256=digest(ROOT / "scripts/blender_export_render.py"),
                  mesh_guard_sha256=digest(ROOT / "scripts/blender_mesh_guard.py"),
                  boundary_fit_sha256=digest(ROOT / "scripts/blender_boundary_fit.py"),
                  geometry_context_sha256=digest(Path(__file__).with_name("staged_geometry.py")))
    with stage(directory, "reconstruction", inputs) as record:
        previous, reports, previous_key = None, [], None
        with BlenderSession(directory) as blender:
            for index, (name, modules, task) in enumerate(PHASES):
                output = directory / "checkpoints" / name
                output.mkdir(parents=True, exist_ok=True)
                images, reference_context = phase_references(manifest_path, modules)
                reference_context["reference_repairs"], reference_context["excluded_reference_repairs"] = scope_repairs(
                    reference_context["reference_repairs"], standard)
                key = phase_checkpoint_key(inputs, name, reference_context, previous_key)
                checkpoint = output / "checkpoint.json"
                cached = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
                if cached.get("key") == key and all((output / n).is_file() and digest(output / n) == h
                                                    for n, h in cached.get("outputs", {}).items()) and cached.get("outputs"):
                    load_scene(output / "semantic-scene.json")
                    require_phase_quality(json.loads((output / "model-quality.json").read_text(encoding="utf-8")), name)
                    blender.execute("import bpy\nbpy.ops.wm.open_mainfile(filepath=" + repr(str(output / "model.blend")) + ")", "restore-" + name)
                    previous, previous_key = output, checkpoint_identity(cached)
                    reports.append(cached)
                    continue
                scene = load_scene(previous / "semantic-scene.json") if previous else None
                geometry_context = phase_geometry(scene, name)
                write_json(output / "input-geometry.json", geometry_context)
                phase_prompt = prompt.replace("Clear all scene objects before building.", "Preserve the existing scene.") + CONTRACT
                if index == 0:
                    phase_prompt += "\nThis is the FIRST phase: clear startup objects before building the running gear."
                payload = dict(vehicle=spec, phase=name, task=task, existing_geometry=geometry_context,
                    detail_standard=standard, excluded_reference_repairs=reference_context["excluded_reference_repairs"],
                    brief=manifest.get("brief"), reference_states={n: manifest["shots"][n].get("state") for n in images},
                    reference_repairs=reference_context["reference_repairs"],
                    repair_policy="Correct these localized reference defects in actual geometry. Preserve raw image findings. "
                        "Do not copy defective details. Repair closure requires independent host checks of the exported assembly.")
                write_json(output / "phase-input.json", payload)
                messages = [{"role": "system", "content": phase_prompt},
                    {"role": "user", "content": [{"type": "text", "text": json.dumps(payload)}, *image_content(images)]}]
                response = None
                for attempt in range(1, 4):
                    write_json(directory / "modelling-progress.json", dict(phase=name, phase_index=index + 1,
                        phases=len(PHASES), attempt=attempt, status="modelling", model=model))
                    try:
                        response = validate_response(ask_json(messages, output, f"attempt-{attempt}", max_tokens=24000, timeout=MODEL_TIMEOUT,
                            reuse_completed=True, model=model, stream=True, reasoning_effort="medium", transient_retries=2))
                        code = validate_code(response["code"])
                        (output / f"attempt-{attempt}.py").write_text(code, encoding="utf-8")
                        blender.execute(code, f"{name}-{attempt}")
                        provenance = {**inputs, "phase": name, "code_sha256": digest(output / f"attempt-{attempt}.py")}
                        blender.execute(render_code(output, provenance=provenance), "export-" + name)
                        semantic = load_scene(output / "semantic-scene.json")
                        preserve_objects(scene, semantic)
                        if name in {"doors-glass", "front", "rear", "details"} and not ownership_report(semantic)["passed"]:
                            raise ValueError("Moving side glass needs a door_assembly parent with a valid joint")
                        quality = assess_model(output, spec, body_style=(manifest.get('brief') or {}).get('body_style'))
                        review_report = dict(status='not_requested', appearance_accepted=False)
                        if review:
                            semantic, quality, review_report = review_phase(blender, output, images, spec, model,
                                phase_prompt, semantic, quality, provenance)
                        require_phase_quality(quality, name)
                        cached = dict(key=key, phase=name, analysis=response["analysis"], attempt=attempt,
                            visual_review=review_report, geometry_status=quality['status'],
                            outputs={p.name: digest(p) for p in exported_paths(output) + [output / 'visual-review-report.json']
                                     if p.is_file()})
                        write_json(checkpoint, cached)
                        break
                    except (RuntimeError, ValueError, SyntaxError, KeyError, TypeError) as error:
                        write_json(output / f"attempt-{attempt}-failure.json", dict(error=str(error)))
                        if previous:
                            blender.execute("import bpy\nbpy.ops.wm.open_mainfile(filepath=" + repr(str(previous / "model.blend")) + ")", "rollback-" + name)
                        else:
                            blender.execute("import bpy\nbpy.ops.object.select_all(action='SELECT')\nbpy.ops.object.delete(use_global=False)", "rollback-initial")
                        if attempt == 3:
                            write_json(directory / "modelling-progress.json", dict(phase=name, status="algorithm_diagnosis_required", error=str(error)))
                            raise
                        if response is not None:
                            messages.append({"role": "assistant", "content": json.dumps(response)})
                        messages.append({"role": "user", "content": "The host restored the PREVIOUS phase. Correct this full phase patch. Failure: " + str(error)[-7000:]})
                previous, previous_key = output, checkpoint_identity(cached)
                reports.append(cached)
            for path in exported_paths(previous):
                shutil.copyfile(path, directory / path.name)
            quality = assess_model(directory, spec, body_style=(manifest.get("brief") or {}).get("body_style"))
            require_phase_quality(quality, "details")
            write_json(directory / "modelling-progress.json", dict(status="draft", phase="complete", model=model))
            record.update(model=model, phases=reports, semantic_scene="semantic-scene.json",
                geometry_diagnostics=dict(status=quality["status"], failed_checks=quality["failed_checks"]),
                visual_review_requested=review, appearance_accepted=False, physical_verified=False,
                outputs={p.name: digest(p) for p in directory.iterdir() if p.suffix in {".blend", ".glb", ".png"} or p.name == "semantic-scene.json"})
    return directory / "calibrated.glb"
