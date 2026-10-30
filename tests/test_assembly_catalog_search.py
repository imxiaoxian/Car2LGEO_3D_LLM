import pytest

from car2lego3d.assembly_catalog import NativeCatalog


@pytest.fixture
def catalog(tmp_path):
    records = {
        "tyre304.dat": "Tyre 30.4 x 14",
        "tyre30.dat": "Tyre 30 x 14",
        "tyre430.dat": "Tyre 43.0 x 10",
        "15462.dat": "Technic Axle 5 with Stop",
        "axle40.dat": "Technic Axle 40",
        "3705.dat": "Technic Axle 4",
        "axle4l.dat": "Technic Axle 4L with Stop",
        "3001.dat": "Brick 2 x 4",
    }
    native = NativeCatalog(tmp_path)
    native.descriptions = lambda: records
    return native


def test_decimal_dimensions_do_not_match_separate_digits_or_part_ids(catalog):
    result = catalog.search("Tyre 30.4")
    assert [part["part"] for part in result["parts"]] == ["tyre304.dat"]


def test_description_numbers_do_not_match_longer_sizes_or_filename_digits(catalog):
    result = catalog.search("Technic Axle 4")
    assert {part["part"] for part in result["parts"]} == {"3705.dat", "axle4l.dat"}


@pytest.mark.parametrize("query", ["3001", "3001.dat", "3001 brick"])
def test_part_number_lookup_still_finds_the_native_filename(catalog, query):
    assert catalog.search(query)["parts"] == [dict(part="3001.dat", description="Brick 2 x 4")]


def test_multiple_dimensions_must_all_occur_in_description(catalog):
    assert catalog.search("tyre 30.4 x 10")["total"] == 0
    assert catalog.search("tyre 30.4 x 14")["total"] == 1


def test_equivalent_decimal_notation_and_size_suffixes(catalog):
    assert catalog.search("tyre 30.40 x 14.0")["total"] == 1
    assert {part["part"] for part in catalog.search("technic axle 4.0")["parts"]} == {
        "3705.dat", "axle4l.dat"}


def test_bare_part_number_keeps_patterned_filename_lookup(catalog):
    catalog.descriptions = lambda: {"3001p01.dat": "Brick 2 x 4 with Pattern"}
    assert catalog.search("3001")["total"] == 1


@pytest.mark.parametrize("query", ["tire 30.4", "tyre 30.4"])
def test_tire_spelling_finds_native_description_without_changing_dimensions(catalog, query):
    assert catalog.search(query)["parts"] == [dict(part="tyre304.dat", description="Tyre 30.4 x 14")]


@pytest.mark.parametrize("query", ["Technic frame 5 x 7", "Technic Beam Open Centre 5 x 7"])
def test_frame_terms_find_native_open_center_beams(catalog, query):
    catalog.descriptions = lambda: {
        "64179.dat": "Technic Beam 7 x 5 with Open Center 5 x 3",
        "large.dat": "Technic Beam 11 x 15 with Open Center 9 x 13",
        "solid.dat": "Technic Beam 7 x 5 Solid",
        "window.dat": "Window Frame 5 x 7",
    }
    assert catalog.search(query)["parts"] == [dict(part="64179.dat",
        description="Technic Beam 7 x 5 with Open Center 5 x 3")]


def test_frame_alias_keeps_literal_candidates_and_actual_descriptions(catalog):
    catalog.descriptions = lambda: {
        "beam.dat": "Technic Beam 7 x 5 with Open Center 5 x 3",
        "frame.dat": "Technic Frame 5 x 7",
    }
    assert catalog.search("Technic frame 5 x 7")["parts"] == [
        dict(part="frame.dat", description="Technic Frame 5 x 7"),
        dict(part="beam.dat", description="Technic Beam 7 x 5 with Open Center 5 x 3")]
    assert catalog.search("Technic frame 5 x 7", limit=1)["total"] == 2


def test_aliases_do_not_expand_part_ids_or_match_fragments(catalog):
    catalog.descriptions = lambda: {
        "tire.dat": "Brick 2 x 4",
        "tyre.dat": "Tyre 30.4 x 14",
        "decoration.dat": "Technic Beam 5 x 7 with Centrefold Pattern",
    }
    assert catalog.search("tire.dat")["parts"] == [dict(part="tire.dat", description="Brick 2 x 4")]
    assert catalog.search("Technic Beam Open Centre")["total"] == 0


@pytest.mark.parametrize("query", ["tire", "tyre"])
def test_spelling_aliases_match_whole_words_only(catalog, query):
    catalog.descriptions = lambda: {
        "rim.dat": "Wheel for Tyre 30.4",
        "figure.dat": "Minifig Attire Pattern",
        "embedded.dat": "Decorative Tyrestore Sign",
    }
    assert catalog.search(query)["parts"] == [dict(part="rim.dat", description="Wheel for Tyre 30.4")]


def test_frame_expansion_requires_a_technic_open_center_beam(catalog):
    catalog.descriptions = lambda: {
        "open.dat": "Technic Beam 7 x 5 with Open Centre 5 x 3",
        "solid.dat": "Technic Beam 7 x 5 Solid",
        "window.dat": "Window Beam 7 x 5 with Open Center",
        "pattern.dat": "Technic Beam 7 x 5 with Open Centerpiece Pattern",
    }
    assert catalog.search("frame 5 x 7")["parts"] == [dict(part="open.dat",
        description="Technic Beam 7 x 5 with Open Centre 5 x 3")]
