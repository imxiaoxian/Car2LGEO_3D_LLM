"""Audit four-car delivery artifacts without turning exports into acceptance."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from zipfile import BadZipFile

from .assembly_evaluation import CORE_CASES, SUPPORTED_CASES
from .assembly_graph import IOArchive
from .common import digest, write_json

STRUCTURAL_GATES = ("archive", "digital", "motion", "completeness", "reference_repairs")


def review_inputs(directory):
    directory = Path(directory)
    paths = dict(io=directory / "04-studio/model.io",
        graph=directory / "03-lego/agent/assembly-graph.json",
        presentation=directory / "06-acceptance/presentation.json")
    result = {name + "_sha256": digest(path) if path.is_file() else None for name, path in paths.items()}
    from .assembly_evidence import snapshot_files
    from .assembly_requirements import identity
    native = directory / "03-lego/agent/checks.json"
    try:
        native_paths = [row["file"] for row in load(native, {}).get("evidence_files", [])]
    except (ValueError, KeyError, TypeError):
        native_paths = []
    resources = [directory / name for name in ("03-lego/agent/dependencies.json", "03-lego/agent/custom-parts.zip",
        "03-lego/agent/assembly.glb", "03-lego/agent/geometry.json", "03-lego/agent/geometry.dependencies.json",
        "05-presentation/scene.json")]
    result["dependencies_sha256"] = identity(snapshot_files([*resources, *native_paths]))
    try:
        images = [(directory / "06-acceptance" / row["file"]).resolve()
            for row in load(paths["presentation"], {}).get("images", [])]
    except (ValueError, KeyError, TypeError):
        images = []
    result["display_sha256"] = identity(snapshot_files(images))
    return result


def human_appearance(directory, case):
    review = load(Path(directory) / "06-acceptance/user-review.json", {})
    inputs = review_inputs(directory)
    current = (all(inputs.values()) and review.get("inputs") == inputs
        and review.get("schema") == "vehicle-appearance-review.v1" and review.get("case") == case
        and review.get("reviewer_kind") == "human" and review.get("reviewer")
        and review.get("status") in {"pass", "fail"})
    return dict(status=review["status"] if current else "unverified", current=bool(current),
        inputs=inputs, reviewer=review.get("reviewer") if current else None)


def record_appearance(case, directory, *, status, inputs, notes=""):
    """Record the user's decision only against the exact candidate shown by the UI."""
    if status not in {"pass", "fail"} or not isinstance(notes, str) or len(notes) > 4000:
        raise ValueError("Invalid appearance review")
    directory = Path(directory)
    result = audit(case, directory)
    if inputs != review_inputs(directory) or not all(inputs.values()):
        raise ValueError("Model changed; reload the candidate before reviewing")
    if (result["missing"] or not result["inspection_views_complete"]
            or any(result["gates"][key] != "pass" for key in STRUCTURAL_GATES)):
        raise ValueError("Complete the vehicle and structural checks before appearance review")
    record = dict(schema="vehicle-appearance-review.v1", case=case, status=status,
        reviewer_kind="human", reviewer="website_user", inputs=inputs, notes=notes,
        reviewed_at=datetime.now(timezone.utc).isoformat())
    # Keep prior user decisions as evidence when a revised candidate is reviewed.
    from .assembly_requirements import identity
    write_json(directory / "06-acceptance/reviews" / (identity(record) + ".json"), record)
    write_json(directory / "06-acceptance/user-review.json", record)
    result = single_report(case, directory, directory / "06-acceptance/website-evidence.json")
    write_json(directory / "06-acceptance/delivery-report.json", result)
    return result


def core_case(make, model):
    from .official_library import identity_key, identity_pattern
    brand, name = identity_key(make), identity_key(model)
    if brand in {"lamborghini", "兰博基尼"} and name.startswith("countach"):
        return "countach"
    if brand in {"ferrari", "法拉利"} and identity_pattern("F40").match(model.casefold()):
        return "f40"
    if brand in {"bmw", "宝马"} and identity_pattern("M4").match(model.casefold()):
        return "bmw-m4"
    if brand in {"lotus", "莲花", "路特斯"} and name in {"forme", "for美"}:
        return "lotus-for-me"
    if brand in {"nissan", "日产"} and identity_pattern("R34").search(model.casefold()):
        return "nissan-r34"
    if (brand in {"bugatti", "布加迪"} and identity_pattern("Chiron Pur Sport").search(model.casefold())):
        return "bugatti-chiron-pur-sport"
    return None


def load(path, fallback=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else fallback


def audit(case, directory):
    if case not in SUPPORTED_CASES:
        raise ValueError("Unsupported delivery vehicle")
    directory = Path(directory).resolve()
    agent_root = directory / "03-lego/agent"
    paths = dict(io=directory / "04-studio/model.io", blend=directory / "02-mesh/model.blend",
        reconstruction=directory / "02-mesh/calibrated.glb", semantic=directory / "02-mesh/semantic-scene.json",
        glb=agent_root / "assembly.glb", graph=agent_root / "assembly-graph.json",
        mpd=agent_root / "model.mpd", ldr=agent_root / "model.ldr", bom=agent_root / "bom.json",
        steps=agent_root / "steps.json", checks=agent_root / "checks.json", motion=agent_root / "motion.json")
    paths.update(dependencies=agent_root / "dependencies.json", custom_parts=agent_root / "custom-parts.zip")
    files = {key: dict(file=str(path), sha256=digest(path), bytes=path.stat().st_size)
             for key, path in paths.items() if path.is_file()}
    missing = sorted(set(paths) - set(files))
    gates = dict(archive="unverified", digital="unverified", motion="unverified",
        completeness="unverified", reference_repairs="unverified",
        appearance="unverified", studio="unverified", physical="unverified")
    issues = []
    graph = load(paths["graph"], {})
    if "io" in files:
        try:
            restored = IOArchive(paths["io"]).assembly
            if not restored.bom():
                raise ValueError("Empty archive")
            if graph and restored.graph()["instances"] != graph.get("instances"):
                raise ValueError("Exported IO differs from assembly graph")
            if "bom" not in files or restored.bom() != load(paths["bom"]):
                raise ValueError("Missing or inconsistent BOM")
            gates["archive"] = "pass"
        except (ValueError, KeyError, OSError, BadZipFile, RuntimeError) as error:
            gates["archive"] = "fail"
            issues.append(str(error))
    state = load(agent_root / "agent.json", {})
    modules = load(agent_root / "module-progress.json", {})
    from .assembly_evidence import module_report_current, module_tasks_current, check_current
    from .assembly_requirements import identity
    schedule_path = agent_root / "module-tasks.json"
    schedule = load(schedule_path, {})
    scheduled = state.get("schema") == "assembly-scheduled-agent.v1"
    schedule_current = (not scheduled or (state.get("module_tasks_sha256") ==
        (digest(schedule_path) if schedule_path.is_file() else None) and module_tasks_current(agent_root, schedule)))
    if (graph and schedule_current and module_report_current(agent_root, modules)
            and modules.get("revision") == graph.get("revision")
            and modules.get("graph_sha256") == identity(graph)
            and state.get("module_progress") == modules):
        gates["completeness"] = "pass" if modules.get("completion_ready") else modules.get("status", "unverified")
        gate = load(directory / "01-image/reference-gate.json", {})
        from .creation_evidence import reference_gate_current
        if gate.get("allowed") and reference_gate_current(directory / "01-image", gate):
            gates["reference_repairs"] = "pass" if all(t.get("status") == "closed" for t in modules.get("repair_tasks", [])) else "fail"
    if scheduled and not schedule_current:
        issues.append("Scheduled modules lack current final-graph candidate and connection evidence")
    for gate in ("digital", "motion"):
        measurement = load(paths["checks" if gate == "digital" else gate], {})
        observation = state.get("checks" if gate == "digital" else gate, {})
        if (graph and observation.get("ok") and observation.get("result") == measurement
                and check_current(graph, measurement)
                and measurement.get("status") in {"pass", "fail"}):
            gates[gate] = measurement["status"]
    presentation_root = directory / "06-acceptance"
    presentation = load(presentation_root / "presentation.json", {})
    images = presentation.get("images", [])
    valid_images = []
    for item in images:
        path = (presentation_root / item["file"]).resolve()
        if path.is_relative_to(presentation_root) and path.is_file() and digest(path) == item.get("sha256"):
            valid_images.append(item)
    views_complete = (len({item.get("sha256") for item in valid_images}) == len(valid_images)
        and sum(item.get("category") == "whole" for item in valid_images) >= 6
        and sum(item.get("category") == "detail" for item in valid_images) >= 8
        and presentation.get("inputs", {}).get("glb_sha256") == files.get("glb", {}).get("sha256"))
    review = load(presentation_root / "review.json", {})
    bound_review = (review.get("io_sha256") == files.get("io", {}).get("sha256")
        and review.get("graph_sha256") == files.get("graph", {}).get("sha256")
        and review.get("presentation_sha256") == (digest(presentation_root / "presentation.json") if presentation else None)
        and bool(files.get("io")) and bool(files.get("graph")))
    if bound_review:
        for gate in ("physical",):
            result = review.get(gate, {})
            if (result.get("status") in {"pass", "fail"} and result.get("reviewer")
                    and (gate != "appearance" or views_complete)):
                gates[gate] = result["status"]
    studio = dict(status="unverified", scope="not_sampled", file_opened_in_studio=False, evidence_files=[])
    sample_path = presentation_root / "studio-compatibility.json"
    if "io" in files and gates["archive"] == "pass":
        from .studio_compatibility import coverage
        studio = coverage(paths["io"], load(sample_path, {}))
        gates["studio"] = studio["status"]
    appearance = human_appearance(directory, case)
    if views_complete:
        gates["appearance"] = appearance["status"]
    accepted = not missing and views_complete and all(gates[key] == "pass" for key in
        ("archive", "digital", "motion", "completeness", "reference_repairs", "appearance", "studio"))
    from .assembly_evidence import snapshot_files
    evidence_paths = [*paths.values(), agent_root / "agent.json", agent_root / "state.json",
        agent_root / "module-progress.json", schedule_path, directory / "01-image/reference-gate.json",
        presentation_root / "presentation.json", presentation_root / "review.json",
        presentation_root / "user-review.json", sample_path]
    reference_root = directory / "01-image"
    evidence_paths.extend([reference_root / "views.json", reference_root / "car2lego/references/manifest.json"])
    evidence_paths.extend(path for folder in ("shots", "car2lego/references")
        for path in (reference_root / folder).glob('*.png'))
    evidence_paths.extend((reference_root / "content-review").rglob('report.json'))
    evidence_paths.extend((reference_root / "content-review").rglob('reference-manifest.json'))
    evidence_paths.extend(path for path in (reference_root / "content-review").rglob('*.json')
        if path.parent.name == 'batch-evidence')
    evidence_paths.extend(row["file"] for row in studio["evidence_files"])
    evidence_paths.extend((presentation_root / item["file"]).resolve() for item in valid_images)
    return dict(case=case, artifacts=str(directory), status="accepted" if accepted else "not_accepted",
        gates=gates, missing=missing, files=files, inspection_views_complete=views_complete,
        views=dict(whole=sum(i.get("category") == "whole" for i in valid_images),
                   detail=sum(i.get("category") == "detail" for i in valid_images)),
        issues=issues, physical_buildability=gates["physical"], evidence_files=snapshot_files(evidence_paths),
        review_inputs=appearance["inputs"], appearance_review_current=appearance["current"],
        studio_compatibility=studio,
        appearance_review_ready=not missing and views_complete and all(gates[key] == "pass" for key in STRUCTURAL_GATES))


def website_acceptance(path, vehicles, *, required_cases=None):
    required_cases = set(CORE_CASES if required_cases is None else required_cases)
    if (not required_cases or not required_cases.issubset(SUPPORTED_CASES)
            or len(vehicles) != len(required_cases) or {v["case"] for v in vehicles} != required_cases):
        raise ValueError("Website acceptance requires exactly the requested supported vehicles")
    if path is None:
        return dict(status="unverified", issues=["Live browser evidence not supplied"], cases=[])
    path = Path(path).resolve()
    evidence = load(path, {})
    issues, checked = [], []
    if (evidence.get("schema") != "vehicle-website-evidence.v1"
            or evidence.get("scope") != "website_transport_and_preview_only"):
        issues.append("Unsupported live browser evidence")
    entries = evidence.get("cases", [])
    if (len(entries) != len(required_cases)
            or {entry.get("case") for entry in entries} != required_cases):
        issues.append("Browser evidence must contain each requested vehicle exactly once")
    for vehicle in vehicles:
        case = vehicle["case"]
        matches = [entry for entry in entries if entry.get("case") == case]
        failures = []
        entry = matches[0] if len(matches) == 1 else {}
        if entry.get("status") != "website_transport_verified":
            failures.append("Website creation or download did not finish")
        preview = entry.get("preview", {})
        for kind in ("solid", "wireframe"):
            image = preview.get(kind, {})
            image_path = Path(image.get("file", ""))
            if (not image_path.is_absolute() or not image_path.is_file()
                    or digest(image_path) != image.get("sha256")):
                failures.append("Missing or changed " + kind + " preview")
        if (not isinstance(preview.get("foreground_ratio"), (int, float))
                or not .01 < preview["foreground_ratio"] <= 1):
            failures.append("Nonblank native preview was not verified")
        if (not preview.get("solid", {}).get("sha256")
                or preview.get("solid", {}).get("sha256") == preview.get("wireframe", {}).get("sha256")):
            failures.append("Wireframe interaction was not verified")
        delivered = vehicle.get("files", {}).get("io", {})
        downloads = [item for item in entry.get("downloads", []) if item.get("kind") == "io"]
        if len(downloads) != 1 or not delivered:
            failures.append("IO download is missing or ambiguous")
        else:
            downloaded = downloads[0]
            download_path = Path(downloaded.get("file", ""))
            if (not download_path.is_absolute() or not download_path.is_file()
                    or digest(download_path) != downloaded.get("sha256")
                    or downloaded.get("sha256") != delivered.get("sha256")
                    or download_path.stat().st_size != downloaded.get("bytes")
                    or downloaded.get("bytes") != delivered.get("bytes")):
                failures.append("Downloaded IO differs from the audited delivery")
        checked.append(dict(case=case, job_id=entry.get("job_id"),
            status="fail" if failures else "pass", issues=failures))
    passed = not issues and len(checked) == len(required_cases) and all(row["status"] == "pass" for row in checked)
    return dict(status="pass" if passed else "fail", evidence=str(path), sha256=digest(path) if path.is_file() else None,
        issues=issues, cases=checked, scope="website_transport_and_preview_only")


def single_report(case, directory, website_evidence=None):
    result = audit(case, directory)
    website = website_acceptance(website_evidence, [result], required_cases={case})
    ready = result["status"] == "accepted"
    from .assembly_evidence import snapshot_files
    evidence_paths = [item["file"] for item in result["evidence_files"]]
    if website_evidence is not None:
        evidence_paths.append(website_evidence)
        website_document = load(Path(website_evidence), {})
        for entry in website_document.get("cases", []):
            evidence_paths.extend(item["file"] for item in entry.get("downloads", []) if item.get("file"))
            evidence_paths.extend(entry.get("preview", {}).get(kind, {}).get("file")
                for kind in ("solid", "wireframe") if entry.get("preview", {}).get(kind, {}).get("file"))
    return dict(result, schema="single-vehicle-delivery.v1", vehicle_delivery_accepted=ready,
        accepted=ready and website["status"] == "pass",
        status="accepted" if ready and website["status"] == "pass" else "not_accepted",
        website_end_to_end=website, evidence_files=snapshot_files(evidence_paths))


def report(cases, website_evidence=None, *, required_cases=None):
    required_cases = set(CORE_CASES if required_cases is None else required_cases)
    if not required_cases or not required_cases.issubset(SUPPORTED_CASES) or set(cases) != required_cases:
        raise ValueError("Delivery report requires all four core vehicles or an explicit complete supported roster")
    rows = [audit(case, cases[case]) for case in sorted(cases)]
    delivery_passed = all(row["status"] == "accepted" for row in rows)
    website = website_acceptance(website_evidence, rows, required_cases=required_cases)
    return dict(schema="vehicle-fleet-delivery.v3", required_cases=sorted(required_cases), accepted=delivery_passed and website["status"] == "pass",
        vehicle_delivery_accepted=delivery_passed, vehicles=rows, website_end_to_end=website)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--cases", type=Path)
    target.add_argument("--case", choices=sorted(SUPPORTED_CASES))
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--website-evidence", type=Path)
    args = parser.parse_args()
    if args.case:
        if not args.artifacts:
            parser.error("--case requires --artifacts")
        result = single_report(args.case, args.artifacts, args.website_evidence)
    else:
        result = report(load(args.cases), args.website_evidence)
    write_json(args.output, result)
    print(json.dumps(dict(accepted=result["accepted"], vehicles=[
        dict(case=row["case"], status=row["status"], missing=row["missing"]) for row in result.get("vehicles", [result])])))


if __name__ == "__main__":
    main()
