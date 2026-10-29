"""Independent BrickWright/BrickLayer checks on the exact exported LDraw parts."""
import argparse
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import warnings

from .common import write_json

VERSION = "bricklayer-audit.v1"
LIMITATIONS = [
    "Digital collision, ground support and connector checks only; no physical acceptance.",
    "Ground support does not check tipping, sliding, clutch strength or assembly access.",
    "Missing parts, import warnings or unavailable native checks prevent a complete pass.",
    "Appearance and articulated motion require separate checks.",
]


def inspect_text(text, library=None):
    """Run only in the worker: native FCL failures must not kill the web service."""
    import bricklayer as bl
    from .studio import assembly_sequence

    sequence = assembly_sequence(text)
    expected = sum(row[0] == "1" for row in sequence)
    if any(line.split()[:2] == ["0", "FILE"] for line in text.splitlines()):
        raise ValueError("Audit requires the final flat LDraw, not submodel references")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build = bl.from_ldraw(text, ldraw_library=library)
        leaves = build.flattened_placements
        indices = {id(leaf): i for i, leaf in enumerate(leaves)}
        collisions = [[indices[id(a)], indices[id(b)]] for a, b in build.colliding_placements]
        floating = [indices[id(leaf)] for leaf in build.floating_placements]
        components = [[indices[id(leaf)] for leaf in group] for group in build.connected_components]
    messages = list(dict.fromkeys(str(item.message) for item in caught))
    complete = expected > 0 and build.brick_count == expected and not messages
    gates = dict(collisions="fail" if collisions else "pass",
        ground_support="fail" if floating else "pass",
        connectivity="pass" if len(components) == 1 else "fail",
        coverage="pass" if complete else "unverified")
    problems = sorted({i for pair in collisions[:100] for i in pair} | set(floating[:100]))
    rows = [row for row in sequence if row[0] == "1"]
    locations = {str(i): dict(part=rows[i][-1], color=rows[i][1],
        position_ldu=[float(value) for value in rows[i][2:5]]) for i in problems} if complete else {}
    return dict(status="fail" if collisions or complete and "fail" in gates.values()
        else "pass" if complete else "unverified",
        complete=complete, expected_parts=expected, imported_parts=build.brick_count,
        gates=gates, warnings=messages, collision_count=len(collisions),
        collision_pairs=collisions[:100], floating_count=len(floating), floating_parts=floating[:100],
        component_count=len(components), components=[group[:100] for group in components[:100]],
        problem_parts=locations, diagnostic_limit=100,
        index_space="zero_based_flat_ldraw" if complete else "imported_subset_only")


def audit_ldraw(source, output, *, library=None, mode=None, timeout=120):
    """Save a source-bound report; partial imports and worker errors never pass."""
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError("Audit output must not overwrite its input")
    mode = mode or os.getenv("CAR2LEGO_BRICKLAYER_AUDIT", "auto")
    if mode not in {"off", "auto", "required"}:
        raise ValueError("CAR2LEGO_BRICKLAYER_AUDIT must be off, auto or required")
    content = source.read_bytes()
    report = dict(schema=VERSION, source_sha256=sha256(content).hexdigest(), mode=mode,
        status="unverified", complete=False, enabled=mode != "off", available=False, enforced=mode == "required",
        physical_verified=False, appearance_accepted=False, limitations=LIMITATIONS)
    try:
        report["sdk_version"] = metadata.version("bricklayer-sdk")
    except metadata.PackageNotFoundError:
        report["reason"] = "Install the brickwright extra to enable the independent validator"
        report["enabled"] = mode == "required"
    else:
        report["available"] = True
        if mode != "off":
            with TemporaryDirectory(prefix="bricklayer-audit-") as temporary:
                directory = Path(temporary)
                snapshot, result = directory / "model.ldr", directory / "result.json"
                snapshot.write_bytes(content)
                command = [sys.executable, "-m", "car2lego3d.bricklayer_audit", "--worker",
                    "--source", str(snapshot), "--output", str(result)]
                if library:
                    command += ["--library", str(Path(library).resolve())]
                try:
                    process = subprocess.run(command, capture_output=True, timeout=timeout,
                        env={**os.environ, "PYTHONUTF8": "1"},
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if process.returncode or not result.is_file():
                        report.update(reason="worker_failed", error=process.stderr.decode("utf-8", errors="replace")[-2000:])
                    else:
                        report.update(json.loads(result.read_text(encoding="utf-8")))
                except subprocess.TimeoutExpired:
                    report.update(reason="worker_timeout", timeout_seconds=timeout)
                except (OSError, ValueError) as error:
                    report.update(status="unverified", complete=False, reason="worker_failed",
                        error=f"{type(error).__name__}: {error}")
    if mode == "off":
        report.update(enabled=False, reason="disabled")
    if sha256(source.read_bytes()).hexdigest() != report["source_sha256"]:
        report.update(status="unverified", complete=False, reason="source_changed_during_audit")
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--mode", choices=["off", "auto", "required"], default="required")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("Output must not overwrite the source")
    if args.worker:
        try:
            report = inspect_text(args.source.read_text(encoding="utf-8-sig"), args.library)
        except Exception as error:
            report = dict(status="unverified", complete=False, reason="validator_error",
                error=f"{type(error).__name__}: {error}")
        write_json(args.output, report)
        return
    report = audit_ldraw(args.source, args.output, library=args.library, mode=args.mode, timeout=args.timeout)
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
