"""Compare collision implementations on an immutable saved assembly and motion sweep."""
import argparse
import json
from pathlib import Path
import time

from car2lego3d import assembly_catalog as native
from car2lego3d.assembly_graph import Assembly
from car2lego3d.assembly_motion import evaluate_motion
from car2lego3d.common import digest, write_json


def run(source, output, root, samples):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Preserve existing benchmark output")
    before = digest(source / "state.json")
    state = json.loads((source / "state.json").read_text(encoding="utf-8"))
    assembly = Assembly(state["text"], state["source_sha256"])
    assembly.revision, assembly.joints = state["revision"], state["joints"]
    graph = assembly.graph()
    optimized, parser = native.colliders_intersect, native.collider_records
    measurements, timings = {}, {}
    try:
        for mode in ("legacy", "optimized"):
            native.colliders_intersect = (lambda a, b: any(native.intersects(x, y) for x in a for y in b)) if mode == "legacy" else optimized
            native.collider_records = parser.__wrapped__ if mode == "legacy" else parser
            catalog = native.NativeCatalog(root)
            started = time.monotonic()
            static = native.validate(graph, catalog)
            motion = evaluate_motion(graph, lambda pose: native.validate(pose, catalog, collision_limit=10), samples)
            timings[mode] = time.monotonic() - started
            measurements[mode] = dict(static=static, motion=motion)
    finally:
        native.colliders_intersect, native.collider_records = optimized, parser
    if digest(source / "state.json") != before:
        raise ValueError("Live source changed during benchmark; discard comparison")
    if measurements["legacy"] != measurements["optimized"]:
        raise ValueError("Optimized checker changed validation results")
    record = dict(schema="assembly-validation-performance.v1", source=str(source),
        state_sha256=before, revision=graph["revision"], samples=samples,
        exact_static_and_motion_results=True, timings_seconds=timings,
        speedup=timings["legacy"] / timings["optimized"], results=measurements["optimized"],
        vehicle_acceptance="unverified")
    write_json(output, record)
    print(json.dumps({key: value for key, value in record.items() if key != "results"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--samples", type=int, default=9)
    run(**vars(parser.parse_args()))
