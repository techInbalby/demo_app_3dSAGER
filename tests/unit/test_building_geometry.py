"""Unit tests for building_api/geometry.py — CityJSON vertex remapping.

The off-by-one in here would produce invisible 3D geometry in the demo's
three.js per-building viewer. These tests pin the contract."""
import pytest

pytestmark = pytest.mark.unit


def test_collect_indices_multisurface():
    from building_api.geometry import collect_vertex_indices
    geom = {
        'type': 'MultiSurface',
        'boundaries': [[[0, 1, 2]], [[2, 3, 4]]],
    }
    assert collect_vertex_indices(geom) == {0, 1, 2, 3, 4}


def test_collect_indices_solid():
    from building_api.geometry import collect_vertex_indices
    geom = {
        'type': 'Solid',
        'boundaries': [[[[0, 1, 2]], [[2, 3, 4]]]],  # one shell, two faces
    }
    assert collect_vertex_indices(geom) == {0, 1, 2, 3, 4}


def test_collect_indices_ignores_negative_and_non_int():
    from building_api.geometry import collect_vertex_indices
    geom = {
        'type': 'MultiSurface',
        'boundaries': [[[0, -1, 'oops', 2.5, 3]]],
    }
    assert collect_vertex_indices(geom) == {0, 3}


def test_collect_indices_unknown_type_returns_empty():
    from building_api.geometry import collect_vertex_indices
    geom = {'type': 'CompositeSurface', 'boundaries': [[[0, 1]]]}
    assert collect_vertex_indices(geom) == set()


def test_collect_indices_empty_boundaries():
    from building_api.geometry import collect_vertex_indices
    assert collect_vertex_indices({'type': 'Solid', 'boundaries': []}) == set()
    assert collect_vertex_indices({'type': 'MultiSurface'}) == set()


def test_remap_geometry_multisurface():
    from building_api.geometry import remap_geometry
    geom = {'type': 'MultiSurface', 'boundaries': [[[10, 20, 30]]]}
    out = remap_geometry(geom, {10: 0, 20: 1, 30: 2})
    assert out == {'type': 'MultiSurface', 'boundaries': [[[0, 1, 2]]]}


def test_remap_geometry_solid():
    from building_api.geometry import remap_geometry
    geom = {'type': 'Solid', 'boundaries': [[[[5, 6, 7]]]]}
    out = remap_geometry(geom, {5: 0, 6: 1, 7: 2})
    assert out == {'type': 'Solid', 'boundaries': [[[[0, 1, 2]]]]}


def test_remap_geometry_unknown_type_unchanged():
    from building_api.geometry import remap_geometry
    geom = {'type': 'CompositeSolid', 'boundaries': [[[1, 2, 3]]]}
    out = remap_geometry(geom, {1: 99})
    assert out == geom   # boundaries untouched


def test_remap_geometry_preserves_extra_keys():
    """Geometry can carry lod / semantics / material fields; the remap must
    not drop them."""
    from building_api.geometry import remap_geometry
    geom = {
        'type': 'MultiSurface',
        'lod': '2.2',
        'semantics': {'surfaces': [{'type': 'RoofSurface'}]},
        'boundaries': [[[1, 2, 3]]],
    }
    out = remap_geometry(geom, {1: 0, 2: 1, 3: 2})
    assert out['lod'] == '2.2'
    assert out['semantics']['surfaces'][0]['type'] == 'RoofSurface'
    assert out['boundaries'] == [[[0, 1, 2]]]


def test_extract_and_remap_end_to_end_multisurface():
    """The canonical case: one MultiSurface building, three faces sharing
    a vertex. The walk should collect 4 unique indices and the output should
    contain exactly 4 vertices, all referenced by valid new indices."""
    from building_api.geometry import extract_and_remap_vertices
    target = {
        'type': 'Building',
        'geometry': [{
            'type': 'MultiSurface',
            'boundaries': [
                [[0, 1, 2]],
                [[2, 3, 0]],
                [[0, 1, 3]],
            ],
        }],
    }
    all_vertices = [
        [0.0, 0.0, 0.0],   # 0
        [1.0, 0.0, 0.0],   # 1
        [1.0, 1.0, 0.0],   # 2
        [0.0, 0.0, 1.0],   # 3
        [9.9, 9.9, 9.9],   # 4 — UNREFERENCED, must be dropped
    ]
    new_vertices, new_geometries = extract_and_remap_vertices(target, all_vertices)
    assert len(new_vertices) == 4
    # Vertex 4 (the unreferenced one) is gone.
    assert [9.9, 9.9, 9.9] not in new_vertices
    # All boundary indices land in [0, 4).
    all_indices = set()
    for face in new_geometries[0]['boundaries']:
        for ring in face:
            all_indices.update(ring)
    assert all_indices == {0, 1, 2, 3}
    assert max(all_indices) < len(new_vertices)


def test_extract_and_remap_drops_out_of_bounds_indices():
    """If a boundary references vertex 99 but all_vertices is only 5 long,
    the bound-check should silently drop it (defensive: real CityJSONs are
    always well-formed, but the demo's old code had this guard)."""
    from building_api.geometry import extract_and_remap_vertices
    target = {
        'geometry': [{
            'type': 'MultiSurface',
            'boundaries': [[[0, 1, 99]]],  # 99 out of bounds
        }],
    }
    all_vertices = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]   # only 2 vertices
    new_vertices, _ = extract_and_remap_vertices(target, all_vertices)
    # Only 0 and 1 are in bounds → exactly 2 vertices output
    assert len(new_vertices) == 2


def test_extract_and_remap_no_geometry_returns_empty():
    from building_api.geometry import extract_and_remap_vertices
    new_v, new_g = extract_and_remap_vertices({}, [[1, 2, 3]])
    assert new_v == []
    assert new_g == []
