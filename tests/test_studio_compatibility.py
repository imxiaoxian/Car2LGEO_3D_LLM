"""Synthetic evidence exercises coverage rules, not actual Studio compatibility."""
from copy import deepcopy
from zipfile import ZipFile

import pytest

from car2lego3d.assembly_evidence import files_current, snapshot_files
from car2lego3d.assembly_graph import IOArchive
from car2lego3d.common import digest
from car2lego3d.studio_compatibility import coverage, exporter_identity, features


def archive(path, *, nested=True, custom=b"part geometry", color=4):
    text = f"1 {color} 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
    if nested:
        text = "0 FILE main.ldr\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 door.ldr\n0 FILE door.ldr\n" + text
    with ZipFile(path, "w") as z:
        z.writestr("model.ldr", text)
        if custom is not None:
            z.writestr("CustomParts/parts/test.dat", custom)


def sample(tmp_path):
    original, saved = tmp_path / "sample.io", tmp_path / "saved.io"
    archive(original)
    archive(saved, color=15)
    image = tmp_path / "studio-sample.png"
    image.write_bytes(b"synthetic screenshot fixture")
    return dict(original=str(original), saved=str(saved), original_sha256=digest(original),
        saved_sha256=digest(saved), exporter_sha256=exporter_identity(),
        reviewer_kind="human", reviewer="fixture", operations=dict(open=True, edit=True, save=True, reopen=True),
        evidence_files=snapshot_files([image]))


def test_sample_coverage_does_not_claim_individual_file_was_opened(tmp_path):
    record = sample(tmp_path)
    candidate = tmp_path / "candidate.io"
    archive(candidate, color=7)
    result = coverage(candidate, dict(samples=[record]))
    assert result["status"] == "pass"
    assert result["scope"] == "representative_sample" and not result["file_opened_in_studio"]
    assert "nested_submodels" in result["covered"]
    assert files_current(result["evidence_files"])
    (tmp_path / "studio-sample.png").write_bytes(b"changed")
    assert not files_current(result["evidence_files"])
    assert coverage(candidate, dict(samples=[record]))["status"] == "unverified"


@pytest.mark.parametrize("fault", ["old_exporter", "agent_review", "missing_operation", "saved_changed",
    "dependency_changed", "nesting_lost", "corrupt_saved", "missing_evidence"])
def test_incomplete_sample_cannot_cover_a_new_candidate(tmp_path, fault):
    record = deepcopy(sample(tmp_path))
    candidate = tmp_path / "candidate.io"
    archive(candidate)
    saved = tmp_path / "saved.io"
    if fault == "old_exporter":
        record["exporter_sha256"] = "old implementation"
    elif fault == "agent_review":
        record["reviewer_kind"] = "agent"
    elif fault == "missing_operation":
        record["operations"]["reopen"] = False
    elif fault == "missing_evidence":
        record["evidence_files"] = []
    else:
        if fault == "saved_changed":
            archive(saved, color=1)
        elif fault == "dependency_changed":
            archive(saved, custom=b"different dependency")
        elif fault == "nesting_lost":
            archive(saved, nested=False)
        else:
            saved.write_bytes(b"not a zip archive")
        if fault != "saved_changed":
            record["saved_sha256"] = digest(saved)
    assert coverage(candidate, dict(samples=[record]))["status"] == "unverified"


def test_no_sample_leaves_exact_dependency_coverage_unverified(tmp_path):
    candidate = tmp_path / "candidate.io"
    archive(candidate)
    result = coverage(candidate, {})
    assert result["status"] == "unverified" and result["missing"]
    assert any(item.startswith("dependency:") for item in features(IOArchive(candidate)))
