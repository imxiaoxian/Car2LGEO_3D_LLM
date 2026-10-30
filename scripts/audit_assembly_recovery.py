"""Record immutable-source and module-ownership evidence for a recovered run."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_requirements import binding_conflicts
from car2lego3d.assembly_schedule import module_signature
from car2lego3d.common import digest, write_json


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def view(state):
    assembly = Assembly(state["text"], state["source_sha256"])
    assembly.revision, assembly.joints = state["revision"], state["joints"]
    return SimpleNamespace(assembly=assembly, module_bindings=state["module_bindings"])


def audit(directory):
    directory = Path(directory).resolve()
    recovery_path = directory / "recovery.json"
    recovery = read(recovery_path)
    source = Path(recovery["source"])
    old_state, state = read(source / "state.json"), read(directory / "state.json")
    old, current = view(old_state), view(state)
    schedule = read(directory / "module-tasks.json")
    source_files = {name: dict(expected_sha256=expected,
        actual_sha256=digest(source / name), unchanged=digest(source / name) == expected)
        for name, expected in recovery["source_files"].items()}
    carried = []
    for name in recovery["carried_modules"]:
        previous, latest = module_signature(old, name), module_signature(current, name)
        carried.append(dict(module=name, source_signature=previous,
            current_signature=latest, unchanged=bool(previous and previous == latest),
            rechecked_status=schedule["tasks"][name].get("measurement", {}).get("status")))
    ownership = {name: binding_conflicts(name, binding, current.module_bindings)
        for name, binding in current.module_bindings.items()}
    quarantined = []
    for item in recovery["quarantined_bindings"]:
        name = item["module"]
        binding = current.module_bindings.get(name)
        quarantined.append(dict(module=name, recovery_reason=item["reason"],
            source_binding=item["binding"], current_binding=binding,
            independent_replacement=bool(binding and not ownership[name]),
            status="pending_independent_binding" if not binding else
                "independent_binding_present" if not ownership[name] else "conflicting_binding"))
    passed = (all(item["unchanged"] for item in source_files.values())
        and all(item["unchanged"] and item["rechecked_status"] == "pass" for item in carried)
        and not any(ownership.values()))
    return dict(schema="assembly-recovery-audit.v1", observed_at=datetime.now(timezone.utc).isoformat(),
        read_only=True, directory=str(directory), recovery_sha256=digest(recovery_path),
        state_sha256=digest(directory / "state.json"), revision=state["revision"],
        source_files=source_files, carried_modules=carried, ownership_conflicts=ownership,
        quarantined_modules=quarantined, rechecked_candidates=recovery["rechecked_candidates"],
        recovery_integrity="pass" if passed else "fail",
        scheduler=dict(active=schedule.get("active"), complete=schedule["complete"], required=schedule["required"]),
        acceptance="unverified", limits="Recovery integrity only; no vehicle, appearance, motion or Studio acceptance.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory)
    write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["recovery_integrity"] == "pass" else 1)
