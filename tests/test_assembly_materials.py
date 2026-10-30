from car2lego3d.assembly_materials import palette, rgba


def test_transparent_materials_use_native_color_and_alpha(tmp_path):
    (tmp_path / "LDConfig.ldr").write_text("0 !COLOUR Clear CODE 47 VALUE #FCFCFC EDGE #AAAAAA ALPHA 128\n"
        "0 !COLOUR Blue CODE 1 VALUE #0055BF EDGE #333333\n", encoding="utf-8")
    colors = palette(tmp_path)
    assert rgba(47, colors) == [252, 252, 252, 128]
    assert rgba(1, colors) == [0, 85, 191, 255]
    assert rgba(999999, colors) is None
    assert rgba(0x3AABBCC, colors) == [170, 187, 204, 128]


def test_studio_extended_ldraw_codes_keep_transparency_without_overriding_native(tmp_path):
    root = tmp_path / "ldraw"
    root.mkdir()
    (root / "LDConfig.ldr").write_text("0 !COLOUR Blue CODE 1 VALUE #0055BF EDGE #333333\n")
    data = tmp_path / "data"
    data.mkdir()
    (data / "StudioColorDefinition.txt").write_text(
        "Studio Color Code\tLDraw Color Code\tRGB value\tAlpha\n"
        "40\t100040\t#5E5E5C\t0.5019608\n"
        "7\t1\t#000000\t1\n"
        "9\t\t#FFFFFF\t1\n"
        "10\t100041\t#FFFFFF\tNaN\n"
        "11\t100042\t#FFFFFF\t2\n", encoding="utf-8-sig")
    colors = palette(root)
    assert colors[100040] == [94, 94, 92, 128]
    assert colors[1] == [0, 85, 191, 255]
    assert 40 not in colors and 100041 not in colors and 100042 not in colors


def test_studio_definition_can_resolve_without_ldconfig(tmp_path):
    root = tmp_path / "ldraw"
    root.mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data/StudioColorDefinition.txt").write_text(
        "LDraw Color Code\tRGB value\tAlpha\n100040\t#5e5e5c\t0.5019608\n")
    assert palette(root) == {100040: [94, 94, 92, 128]}
