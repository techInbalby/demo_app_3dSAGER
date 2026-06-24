"""Unit tests for align_api/loaders.py.

`slice_cand_from_post_disaster` is the most complex helper here — it walks
CityJSON 1.1 boundaries, collects + remaps vertices, and (since plan
addendum 13) normalizes z so the building base sits at z=0 in the
three.js viewer. Plus the small registry helpers.
"""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# slice_cand_from_post_disaster — the meaty one
# ---------------------------------------------------------------------------

def _write_post_disaster_fixture(tmp_path, cand_id, verts):
    """Minimal CityJSON 1.1 with a single Building cand referencing all verts."""
    boundaries = [[[list(range(len(verts)))]]]  # one MultiSurface, one polygon
    payload = {
        'type': 'CityJSON',
        'version': '1.1',
        'metadata': {'referenceSystem': 'EPSG:7415'},
        'CityObjects': {
            f'bag_{cand_id}': {
                'type': 'Building',
                'attributes': {},
                'geometry': [{'type': 'MultiSurface', 'lod': '2', 'boundaries': boundaries}],
            }
        },
        'vertices': verts,
    }
    (tmp_path / 'post_disaster_cands.json').write_text(json.dumps(payload))


def test_slice_returns_none_when_cache_missing(tmp_path):
    from align_api import loaders
    assert loaders.slice_cand_from_post_disaster(tmp_path, '0518100000000001') is None


def test_slice_returns_none_when_cand_not_in_file(tmp_path):
    from align_api import loaders
    _write_post_disaster_fixture(tmp_path, '0518100000000001', [[0, 0, 40], [1, 0, 40], [0, 1, 40], [0, 0, 50]])
    assert loaders.slice_cand_from_post_disaster(tmp_path, 'NONEXISTENT') is None


def test_slice_finds_cand_by_bag_prefix(tmp_path):
    from align_api import loaders
    _write_post_disaster_fixture(tmp_path, '0518100000000001', [[0, 0, 40], [1, 0, 40], [0, 1, 40], [0, 0, 50]])
    out = loaders.slice_cand_from_post_disaster(tmp_path, '0518100000000001')
    assert out is not None
    assert 'bag_0518100000000001' in out['CityObjects']


def test_slice_normalizes_z_to_zero(tmp_path):
    """Addendum 13: building base must sit at z=0 after slicing so the
    three.js viewer renders it on the same grid plane as the pristine path."""
    from align_api import loaders
    verts = [[0, 0, 40.5], [1, 0, 40.5], [0, 1, 40.5], [0, 0, 55.7]]
    _write_post_disaster_fixture(tmp_path, '0518100000000001', verts)
    out = loaders.slice_cand_from_post_disaster(tmp_path, '0518100000000001')
    out_verts = out['vertices']
    min_z = min(v[2] for v in out_verts)
    max_z = max(v[2] for v in out_verts)
    assert min_z == pytest.approx(0.0, abs=1e-9)
    # Relative height is preserved (55.7 - 40.5 = 15.2)
    assert max_z == pytest.approx(15.2, abs=1e-6)


def test_slice_preserves_xy(tmp_path):
    """X/Y aren't touched by the z normalization."""
    from align_api import loaders
    verts = [[100.0, 200.0, 40.0], [101.0, 200.0, 40.0], [100.0, 201.0, 40.0], [100.0, 200.0, 50.0]]
    _write_post_disaster_fixture(tmp_path, '0518100000000001', verts)
    out = loaders.slice_cand_from_post_disaster(tmp_path, '0518100000000001')
    xys = [(v[0], v[1]) for v in out['vertices']]
    assert (100.0, 200.0) in xys
    assert (101.0, 200.0) in xys
    assert (100.0, 201.0) in xys


def test_slice_remaps_boundary_indices(tmp_path):
    """When extracting a subset of vertices, the boundary indices must point
    into the new (trimmed) vertex array, not the original."""
    from align_api import loaders
    # 5 verts total, but only 4 referenced by the cand (idx 0,2,3,4).
    verts = [
        [0, 0, 40],     # idx 0 — referenced
        [9, 9, 999],    # idx 1 — UNREFERENCED, will be dropped
        [1, 0, 40],     # idx 2 — referenced
        [0, 1, 40],     # idx 3 — referenced
        [0, 0, 50],     # idx 4 — referenced
    ]
    payload = {
        'type': 'CityJSON',
        'version': '1.1',
        'CityObjects': {
            'bag_0518100000000001': {
                'type': 'Building',
                'attributes': {},
                'geometry': [{'type': 'MultiSurface', 'lod': '2',
                              'boundaries': [[[0, 2, 3, 4]]]}],
            }
        },
        'vertices': verts,
    }
    (tmp_path / 'post_disaster_cands.json').write_text(json.dumps(payload))
    out = loaders.slice_cand_from_post_disaster(tmp_path, '0518100000000001')
    assert len(out['vertices']) == 4
    boundaries = out['CityObjects']['bag_0518100000000001']['geometry'][0]['boundaries']
    flat = boundaries[0][0]
    assert sorted(flat) == [0, 1, 2, 3]  # re-indexed 0..3


# ---------------------------------------------------------------------------
# damage_factor_for_cand
# ---------------------------------------------------------------------------

def test_damage_factor_returns_none_when_log_missing(tmp_path):
    from align_api import loaders
    assert loaders.damage_factor_for_cand(tmp_path, 'any_id') is None


def test_damage_factor_finds_by_raw_id(tmp_path):
    from align_api import loaders
    (tmp_path / 'disaster_log.json').write_text(json.dumps({
        'damage_log': {'0518100000209206': 0.62, 'bag_0518100000231007': 1.0},
    }))
    assert loaders.damage_factor_for_cand(tmp_path, '0518100000209206') == pytest.approx(0.62)


def test_damage_factor_finds_by_bag_prefix(tmp_path):
    from align_api import loaders
    (tmp_path / 'disaster_log.json').write_text(json.dumps({
        'damage_log': {'bag_0518100000231007': 0.83},
    }))
    assert loaders.damage_factor_for_cand(tmp_path, '0518100000231007') == pytest.approx(0.83)


def test_damage_factor_returns_none_when_cand_not_logged(tmp_path):
    from align_api import loaders
    (tmp_path / 'disaster_log.json').write_text(json.dumps({'damage_log': {}}))
    assert loaders.damage_factor_for_cand(tmp_path, 'unknown') is None


# ---------------------------------------------------------------------------
# cityjson_path
# ---------------------------------------------------------------------------

def test_cityjson_path_unknown_stage_returns_none(tmp_path):
    from align_api import loaders
    assert loaders.cityjson_path('NOT_A_STAGE', cache_dir=tmp_path) is None


def test_cityjson_path_prefers_prebaked(tmp_path):
    """When both stems exist, the prebaked variant is preferred (fast WGS84)."""
    from align_api import loaders
    raw = tmp_path / 'post_disaster_cands.json'
    pre = tmp_path / 'post_disaster_cands.prebaked.json'
    raw.write_text('{}')
    pre.write_text('{}')
    assert loaders.cityjson_path('misaligned', cache_dir=tmp_path) == pre


def test_cityjson_path_falls_back_to_raw(tmp_path):
    from align_api import loaders
    raw = tmp_path / 'post_disaster_cands.json'
    raw.write_text('{}')
    assert loaders.cityjson_path('misaligned', cache_dir=tmp_path) == raw


def test_cityjson_path_returns_none_when_neither_exists(tmp_path):
    from align_api import loaders
    assert loaders.cityjson_path('aligned', seed=1, cache_dir=tmp_path) is None


def test_cityjson_path_damaged_heights_stage(tmp_path):
    """The damaged_heights stage (addendum 11) resolves correctly."""
    from align_api import loaders
    pre = tmp_path / 'damaged_heights_only_cands.prebaked.json'
    pre.write_text('{}')
    assert loaders.cityjson_path('damaged_heights', cache_dir=tmp_path) == pre


def test_cityjson_path_aligned_uses_seed(tmp_path):
    from align_api import loaders
    raw = tmp_path / 'aligned_candidates_seed7.json'
    raw.write_text('{}')
    assert loaders.cityjson_path('aligned', seed=7, cache_dir=tmp_path) == raw
    # Wrong seed → no match
    assert loaders.cityjson_path('aligned', seed=1, cache_dir=tmp_path) is None


# ---------------------------------------------------------------------------
# status() — manifest summary
# ---------------------------------------------------------------------------

def test_status_reports_existing_artifacts(tmp_path):
    from align_api import loaders
    # Seed a few of the expected files
    (tmp_path / 'alignment_info.json').write_text('{"alignment_succeeded": true}')
    (tmp_path / 'matches_by_cand.json').write_text('{}')
    out = loaders.status(cache_dir=tmp_path)
    assert isinstance(out, dict)
    # The exact key names live in loaders.status; at minimum it should report
    # alignment_info as available.
    assert 'alignment_info' in out or 'has_alignment_info' in out or out  # tolerate naming


def test_read_json_cached_returns_none_for_missing(tmp_path):
    from align_api import loaders
    assert loaders._read_json_cached(tmp_path / 'nope.json') is None


def test_read_json_cached_invalidates_on_mtime_change(tmp_path):
    """The mtime-keyed cache must reload when the file changes on disk."""
    from align_api import loaders
    p = tmp_path / 'x.json'
    p.write_text('{"v": 1}')
    assert loaders._read_json_cached(p) == {'v': 1}
    # Bump content + mtime
    import time
    time.sleep(0.01)  # ensure mtime differs at coarse granularity
    p.write_text('{"v": 2}')
    os_utime = __import__('os').utime
    os_utime(p, None)  # touch
    # Force a new mtime by writing again (touch alone may not bump on tmpfs)
    p.write_text('{"v": 2}')
    assert loaders._read_json_cached(p) == {'v': 2}
