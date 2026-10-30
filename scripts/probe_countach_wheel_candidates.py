"""Build real native wheel prototypes without copying a held-out source IO."""
import argparse
import json
from pathlib import Path

import numpy as np

from car2lego3d.assembly_catalog import compatible
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def matrix(position=(0, 0, 0), rotation=None):
    result = np.eye(4)
    if rotation is not None:
        result[:3, :3] = rotation
    result[:3, 3] = position
    return result.tolist()


def execute(tools, tool, **arguments):
    result = tools.call(tool, arguments)
    if not result["ok"]:
        raise ValueError(result["error"])
    return result["result"]


def candidate(output, native, rim, tyre):
    tools = AssemblyTools(output, native)
    created = execute(tools, "edit", revision=0, operations=[
        dict(op="add", alias="bearing", part="3700.dat", color=0, matrix=matrix((250, 0, 0))),
        dict(op="module", alias="wheel", name="rolling-wheel.ldr", matrix=matrix()),
        dict(op="add", alias="rim", parent="@wheel", part=rim, color=71, matrix=matrix()),
        dict(op="add", alias="tyre", parent="@wheel", part=tyre, color=0, matrix=matrix()),
        dict(op="add", alias="axle", parent="@wheel", part="18651.dat", color=0,
             matrix=matrix((0, 0, 120), [[0, 0, -1], [0, 1, 0], [1, 0, 0]]))])
    ids = created["aliases"]
    socket = next(p for p in tools.catalog.ports(rim) if p.semantic == "axle_socket")
    execute(tools, "snap", revision=tools.assembly.revision,
        source=ids["axle"], source_port="1", target=ids["rim"], target_port=socket.id)
    execute(tools, "snap", revision=tools.assembly.revision, moving=ids["wheel"],
        source=ids["axle"], source_port="0", target=ids["bearing"], target_port="2")
    nodes = {n["id"]: n for n in tools.assembly.graph()["instances"]}
    world = np.array(nodes[ids["bearing"]]["world"])
    port = next(p for p in tools.catalog.ports("3700.dat") if p.id == "2")
    origin = world @ [*port.position, 1]
    execute(tools, "edit", revision=tools.assembly.revision, operations=[dict(op="joint",
        instance=ids["wheel"], type="revolute", origin=origin[:3].tolist(),
        axis=(world[:3, :3] @ port.direction).tolist(), limits=[0, 360], purpose="rolling")])
    checks = execute(tools, "check")
    motion = execute(tools, "motion", samples=9)
    rendered = execute(tools, "render")
    exported = execute(tools, "export")
    geometry, _ = tools.catalog.library.triangles(tyre)
    points = np.asarray(geometry).reshape(-1, 3)
    dimensions = ((points.max(axis=0) - points.min(axis=0)) * .4).tolist()
    mating = [(a.id, b.id) for a in tools.catalog.ports(rim) for b in tools.catalog.ports(tyre)
              if a.kind == b.kind == "fixed" and compatible(a, b)]
    return dict(rim=rim, tyre=tyre, bearing="3700.dat", axle="18651.dat",
        tyre_dimensions_mm=dimensions, connector_candidates=mating,
        digital=checks["status"], motion=motion["status"], render=rendered["status"],
        components=len(checks["components"]), collisions=len(checks["collisions"]),
        unknown=len(checks["unknown"]),
        poses=sum(len(j["poses"]) for j in motion["joints"]),
        directory=str(Path(output).resolve()), io_sha256=digest(Path(output) / exported["file"]),
        appearance="unverified", vehicle_fit="unverified", physical="unverified")


def run(output, native):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new prototype directory; retain historical results")
    output.mkdir(parents=True, exist_ok=True)
    candidates = []
    for rim, tyre in (("41896.dat", "41897.dat"), ("56908.dat", "41897.dat"), ("56145.dat", "61481.dat")):
        folder = output / (Path(rim).stem + "-" + Path(tyre).stem)
        try:
            record = candidate(folder, native, rim, tyre)
        except Exception as error:
            record = dict(rim=rim, tyre=tyre, error=str(error), digital="unverified", motion="unverified")
            write_json(folder / "diagnosis.json", record)
        candidates.append(record)
    result = dict(schema="native-wheel-prototypes.v1", candidates=candidates,
        source_io_used=False, planner_evaluation=False, vehicle_accepted=False, training_approved=False,
        limitations=["Native engineering prototypes, not generated Countach modules or whole-vehicle acceptance.",
            "Period five-opening wheel appearance and tyre/body fit still need vehicle-specific design review.",
            "Bearing is a test mounting interface, not completed steering or suspension."])
    write_json(output / "prototypes.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    print(json.dumps(run(**vars(parser.parse_args())), ensure_ascii=False))
