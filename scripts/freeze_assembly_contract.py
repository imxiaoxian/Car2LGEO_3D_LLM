"""Preserve the actual executor and token-fit evidence for an independent round."""
import argparse
from pathlib import Path
import shutil

from car2lego3d.assembly_agent import CONTEXT_VERSION, execution_hash, skill_bundle
from car2lego3d.assembly_benchmark import read
from car2lego3d.assembly_protocol import protocol_hash
from car2lego3d.common import ROOT, digest, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--budget-report", type=Path, required=True)
    args = parser.parse_args()
    suite, budget = read(args.suite), read(args.budget_report)
    current = dict(context_version=CONTEXT_VERSION, execution_hash=execution_hash(),
                   protocol_hash=protocol_hash(), skill_hash=skill_bundle()["sha256"])
    for key in ("execution_hash", "protocol_hash", "skill_hash"):
        if current[key] != suite[key]:
            raise ValueError("Suite differs from actual executor: " + key)
    if not budget["all_fit"] or suite["budgets"]["context_version"] != CONTEXT_VERSION:
        raise ValueError("Context evidence does not fit the actual version")
    destination = args.suite.parent.parent / "implementation"
    if destination.exists():
        parser.error("Implementation is already frozen; use a new suite")
    paths = [*sorted((ROOT / "src/car2lego3d").glob("assembly_*.py")),
             ROOT / "src/car2lego3d/native_ports.py", ROOT / "src/car2lego3d/common.py",
             ROOT / "src/car2lego3d/gpu_lock.py",
             ROOT / "src/car2lego3d/part_library.py", ROOT / "src/car2lego3d/ldraw_document.py",
             ROOT / "src/car2lego3d/bricklayer_audit.py",
             ROOT / "scripts/render_assembly_preview.py",
             *sorted((ROOT / "skills/vehicle-io-creation").rglob("*.md"))]
    files = {}
    for path in paths:
        name = path.relative_to(ROOT)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        files[name.as_posix()] = digest(target)
    contract = dict(**current, snapshot=suite["snapshot"], suite=str(args.suite.resolve()),
        suite_sha256=digest(args.suite), budget_report=str(args.budget_report.resolve()),
        budget_report_sha256=digest(args.budget_report), implementation=files,
        quality_accepted=False, default_changed=False)
    write_json(destination.parent / "contract.json", contract)
    print(current)


if __name__ == "__main__":
    main()
