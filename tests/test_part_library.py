import numpy as np
import pytest

from car2lego3d.ldraw_document import Document
from car2lego3d.part_library import PartLibrary


def test_custom_native_recursion_colors_and_placement_share_one_geometry(tmp_path):
    native, package = tmp_path / 'native', tmp_path / 'custom'
    for root in (native, package):
        (root / 'parts').mkdir(parents=True)
    (native / 'parts/face.dat').write_text('3 16 0 0 0 2 0 0 0 2 0\n3 0x2FF8800 0 0 1 2 0 1 0 2 1\n')
    (package / 'parts/c2l-panel.dat').write_text('1 4 10 20 30 0 -1 0 1 0 0 0 0 1 face.dat\n')
    resolver = PartLibrary(native, [package])
    row = dict(part='c2l-panel', rotation=np.eye(3).ravel().tolist(), position=[100, 0, 0], color=15)
    triangles, colors = resolver.instance(row)
    np.testing.assert_allclose(triangles.min(axis=(0, 1)), [108, 20, 30])
    np.testing.assert_allclose(triangles.max(axis=(0, 1)), [110, 22, 31])
    assert colors.tolist() == [4, 0x2FF8800]
    assert len(resolver.dependency_record()['files']) == 2
    assert 'c2l-panel.dat' in resolver.geometry([row])
    document = Document('1 0x2FF8800 0 0 0 1 0 0 0 1 0 0 0 1 face.dat\n')
    assert Document(document.flatten()).instances() == document.instances()


def test_changed_nested_dependency_invalidates_geometry_cache(tmp_path):
    (tmp_path / 'parts').mkdir()
    child = tmp_path / 'parts/child.dat'
    child.write_text('3 16 0 0 0 1 0 0 0 1 0\n')
    (tmp_path / 'parts/parent.dat').write_text('1 16 0 0 0 1 0 0 0 1 0 0 0 1 child.dat\n')
    library = PartLibrary(tmp_path)
    assert library.triangles('parent.dat')[0].max() == 1
    child.write_text('3 16 0 0 0 2 0 0 0 2 0\n')
    assert library.triangles('parent.dat')[0].max() == 2


@pytest.mark.parametrize('metadata,geometry,expected', [
    ('0 !LDRAW_ORG Part Alias UPDATE 2020-01', '1 16 0 0 0 1 0 0 0 1 0 0 0 1 actual.dat', 'actual.dat'),
    ('0 !LDRAW_ORG Part', '1 16 0 0 0 1 0 0 0 1 0 0 0 1 actual.dat', None),
    ('0 !LDRAW_ORG Part Alias', '1 16 1 0 0 1 0 0 0 1 0 0 0 1 actual.dat', None),
    ('0 !LDRAW_ORG Part Alias', '1 16 0 0 0 0 -1 0 1 0 0 0 0 1 actual.dat', None),
    ('0 !LDRAW_ORG Part Alias', '1 4 0 0 0 1 0 0 0 1 0 0 0 1 actual.dat', None),
    ('0 !LDRAW_ORG Part Alias', '1 16 0 0 0 1 0 0 0 1 0 0 0 1 actual.dat\n3 16 0 0 0 1 0 0 0 1 0', None),
])
def test_only_declared_exact_geometry_alias_can_share_native_evidence(tmp_path, metadata, geometry, expected):
    (tmp_path / 'parts').mkdir()
    path = tmp_path / 'parts/alias.dat'
    path.write_text(metadata + '\n' + geometry + '\n')
    library = PartLibrary(tmp_path)
    assert library.identity_alias('alias.dat') == expected
    assert str(path.resolve()) in library.used


def test_missing_cyclic_and_stretched_geometry_are_rejected(tmp_path):
    (tmp_path / 'parts').mkdir()
    library = PartLibrary(tmp_path)
    with pytest.raises(FileNotFoundError):
        library.triangles('missing.dat')
    (tmp_path / 'parts/cycle.dat').write_text('1 16 0 0 0 1 0 0 0 1 0 0 0 1 cycle.dat\n')
    with pytest.raises(ValueError, match='Cyclic'):
        library.triangles('cycle.dat')
    (tmp_path / 'parts/triangle.dat').write_text('3 16 0 0 0 1 0 0 0 1 0\n')
    with pytest.raises(ValueError, match='stretches'):
        library.instance(dict(part='triangle.dat', rotation=(np.eye(3) * 2).ravel().tolist(), position=[0, 0, 0], color=4))


def test_malformed_packaged_conditional_edge_does_not_remove_valid_surfaces(tmp_path):
    (tmp_path / 'parts').mkdir()
    (tmp_path / 'parts/primitive.dat').write_text('3 16 0 0 0 1 0 0 0 1 0\n5 24 1 1 0 1 0 0\n')
    (tmp_path / 'parts/parent.dat').write_text('1 16 0 0 0 1 0 0 0 1 0 0 0 1 primitive.dat\n')
    library = PartLibrary(tmp_path)
    triangles, colors = library.triangles('parent.dat')
    assert triangles.shape == (1, 3, 3) and colors.tolist() == [16]
    warnings = library.dependency_record()['ignored_edge_records']
    assert len(warnings) == 1 and warnings[0]['line'] == 2
    library.ignored_edge_records.clear()
    library.triangles('parent.dat')
    assert library.dependency_record()['ignored_edge_records'] == warnings
    with pytest.raises(ValueError, match='Malformed'):
        Document.parse_line('5 24 1 1 0 1 0 0', geometry=True)


@pytest.mark.parametrize('record', ['3 16 0 0 0 1 0 0', '4 16 0 0 0 1 0 0 0 1 0', '3 16 nan 0 0 1 0 0 0 1 0'])
def test_malformed_surface_records_are_still_rejected(tmp_path, record):
    (tmp_path / 'parts').mkdir()
    (tmp_path / 'parts/bad.dat').write_text(record)
    with pytest.raises(ValueError, match='Malformed'):
        PartLibrary(tmp_path).triangles('bad.dat')


def test_studio_textured_surfaces_preserve_vertices_and_warn_on_unrendered_uv(tmp_path):
    (tmp_path / 'parts').mkdir()
    source = tmp_path / 'parts/textured.dat'
    source.write_text('3 16 0 0 0 2 0 0 0 2 0 0 0 1 0 0 1\n')
    library = PartLibrary(tmp_path)
    triangles, _ = library.triangles('textured.dat')
    np.testing.assert_array_equal(triangles, [[[0, 0, 0], [2, 0, 0], [0, 2, 0]]])
    warnings = library.dependency_record()['geometry_warnings']
    assert len(warnings) == 1 and 'texture appearance unverified' in warnings[0]['issue']
    library.geometry_warnings.clear()
    library.triangles('textured.dat')
    assert library.dependency_record()['geometry_warnings'] == warnings
    source.write_text('3 16 0 0 0 2 0 0 0 2 0 0 0 1 0 0 nan\n')
    with pytest.raises(ValueError, match='Malformed textured'):
        library.triangles('textured.dat')


def test_high_resolution_primitive_fallback_preserves_regular_root_precedence(tmp_path):
    native, package = tmp_path / 'native', tmp_path / 'package'
    for root in (native, package):
        (root / 'p/48').mkdir(parents=True)
    (package / 'p/48/arc.dat').write_text('3 16 0 0 0 48 0 0 0 48 0\n')
    library = PartLibrary(native, [package])
    assert library.triangles('arc.dat')[0].max() == 48
    assert library.dependency_record()['geometry_warnings'][0]['requested'] == 'arc.dat'
    (native / 'p/arc.dat').write_text('3 16 0 0 0 16 0 0 0 16 0\n')
    assert PartLibrary(native, [package]).triangles('arc.dat')[0].max() == 16
    with pytest.raises(FileNotFoundError):
        library.triangles('missing.dat')


def test_studio_customparts_root_and_subparts_are_resolved_without_rewriting_archive(tmp_path):
    native, package = tmp_path / 'native', tmp_path / 'package'
    (package / 's').mkdir(parents=True)
    (package / 'custom.dat').write_text('1 16 0 0 0 1 0 0 0 1 0 0 0 1 s/child.dat\n')
    (package / 's/child.dat').write_text('3 16 0 0 0 1 0 0 0 1 0\n')
    library = PartLibrary(native, [package])
    assert len(library.triangles('custom.dat')[0]) == 1
    assert len(library.dependency_record()['files']) == 2
    assert not (package / 'parts').exists()


@pytest.mark.parametrize('declared,resolution,accepted', [
    ('48/torus.dat', '48_Primitive', True),
    ('48/torus.dat', 'Unofficial_48_Primitive', True),
    ('torus.dat', 'Primitive', False),
    ('48/other.dat', '48_Primitive', False),
    ('48/torus.dat', 'Primitive', False)])
def test_misplaced_48_primitive_requires_exact_logical_name_and_resolution(tmp_path, declared, resolution, accepted):
    folder = tmp_path / 'UnOfficial/p'
    folder.mkdir(parents=True)
    path = folder / 'torus.dat'
    path.write_text(f'0 Name: {declared}\n0 !LDRAW_ORG {resolution}\n3 16 0 0 0 48 0 0 0 48 0\n')
    library = PartLibrary(tmp_path)
    if not accepted:
        with pytest.raises(FileNotFoundError, match='48/torus.dat'):
            library.triangles('48/torus.dat')
        return
    assert library.triangles('48/torus.dat')[0].max() == 48
    warning = library.dependency_record()['geometry_warnings'][0]
    assert warning['requested'] == '48/torus.dat'
    assert warning['file'] == str(path.resolve())
    library.geometry_warnings.clear()
    library.triangles('48/torus.dat')
    assert library.dependency_record()['geometry_warnings'] == [warning]
    (tmp_path / 'p/48').mkdir(parents=True)
    (tmp_path / 'p/48/torus.dat').write_text('3 16 0 0 0 49 0 0 0 49 0\n')
    assert PartLibrary(tmp_path).triangles('48/torus.dat')[0].max() == 49
