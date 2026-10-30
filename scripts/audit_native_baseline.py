"""Read an existing IO through the creation resolver without claiming a fresh delivery."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory to preserve previous evidence")
    started = time.monotonic()
    source_hash = digest(args.source)
    tools = AssemblyTools(args.output, args.library)
    tools.import_file(args.source)
    imported = time.monotonic()
    result = tools.call("render", {})
    instances = {node["id"]: node for node in tools.assembly.graph()["instances"]
                 if node["kind"] == "part"}
    missing = result.get("result", {}).get("missing", [])
    summary = dict(schema="native-baseline-audit.v1", fresh_creation=False,
        source=str(args.source.resolve()), source_sha256=source_hash,
        source_unchanged=digest(args.source) == source_hash,
        instances=len(instances), resolved=len(instances) - len(missing),
        missing=len(missing), missing_parts=dict(Counter(instances[item["instance"]]["part"]
            for item in missing)), import_seconds=imported - started,
        render_seconds=time.monotonic() - imported,
        geometry_status=result.get("result", {}).get("status", "failed"),
        geometry_warnings=len(result.get("result", {}).get("geometry_warnings", [])),
        appearance="unverified", digital_assembly="unverified")
    write_json(args.output / "resolver-result.json", result)
    write_json(args.output / "baseline.json", summary)
    print(json.dumps(summary))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
