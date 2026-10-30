"""Compare live candidate geometry and joint evidence without changing a run."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from car2lego3d.assembly_candidates import structure
from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_mounts import owned_members
from car2lego3d.common import digest, write_json


def load(path):
    state = json.loads(path.read_text(encoding="utf-8"))
    assembly = Assembly(state["text"], state["source_sha256"])
    assembly.revision, assembly.joints = state["revision"], state["joints"]
    return SimpleNamespace(assembly=assembly, module_bindings=state["module_bindings"])


def joints(tools, module):
    graph = tools.assembly.graph()
    members = owned_members(graph, tools.module_bindings, module)
    return {key: value for key, value in graph["joints"].items() if key in members}


def audit(source, module):
    state_path = source / "state.json"
    before = digest(state_path)
    current = load(state_path)
    fingerprint = structure(current, module)
    candidate = source / "module-candidates" / module / fingerprint
    report = dict(schema="candidate-remeasurement-audit.v1", module=module,
        source_revision=current.assembly.revision, source_sha256=before,
        structure_id=fingerprint, existing_record=candidate.is_dir(),
        scope="Read-only cache diagnosis; no module or vehicle acceptance.")
    if (candidate / "state.json").is_file():
        previous = load(candidate / "state.json")
        record = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        changed = joints(current, module) != joints(previous, module)
        report.update(snapshot_revision=previous.assembly.revision,
            snapshot_status=record["measurement"]["status"],
            joints_changed=changed, previous_joints=joints(previous, module),
            current_joints=joints(current, module),
            remeasurement_blocked=changed,
            reason="capture rejects an existing geometry ID before saving revised joint evidence"
                if changed else "no joint-only change observed")
    if digest(state_path) != before:
        raise ValueError("Live source changed during audit; retry")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new output file")
    result = audit(args.source, args.module)
    write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
