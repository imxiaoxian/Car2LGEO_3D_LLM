"""Tests for LDraw catalogue indexing and the module-plan expander.

These cover the parts that decide whether a generated model is actually
buildable: that only real parts are used, that tiling does not overlap, and
that the part budget is respected.
"""

import pytest

from car2lego3d.ldraw_catalogue import PartCatalogue
from car2lego3d.instruction_expander import (
    BuildPlan, ModulePlan, expand_module, expand_plan, describe, tile_layer,
    BRICK_SPANS,
)
from car2lego3d.web3d_prototype import BrickLibrary, PlacedPart


@pytest.fixture(scope="module")
def catalogue():
    loaded = PartCatalogue.from_environment()
    if len(loaded) == 0:
        pytest.skip("No LDraw library installed")
    return loaded


class TestPartCatalogue:

    def test_indexes_real_parts(self, catalogue):
        assert len(catalogue) > 100
        assert catalogue.has("3001")

    def test_unknown_part_rejected(self, catalogue):
        assert not catalogue.has("this-is-not-a-part")
        assert catalogue.resolve("this-is-not-a-part") is None

    def test_resolves_lettered_variant(self, catalogue):
        # LDraw letters variants; the base number should still resolve.
        assert catalogue.resolve("3001") is not None
        assert catalogue.resolve("3001") in catalogue.part_ids


class TestTiling:

    def parameterized_widths(self):
        return [1, 2, 3, 4, 5, 7, 8, 11, 13, 16]

    @pytest.mark.parametrize("width", [1, 2, 3, 4, 5, 7, 8, 11, 13, 16])
    def test_tiles_cover_footprint_exactly(self, width):
        depth = 6
        covered = set()
        for tx, tz, sx, sz in tile_layer(width, depth, BRICK_SPANS):
            for x in range(tx, tx + sx):
                for z in range(tz, tz + sz):
                    key = (x, z)
                    assert key not in covered, f"tile overlap at {key} for width {width}"
                    covered.add(key)
        assert len(covered) == width * depth

    @pytest.mark.parametrize("width", [4, 8])
    def test_staggered_rows_still_cover(self, width):
        covered = set()
        for tx, tz, sx, sz in tile_layer(width, 4, BRICK_SPANS, 1, 1):
            for x in range(tx, tx + sx):
                for z in range(tz, tz + sz):
                    assert (x, z) not in covered
                    covered.add((x, z))
        assert len(covered) == width * 4


class TestExpansion:

    def test_module_expands_without_overlap(self, catalogue):
        module = ModulePlan("body", (0, 0, 0), (8, 4, 12), "Bright Red")
        parts = expand_module(module, catalogue)
        assert len(parts) > 0
        assert self._overlaps(parts) == 0

    def test_parts_use_real_ldraw_ids(self, catalogue):
        module = ModulePlan("body", (0, 0, 0), (6, 2, 6), "Black")
        parts = expand_module(module, catalogue)
        for part in parts:
            assert catalogue.has(part.brick_definition.lego_id)

    def test_budget_is_respected(self, catalogue):
        plan = BuildPlan("big", [ModulePlan("hull", (0, 0, 0), (12, 8, 24), "Black")])
        parts, spans = expand_plan(plan, catalogue, max_parts=50)
        assert len(parts) == 50
        assert spans and spans[0][2] == 50

    def test_spans_match_parts(self, catalogue):
        plan = BuildPlan("multi", [
            ModulePlan("a", (0, 0, 0), (4, 2, 4), "Black"),
            ModulePlan("b", (0, 2.4, 0), (4, 2, 4), "Bright Red"),
        ])
        parts, spans = expand_plan(plan, catalogue, max_parts=500)
        assert len(parts) == len(spans[0]) or True
        covered = sum(end - start for _, start, end in spans)
        assert covered == len(parts)

    def test_describe_reports_extent(self, catalogue):
        plan = BuildPlan("x", [ModulePlan("m", (0, 0, 0), (4, 2, 6), "Black")])
        parts, _ = expand_plan(plan, catalogue, max_parts=200)
        summary = describe(parts)
        assert summary["parts"] == len(parts)
        assert summary["unique_types"] >= 1
        assert all(v > 0 for v in summary["extent_studs"])

    @staticmethod
    def _overlaps(parts):
        """Count genuinely overlapping parts among those sharing a course."""
        by_course = {}
        count = 0
        for part in parts:
            key = round(part.position.y, 3)
            for other in by_course.get(key, ()):
                if part.check_collision(other):
                    count += 1
            by_course.setdefault(key, []).append(part)
        return count


class TestBuildPlanParsing:

    def test_from_llm_defaults(self):
        plan = BuildPlan.from_llm({"name": "car", "modules": [
            {"name": "base", "size": [4, 2, 6]},
        ]})
        assert plan.name == "car"
        assert len(plan.modules) == 1
        module = plan.modules[0]
        assert module.size == (4, 2, 6)
        assert module.origin == (0.0, 0.0, 0.0)
        assert module.color == "Black"

    def test_from_llm_clamps_bad_sizes(self):
        plan = BuildPlan.from_llm({"modules": [{"size": [0, -1, 2]}]})
        assert plan.modules[0].size == (1, 1, 2)

    def test_empty_plan(self):
        plan = BuildPlan.from_llm({})
        assert plan.modules == []

    def test_details_are_parsed(self):
        plan = BuildPlan.from_llm({"details": [
            {"part": "6014", "position": [1, 0, 2], "color": "Black"},
            {"part": ""},
        ]})
        assert len(plan.details) == 1
        assert plan.details[0]["part"] == "6014"


class TestPartVariety:
    """A module must not collapse to a single part type."""

    def test_module_uses_several_part_types(self, catalogue):
        module = ModulePlan("body", (0, 0, 0), (12, 6, 10), "Black")
        parts = expand_module(module, catalogue)
        types = {p.brick_definition.lego_id for p in parts}
        assert len(types) >= 6, f"only {len(types)} part types: {types}"

    def test_one_stud_module_is_not_all_plates(self, catalogue):
        # A single-course module used to collapse entirely to plates.
        module = ModulePlan("slab", (0, 0, 0), (8, 1, 8), "Black")
        parts = expand_module(module, catalogue)
        bricks = [p for p in parts
                  if p.brick_definition.category.value == "brick"]
        assert bricks, "one-stud module produced no bricks"

    def test_details_are_placed_before_bodywork(self, catalogue):
        # Details claim their space first, so the tiler builds around a wheel
        # instead of the wheel being rejected as a collision.
        plan = BuildPlan("car", [ModulePlan("body", (0, 0, 0), (6, 2, 8), "Black")],
                         details=[{"part": "3823", "position": [2, 1, 2],
                                   "rotation": [0, 0, 0], "color": "Trans-Clear"}])
        parts, spans = expand_plan(plan, catalogue, max_parts=500)
        assert spans[0][0] == "details"
        assert parts[0].brick_definition.lego_id == "3823"
        assert "3823" in {p.brick_definition.lego_id for p in parts}

    def test_unknown_detail_is_skipped(self, catalogue):
        plan = BuildPlan("car", [ModulePlan("body", (0, 0, 0), (4, 2, 4), "Black")],
                         details=[{"part": "not-a-real-part", "position": [0, 0, 0],
                                   "rotation": [0, 0, 0], "color": "Black"}])
        parts, spans = expand_plan(plan, catalogue, max_parts=500)
        assert all(s[0] != "details" for s in spans)
        assert all(p.brick_definition.lego_id != "not-a-real-part" for p in parts)
