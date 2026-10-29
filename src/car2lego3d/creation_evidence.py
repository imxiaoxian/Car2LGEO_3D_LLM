"""Read current creation evidence for the desktop API, including historical gaps."""
import json
from pathlib import Path

from .common import digest


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def reference_gate_current(directory, gate):
    """A previous verdict cannot describe replaced pixels or a different shot set."""
    from .reference_gate import assess_reference_gate
    directory = Path(directory).resolve()
    try:
        manifest = read(directory / "views.json", {})
        review = manifest["content_review"]
        report_path = (directory / review["file"]).resolve()
        if not report_path.is_relative_to(directory) or digest(report_path) != review["sha256"]:
            return False
        report = read(report_path, {})
        for source in report.get("reused_batch_reviews", []):
            path = (report_path.parent / source["file"]).resolve()
            if not path.is_relative_to(directory) or digest(path) != source["sha256"]:
                return False
        snapshot_path = (report_path.parent / report["manifest_snapshot"]["file"]).resolve()
        if not snapshot_path.is_relative_to(directory) or digest(snapshot_path) != report["manifest_sha256"]:
            return False
        snapshot = read(snapshot_path, {})
        for key in ("expected_shots", "camera_contract", "brief", "identity_lock", "schema_version", "synthetic", "identity_anchor"):
            if manifest.get(key) != snapshot.get(key):
                return False
        shots = manifest["shots"]
        if not shots or set(report["shots"]) != set(shots) or gate != assess_reference_gate(report["shots"]):
            return False
        for name, shot in shots.items():
            path = (directory / shot["file"]).resolve()
            if (not path.is_relative_to(directory) or shot["sha256"] != report["shots"][name]["sha256"]
                    or digest(path) != shot["sha256"]
                    or {k: v for k, v in shot.items() if k != "consistency"}
                    != {k: v for k, v in snapshot["shots"][name].items() if k != "consistency"}):
                return False
        source = directory / "car2lego/references/manifest.json"
        if source.is_file():
            images = read(source, {})["images"]
            if len(images) != len(shots) or {row["view"] for row in images} != set(shots):
                return False
            for row in images:
                path = (directory / "car2lego" / row["path"]).resolve()
                if (not path.is_relative_to(directory) or row["sha256"] != shots[row["view"]]["sha256"]
                        or digest(path) != row["sha256"]):
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def evidence(artifacts, request=None):
    artifacts = Path(artifacts)
    images, agent = artifacts / "01-image", artifacts / "03-lego/agent"
    gate = read(images / "reference-gate.json")
    if not gate:
        gate = dict(schema="reference-gate.v1", status="unverified", allowed=False,
            image_correct=False, appearance_accepted=False, blocked_views=[], repair_tasks=[],
            reason="No completed graded reference review for this task")
    elif not reference_gate_current(images, gate):
        gate = {**gate, "raw_gate": gate, "status": "unverified", "allowed": False,
            "image_correct": False, "blocked_views": [], "stale": True,
            "reason": "Reference pixels or review evidence changed; current review required"}
    repairs = read(images / "repair-tasks.json", {}).get("tasks", gate.get("repair_tasks", []))
    from .baseline_detail import detail_standard, scope_repairs
    standard = detail_standard(request or {})
    repairs, excluded_repairs = scope_repairs(repairs, standard)
    modules = read(agent / "module-progress.json")
    from .assembly_evidence import files_current, module_report_current
    if modules and not module_report_current(agent, modules):
        modules = {**modules, "status": "unverified", "completion_ready": False, "stale": True,
            "complete": 0,
            "modules": [{**m, "status": "unverified", "unverified": ["current_revision_check_required"]}
                        for m in modules.get("modules", [])]}
    if not modules:
        modules = dict(schema="vehicle-modules.v1", status="unverified", applicable=True,
            completion_ready=False, complete=0, required=None, modules=[], reason="No vehicle module evidence")
    # Only host-bound module reviews may close transferred image defects.
    repaired = {item["id"]: item for item in modules.get("repair_tasks", [])} if not modules.get("stale") else {}
    repairs = [repaired.get(item["id"], item) for item in repairs]
    delivery = read(artifacts / "06-acceptance/delivery-report.json", {})
    delivery_current = bool(delivery.get("evidence_files")) and files_current(delivery["evidence_files"])
    gates = {key: delivery.get("gates", {}).get(key, "unverified") if delivery_current else "unverified"
             for key in ("archive", "digital", "motion", "appearance", "studio", "physical")}
    gates["modules"] = modules["status"]
    gates["website"] = delivery.get("website_end_to_end", {}).get("status", "unverified") if delivery_current else "unverified"
    gates["reference_repairs"] = "pass" if gate.get("allowed") and all(r.get("status") == "closed" for r in repairs) else "unverified"
    accepted = (delivery_current and delivery.get("accepted") is True and gates["website"] == "pass"
        and delivery.get("status") == "accepted" and modules.get("completion_ready") is True
        and gates["reference_repairs"] == "pass")
    schedule = read(agent / "module-tasks.json", {})
    tasks = None
    if schedule.get("schema") in {"vehicle-assembly-schedule.v1", "vehicle-assembly-schedule.v2"}:
        from .assembly_graph import Assembly
        from .assembly_evidence import module_task_current
        state = read(agent / "state.json", {})
        try:
            assembly = Assembly(state["text"], state["source_sha256"])
            assembly.revision, assembly.joints = state["revision"], state["joints"]
            graph = assembly.graph()
        except (KeyError, ValueError, TypeError):
            graph = None
        rows = []
        for task in schedule.get("tasks", {}).values():
            proof = task.get("measurement", {})
            current = bool(graph and module_task_current(graph, state.get("module_bindings", {}), task))
            status = "unverified" if task.get("status") == "assembled" and not current else task.get("status", "unverified")
            rows.append(dict(id=task["id"], dependencies=task["dependencies"],
                status=status,
                attempts=len(task["attempts"]), reason=task.get("reason"),
                candidates=proof.get("candidates"), provisional=True, evidence_current=current))
        tasks = dict(schema=schedule["schema"], planner=schedule["planner"], active=schedule.get("active"),
            input_hash=schedule["input_hash"], required=schedule["required"], tasks=rows,
            complete=sum(t["status"] == "assembled" and t["evidence_current"] for t in rows),
            acceptance="provisional_mechanical_only")
    return dict(reference_gate=gate, repair_tasks=repairs, excluded_reference_repairs=excluded_repairs,
        detail_standard=standard, module_progress=modules, module_tasks=tasks,
        acceptance=dict(status="accepted" if accepted else "not_accepted", accepted=accepted, gates=gates,
            evidence_current=delivery_current,
            appearance_review_ready=delivery_current and delivery.get("appearance_review_ready") is True,
            review_inputs=delivery.get("review_inputs") if delivery_current else None))
