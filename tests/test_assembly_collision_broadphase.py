import os

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from car2lego3d.assembly_catalog import NativeCatalog, colliders_intersect, intersects


def test_collider_broadphase_matches_sat_for_rotated_and_touching_sets():
    rng = np.random.default_rng(20261019)
    for _ in range(100):
        def boxes(count):
            return [(rng.uniform(-20, 20, 3), Rotation.random(random_state=rng).as_matrix(),
                     rng.uniform(.01, 4, 3)) for _ in range(count)]
        left, right = boxes(7), boxes(11)
        assert colliders_intersect(left, right) == any(intersects(a, b) for a in left for b in right)
    a = (np.zeros(3), np.eye(3), np.ones(3))
    for distance in (0, 1.64, 1.65, 1.650000001, 2, 2.000000001):
        b = (np.array([distance, 0, 0]), np.eye(3), np.ones(3))
        assert colliders_intersect([a], [b]) == intersects(a, b)
    assert not colliders_intersect([], [a])


def test_collider_broadphase_reaches_final_chunk_without_testing_separated_pairs(monkeypatch):
    import car2lego3d.assembly_catalog as catalog
    left = [(np.array([i * 10., 0, 0]), np.eye(3), np.ones(3)) for i in range(130)]
    right = [left[-1]]
    original, tested = catalog.intersects, []

    def tracked(a, b):
        tested.append(a)
        return original(a, b)

    monkeypatch.setattr(catalog, "intersects", tracked)
    assert colliders_intersect(left, right)
    assert len(tested) == 1


def test_collider_cache_is_content_addressed_and_cannot_be_mutated(tmp_path):
    folder = tmp_path / "collider"
    folder.mkdir()
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "box.dat").write_text("0 Test collider geometry\n")
    path = folder / "box.col"
    line = "9 0 1 0 0 0 1 0 0 0 1 0 0 0 1 1 1\n"
    path.write_text(line)
    catalog = NativeCatalog(tmp_path)
    first = catalog.boxes("box.dat")
    assert catalog.boxes("box.dat") is first
    with pytest.raises(ValueError):
        first[0][0][0] = 99
    timestamp = path.stat()
    path.write_text(line.replace("1 1 1\n", "2 1 1\n"))
    os.utime(path, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
    assert catalog.boxes("box.dat")[0][2][0] == 2
    path.unlink()
    with pytest.raises(ValueError, match="Missing"):
        catalog.boxes("box.dat")
