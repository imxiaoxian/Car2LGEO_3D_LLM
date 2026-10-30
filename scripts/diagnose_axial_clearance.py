"""Inspect archived rolling modules without changing historical assemblies."""
import argparse
from hashlib import sha256
from pathlib import Path

import numpy as np

from car2lego3d.assembly_catalog import connections, world_ports
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import write_json


def run(source, output, native):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory to preserve previous evidence")
    source_hash = sha256(source.read_bytes()).hexdigest()
    tools = AssemblyTools(output / "assembly", native)
    tools.import_file(source)
    graph = tools.assembly.graph()
    nodes = {n["id"]: n for n in graph["instances"]}
    parts = [n for n in nodes.values() if n["kind"] == "part"]
    contacts, unknown = connections(parts, tools.catalog)
    ports, _ = world_ports(parts, tools.catalog)
    by_port = {(p[0], p[1].id): p for p in ports}
    reports, missing = [], []
    for moving, joint in graph["joints"].items():
        if joint.get("purpose") != "rolling":
            continue
        inside = lambda name: name == moving or name.startswith(moving + "/")
        parent = nodes[moving]["parent"]
        rotation = np.eye(3) if parent == "root" else np.asarray(nodes[parent]["world"])[:3, :3]
        axis = rotation @ joint["axis"]
        count = 0
        for contact in contacts:
            if contact["kind"] != "axle:axle" or inside(contact["a"]) == inside(contact["b"]):
                continue
            a, b = ("a", "b") if inside(contact["a"]) else ("b", "a")
            if abs(float(by_port[(contact[a], contact["port_" + a])][3] @ axis)) < .9999:
                continue
            result = tools.call("axial_clearance", dict(revision=graph["revision"], moving=moving,
                source=contact[a], source_port=contact["port_" + a],
                target=contact[b], target_port=contact["port_" + b]))
            reports.append(result)
            count += 1
        if not count:
            missing.append(moving)
    record = dict(schema="archived-axial-diagnostic.v1", source=str(source), source_sha256=source_hash,
        source_unchanged=sha256(source.read_bytes()).hexdigest() == source_hash,
        graph_unchanged=tools.assembly.graph() == graph, missing_rolling_contacts=missing,
        contact_unknown=unknown, diagnostics=reports,
        physical_retention="unverified", load_capacity="unverified")
    write_json(output / "report.json", record)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    args = parser.parse_args()
    record = run(args.source, args.output, args.native)
    for row in record["diagnostics"]:
        print({"ok": row["ok"], **({"moving": row["result"]["moving"], "directions": {
            k: {field: v[field] for field in ("status", "disengagement_distance_ldu", "first_blocker")}
            for k, v in row["result"]["directions"].items()}} if row["ok"] else {"error": row["error"]})})
