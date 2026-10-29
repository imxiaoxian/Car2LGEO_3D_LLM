"""Grouping cannot conceal sliding between individual native wheel parts."""
import os
from pathlib import Path

import pytest

from scripts.probe_axial_wheel_candidate import run


def test_native_double_bush_candidate_distinguishes_internal_release(tmp_path):
    native = Path(os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw"))
    if not native.is_dir():
        pytest.skip("Native parts not installed")
    report = run(tmp_path / "candidate", native, preview=False)
    assert report["checks_status"] == report["motion_status"] == "pass"
    assert report["check_current"] and report["motion_current"]
    assert report["graph_unchanged"] and report["archive_roundtrip"]
    cuts = report["diagnostics"]
    assert cuts["whole_wheel_on_bearing"]["classification"] == "blocked_both_at_sampled_poses"
    for name in ("shaft_alone_on_bearing", "inner_bush_on_shaft", "hub_on_shaft"):
        assert cuts[name]["classification"] == "release_path_observed"
    assert report["physical_retention"] == report["load_capacity"] == report["assembly_order"] == "unverified"
    assert report["live_planner"] is False and report["acceptance"]["car"] is False
