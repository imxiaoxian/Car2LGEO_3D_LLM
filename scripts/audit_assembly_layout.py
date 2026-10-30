"""Inspect resolved module bounds on an immutable native-parts state copy."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def run(source, output, native, presentation=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new audit directory; preserve previous evidence")
    captured = source.read_bytes()
    state = json.loads(captured)
    if state.get("packages") or state.get("source_archive"):
        raise ValueError("This audit requires an independent native-parts state")
    output.mkdir(parents=True)
    (output / "state.json").write_bytes(captured)
    tools = AssemblyTools(output, native)
    graph = tools.assembly.graph()
    schedule_path = source.parent / "module-tasks.json"
    layout = None
    schedule_sha256 = None
    if schedule_path.is_file():
        from car2lego3d.assembly_layout import wheel_layout
        schedule_bytes = schedule_path.read_bytes()
        (output / "source-module-tasks.json").write_bytes(schedule_bytes)
        schedule_sha256 = sha256(schedule_bytes).hexdigest()
        contract = json.loads(schedule_bytes)["contract"]["mission"]["vehicle_contract"]
        layout = wheel_layout(graph, tools.catalog, contract, tools.module_bindings)
    nodes = {row["id"]: row for row in graph["instances"]}
    surfaces, missing = {}, []
    for row in nodes.values():
        if row["kind"] != "part":
            continue
        try:
            triangles, _ = tools.catalog.library.triangles(row["part"])
            points = np.asarray(triangles).reshape(-1, 3)
            if not len(points) or not np.isfinite(points).all():
                raise ValueError("Missing finite surface geometry")
            world = np.asarray(row["world"])
            surfaces[row["id"]] = points @ world[:3, :3].T + world[:3, 3]
        except (ValueError, OSError) as error:
            missing.append(dict(instance=row["id"], part=row["part"], error=str(error)))

    def bounds(identities):
        points = [surfaces[key] for key in identities if key in surfaces]
        if not points:
            return None
        points = np.concatenate(points) * .4
        low, high = points.min(axis=0), points.max(axis=0)
        return dict(bounds_mm=[low.tolist(), high.tolist()],
                    dimensions_mm=(high - low).tolist(), center_mm=((low + high) / 2).tolist())

    modules = {}
    for name, binding in state.get("module_bindings", {}).items():
        members = [key for key in surfaces if any(key.startswith(root + "/") for root in binding["roots"])]
        modules[name] = dict(roots=binding["roots"], part_count=len(members), geometry=bounds(members),
            roles={role: [dict(instance=key, part=nodes[key]["part"], geometry=bounds([key]))
                          for key in identities] for role, identities in binding["roles"].items()})
    result = dict(schema="assembly-layout-audit.v1", source=str(source),
        source_sha256=sha256(captured).hexdigest(), revision=state["revision"],
        coordinates="LDraw: X longitudinal, Y down, Z lateral; dimensions in mm",
        total_parts=len(surfaces) + len(missing), geometry=bounds(surfaces),
        modules=modules, missing=missing, render=tools.call("render", {}),
        wheel_layout=layout, source_schedule_sha256=schedule_sha256,
        native_evidence=tools.verification_inputs(),
        source_schedule_still_matches_snapshot=(digest(schedule_path) == schedule_sha256) if schedule_sha256 else None,
        source_still_matches_snapshot=digest(source) == sha256(captured).hexdigest(),
        vehicle_accepted=False, training_approved=False,
        limitations="Preserved intermediate geometry; no independent semantic, appearance or vehicle acceptance.")
    write_json(output / "layout.json", result)
    if presentation:
        from car2lego3d.assembly_presentation import render
        if not result["render"]["ok"] or result["render"]["result"]["status"] != "pass":
            raise ValueError("Cannot render unresolved geometry")
        render(output / "assembly.glb", output / "presentation")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--presentation", action="store_true")
    report = run(**vars(parser.parse_args()))
    print(json.dumps({key: report[key] for key in ("revision", "total_parts", "geometry", "missing", "source_still_matches_snapshot")}))
