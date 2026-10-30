"""Bind verification to exact assembly and dependency bytes, including absent files."""
from pathlib import Path

from .common import digest


SCHEDULE_SCHEMAS = {"vehicle-assembly-schedule.v1", "vehicle-assembly-schedule.v2"}


def snapshot_files(paths):
    return [dict(file=str(path), sha256=digest(path) if path.is_file() else None)
            for path in sorted({Path(p).resolve() for p in paths})]


def files_current(records):
    if not isinstance(records, list):
        return False
    try:
        return records == snapshot_files(item["file"] for item in records)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def check_current(graph, report):
    from .assembly_requirements import identity
    return (report.get("revision") == graph["revision"]
        and report.get("graph_sha256") == identity(graph)
        and files_current(report.get("evidence_files")))


def module_report_current(directory, report):
    import json
    from .assembly_graph import Assembly
    from .assembly_requirements import identity
    try:
        state = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))
        assembly = Assembly(state["text"], state["source_sha256"])
        assembly.revision, assembly.joints = state["revision"], state["joints"]
        return (report.get("graph_sha256") == identity(assembly.graph())
            and report.get("bindings_sha256") == identity(state.get("module_bindings", {}))
            and files_current(report.get("evidence_files")))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def module_tasks_current(directory, schedule):
    """Require every scheduled mechanical check to describe the final surrounding graph."""
    import json
    from .assembly_graph import Assembly
    from .assembly_requirements import identity
    try:
        state = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))
        assembly = Assembly(state["text"], state["source_sha256"])
        assembly.revision, assembly.joints = state["revision"], state["joints"]
        graph_hash, bindings_hash = identity(assembly.graph()), identity(state.get("module_bindings", {}))
        tasks = schedule["tasks"]
        required = schedule["contract"]["mission"]["vehicle_contract"]["modules"]
        if (schedule.get("schema") not in SCHEDULE_SCHEMAS or not required
                or set(tasks) != set(required) or schedule.get("complete") != len(required)
                or schedule.get("required") != len(required)):
            return False
        seen = set()
        for name, task in tasks.items():
            proof = task.get("measurement", {})
            if (task.get("id") != name or task.get("status") != "assembled"
                    or not set(task["dependencies"]) <= seen or task["requirement"] != required[name]
                    or proof.get("status") != "pass" or proof.get("graph_sha256") != graph_hash
                    or proof.get("bindings_sha256") != bindings_hash
                    or (proof.get("candidates") or {}).get("status") != "pass"
                    or not files_current(proof.get("native_evidence"))):
                return False
            seen.add(name)
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def module_task_current(graph, bindings, task):
    """Check one module measurement against the exact current graph and native inputs."""
    from .assembly_mounts import owned_members, semantic_node
    from .assembly_requirements import identity

    proof = task.get("measurement", {})
    name = task.get("id")
    binding = bindings.get(name)
    if not binding or proof.get("status") != "pass":
        return False
    try:
        members = owned_members(graph, bindings, name)
        signature = identity(dict(binding=binding,
            instances=[semantic_node(node) for node in graph["instances"] if node["id"] in members],
            joints={key: value for key, value in graph["joints"].items() if key in members}))
        return (proof.get("signature") == signature
            and proof.get("graph_sha256") == identity(graph)
            and proof.get("bindings_sha256") == identity(bindings)
            and files_current(proof.get("native_evidence")))
    except (KeyError, TypeError, ValueError):
        return False
