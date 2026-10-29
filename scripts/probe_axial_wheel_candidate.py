"""Compare whole-wheel and component axial paths of an explicit native candidate."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

import numpy as np

from car2lego3d.assembly_evidence import check_current
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.assembly_tools import AssemblyTools
from car2lego3d.common import digest, write_json


def matrix(position=(0, 0, 0), rotation=None):
    result = np.eye(4)
    result[:3, 3] = position
    if rotation is not None:
        result[:3, :3] = rotation
    return result.tolist()


def candidate_operations():
    # Deliberately explicit fixture: these positions are not planner output.
    return [
        dict(op="add", part="3700.dat", alias="bearing", color=71, matrix=matrix()),
        dict(op="module", name="wheel.ldr", alias="wheel", matrix=matrix()),
        dict(op="add", part="3707.dat", alias="shaft", parent="@wheel", color=0,
            matrix=matrix((0, 10, 0), [[0, 0, -1], [0, 1, 0], [1, 0, 0]])),
        dict(op="add", part="584.dat", alias="inner_bush", parent="@wheel", color=71,
            matrix=matrix((0, 10, -20.5))),
        dict(op="add", part="584.dat", alias="outer_bush", parent="@wheel", color=71,
            matrix=matrix((0, 10, 20.5), [[-1, 0, 0], [0, 1, 0], [0, 0, -1]])),
        dict(op="module", name="hub.ldr", alias="hub", parent="@wheel", matrix=matrix((0, 10, 51))),
        dict(op="add", part="55982.dat", alias="rim", parent="@hub", color=71, matrix=matrix()),
        dict(op="attach", part="35578.dat", alias="tyre", parent="@hub", color=0,
            source_port="0", target="@rim", target_port="1"),
        dict(op="articulate", instance="@wheel", source="@shaft", source_port="0",
            target="@bearing", target_port="2", purpose="rolling", limits=[0, 360]),
    ]


def run(output, native, *, preview=True):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; preserve earlier evidence")
    started = time.monotonic()
    tools = AssemblyTools(output / "assembly", native)

    def call(tool, **arguments):
        response = tools.call(tool, arguments)
        if not response["ok"]:
            raise ValueError(f"{tool}: {response['error']}")
        return response["result"]

    built = call("build", revision=0, operations=candidate_operations())
    ids = built["aliases"]
    graph = deepcopy(tools.assembly.graph())
    checks = call("check")
    motion = call("motion", samples=9)
    cuts = [
        ("whole_wheel_on_bearing", "wheel", "shaft", "0", "bearing", "2"),
        ("shaft_alone_on_bearing", "shaft", "shaft", "0", "bearing", "2"),
        ("inner_bush_on_shaft", "inner_bush", "inner_bush", "0", "shaft", "0"),
        ("outer_bush_on_shaft", "outer_bush", "outer_bush", "0", "shaft", "0"),
        ("hub_on_shaft", "hub", "rim", "0", "shaft", "0"),
    ]
    diagnostics = {}
    for label, moving, source, source_port, target, target_port in cuts:
        diagnostics[label] = call("axial_motion", revision=graph["revision"], joint=ids["wheel"],
            moving=ids[moving], source=ids[source], source_port=source_port, target=ids[target],
            target_port=target_port, samples=9)
    if preview:
        call("preview")
    call("export")
    archive = output / "assembly/model.io"
    restored = IOArchive(archive).assembly.graph()
    comparable = lambda value: {k: v for k, v in value.items() if k not in {"revision", "source_sha256"}}
    record = dict(schema="axial-wheel-candidate.v1", live_planner=False, bom=tools.assembly.bom(),
        fixture="Explicit native 3707 shaft, two 584 bushes, 55982 rim, 35578 tyre and 3700 support",
        check_current=check_current(graph, checks), motion_current=check_current(graph, motion),
        checks_status=checks["status"], motion_status=motion["status"], diagnostics=diagnostics,
        graph_unchanged=graph == tools.assembly.graph(), archive_roundtrip=comparable(graph) == comparable(restored),
        model_io_sha256=digest(archive), seconds=time.monotonic()-started,
        assembly_order="unverified", physical_retention="unverified", load_capacity="unverified",
        acceptance=dict(car=False, appearance="unverified", studio="unverified", physical="unverified"))
    write_json(output / "report.json", record)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()
    report = run(args.output, args.native, preview=not args.no_preview)
    print(json.dumps({**{k: report[k] for k in ("checks_status", "motion_status", "archive_roundtrip", "seconds")},
        "cuts": {label: value["classification"] for label, value in report["diagnostics"].items()}}))
