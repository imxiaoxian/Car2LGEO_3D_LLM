"""Check a faster SAT implementation against the existing native-collider checker."""
import argparse
from itertools import chain
import json
from pathlib import Path
import time

import numpy as np
from scipy.spatial.transform import Rotation

from car2lego3d import assembly_catalog as catalog
from car2lego3d.assembly_graph import Assembly
from car2lego3d.common import digest, write_json


def candidate(a, b, tolerance=.35):
    ca, aa, ha = a
    cb, ab, hb = b
    if np.any(abs(cb-ca) > abs(aa) @ ha + abs(ab) @ hb + 1e-9):
        return False
    axes = chain(aa.T, ab.T, (np.cross(x, y) for x in aa.T for y in ab.T))
    for axis in axes:
        length = np.linalg.norm(axis)
        if length < 1e-8:
            continue
        axis = axis / length
        if abs((cb-ca) @ axis) >= np.abs(aa.T @ axis) @ ha + np.abs(ab.T @ axis) @ hb - tolerance:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, default=Path("D:/lego/Studio 2.0/ldraw"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rng = np.random.default_rng(20261017)
    pairs = [((rng.uniform(-20, 20, 3), Rotation.random(random_state=rng).as_matrix(), rng.uniform(.01, 10, 3)),
              (rng.uniform(-20, 20, 3), Rotation.random(random_state=rng).as_matrix(), rng.uniform(.01, 10, 3)))
             for _ in range(3000)]
    start = time.monotonic()
    expected = [catalog.intersects(a, b) for a, b in pairs]
    old_seconds = time.monotonic()-start
    start = time.monotonic()
    actual = [candidate(a, b) for a, b in pairs]
    new_seconds = time.monotonic()-start
    if actual != expected:
        raise ValueError("SAT candidate differs on seeded random cases")
    state = json.loads((args.run / "state.json").read_text(encoding="utf-8"))
    assembly = Assembly(state["text"], state["source_sha256"])
    assembly.revision, assembly.joints = state["revision"], state["joints"]
    existing = json.loads((args.run / "checks.json").read_text(encoding="utf-8"))
    original = catalog.intersects
    try:
        catalog.intersects = candidate
        start = time.monotonic()
        checked = catalog.validate(assembly.graph(), catalog.NativeCatalog(args.native_root))
        check_seconds = time.monotonic()-start
    finally:
        catalog.intersects = original
    if checked != existing:
        raise ValueError("SAT candidate changes the executed source check")
    result = dict(schema="assembly-collision-probe.v1", seeded_pairs=len(pairs), exact_pair_results=True,
        old_pair_seconds=old_seconds, new_pair_seconds=new_seconds, source_check_exact=True,
        source=str(args.run.resolve()), source_sha256=digest(args.run / "state.json"),
        source_check_sha256=digest(args.run / "checks.json"), source_check_seconds=check_seconds,
        quality_accepted=False)
    write_json(args.output, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
