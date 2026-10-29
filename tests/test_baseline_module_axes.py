import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("review_baseline_module_axes",
    Path(__file__).resolve().parents[1] / "scripts/review_baseline_module_axes.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def module(axes):
    boundaries, hints = [], []
    for index, (origin, axis) in enumerate(axes):
        edge = dict(a="module/part", b="mount" + str(index), port_a=str(index), port_b="0")
        boundaries.append(edge)
        hints.append(dict(connection=edge, connector_origin_ldu=origin, axis=axis))
    return dict(boundary_connections=boundaries, candidate_axes=hints,
        part_instances=["module/part"], missing_connection_evidence=[])


def test_opposite_and_rounded_axes_share_a_line_but_do_not_certify_motion():
    result = review.compare_mount_axes(module([([0, 0, 0], [0, 0, 1.000001]),
        ([0, 0, 200], [0, 0, -1])]))
    assert result["status"] == "consistent_known_axis_candidate"
    assert not result["conflicting_pairs"]
    assert result["articulation"] == "unverified" and result["motion"] == "not_checked"


@pytest.mark.parametrize("second", [([0, 20, 0], [0, 0, 1]), ([0, 0, 0], [0, 1, 0])])
def test_all_mounts_are_compared_for_offset_or_crossing_constraints(second):
    result = review.compare_mount_axes(module([([0, 0, 0], [0, 0, 1]), second]))
    assert result["status"] == "conflicting_known_axes"
    assert len(result["conflicting_pairs"]) == 1


def test_external_mount_missing_data_and_non_axis_connections_block_candidate():
    row = module([([0, 0, 0], [0, 1, 0])])
    issue = dict(instance="mount0", issue="unsupported_connector: slider")
    result = review.compare_mount_axes(row, [issue, dict(instance="unrelated", issue="missing")])
    assert result["status"] == "incomplete_boundary_evidence"
    assert result["missing_connection_evidence"] == [issue]
    row["boundary_connections"].append(dict(a="module/part", b="other", port_a="7", port_b="0"))
    result = review.compare_mount_axes(row)
    assert result["status"] == "incomplete_boundary_evidence"
    assert len(result["non_axis_boundaries"]) == 1


def test_no_boundary_is_not_evidence_of_free_motion():
    assert review.compare_mount_axes(module([]))["status"] == "no_axis_evidence"


def test_nan_and_unbound_axis_evidence_are_rejected():
    with pytest.raises(ValueError, match="Invalid mount axis"):
        review.compare_mount_axes(module([([0, 0, 0], [float("nan"), 0, 1])]))
    row = module([([0, 0, 0], [0, 0, 1])])
    row["boundary_connections"] = []
    with pytest.raises(ValueError, match="not a module boundary"):
        review.compare_mount_axes(row)


def presentation_fixture(tmp_path):
    row = module([([0, 0, 0], [0, 0, 1])])
    row["instance"] = "module"
    paths = {}
    for key in ("source", "baseline_state", "glb", "dependency"):
        paths[key] = tmp_path / key
        paths[key].write_bytes(key.encode())
    image = tmp_path / "door.png"
    image.write_bytes(b"rendered module pixels")
    audit = dict(case="test", source=str(paths["source"]), source_sha256=review.digest(paths["source"]),
        baseline_state_sha256=review.digest(paths["baseline_state"]),
        baseline_import_matches_source=True,
        dependency_hashes={str(paths["dependency"]): review.digest(paths["dependency"])},
        modules=[row], connection_unknown=[], port_unknown=[])
    paths["audit"] = tmp_path / "audit.json"
    paths["audit"].write_text(json.dumps(audit), encoding="utf-8")
    data = dict(case="test", geometry_modified=False, modules=[dict(instance="module")],
        images=[dict(file="door.png", module="module", sha256=review.digest(image))])
    for key in ("source", "audit", "baseline_state", "glb"):
        data[key] = str(paths[key])
        data[key + "_sha256"] = review.digest(paths[key])
    path = tmp_path / "presentation.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    labels = {"module": dict(role="door", observation="Highlighted door panel", observed_images=["door.png"])}
    return path, paths, labels


@pytest.mark.parametrize("changed", ["source", "baseline_state", "glb", "dependency", "door.png"])
def test_changed_evidence_cannot_reuse_visual_review(tmp_path, changed):
    path, paths, labels = presentation_fixture(tmp_path)
    result = review.review(path, labels)
    assert result["modules"][0]["role"] == "door"
    assert result["audit"] == str(paths["audit"])
    target = paths.get(changed, tmp_path / changed)
    target.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        review.review(path, labels)


def test_role_review_rejects_wrong_module_or_unrelated_image(tmp_path):
    path, _, labels = presentation_fixture(tmp_path)
    with pytest.raises(ValueError, match="exactly match"):
        review.review(path, {})
    labels["module"]["observed_images"] = ["source_oblique.png"]
    with pytest.raises(ValueError, match="not an image of this module"):
        review.review(path, labels)
