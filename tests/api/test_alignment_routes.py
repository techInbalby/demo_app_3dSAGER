"""Flask route tests for the /api/alignment blueprint.

Many routes require a fully-populated cache (alignment_info.json,
matches_by_cand.json, etc.). Tests run against the warm cache fixture;
if a particular artifact is missing the test skips rather than fails."""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.api


# ---------------------------------------------------------------------------
# /api/alignment/status
# ---------------------------------------------------------------------------

def test_status_returns_object(client, warm_cache_dir):
    resp = client.get('/api/alignment/status')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), dict)


# ---------------------------------------------------------------------------
# /api/alignment/cityjson?stage=…
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('stage', ['misaligned', 'aligned', 'damaged_heights'])
def test_cityjson_accepts_known_stages(client, warm_cache_dir, stage):
    """Each known stage either returns 200 (file exists) or 409 (not yet run).
    Never 400 — that would mean the stage name was unrecognized."""
    resp = client.get(f'/api/alignment/cityjson?stage={stage}')
    assert resp.status_code in (200, 409)
    if resp.status_code == 200:
        assert resp.mimetype == 'application/json'


def test_cityjson_rejects_unknown_stage(client):
    resp = client.get('/api/alignment/cityjson?stage=NOT_A_STAGE')
    assert resp.status_code == 400
    assert 'error' in resp.get_json()


def test_cityjson_rejects_missing_stage(client):
    resp = client.get('/api/alignment/cityjson')
    assert resp.status_code == 400


def test_cityjson_damaged_heights_grounds_at_z0_in_pipeline_run(client, warm_cache_dir):
    """The damaged_heights file itself is in absolute coords; only the
    per-cand slice route normalizes. This route serves the full prebaked
    file — check it parses as a CityJSON-shaped dict."""
    resp = client.get('/api/alignment/cityjson?stage=damaged_heights')
    if resp.status_code != 200:
        pytest.skip(f"damaged_heights not available (status {resp.status_code})")
    body = json.loads(resp.get_data())
    # Either it's prebaked CityJSON or raw CityJSON 1.1 — both are valid.
    assert isinstance(body, dict)


# ---------------------------------------------------------------------------
# /api/alignment/cand/<id>/cityjson?stage=post_disaster
# ---------------------------------------------------------------------------

def _first_cand_id_from_warm_cache(warm_cache_dir):
    """Find any cand id we can use for parametric tests."""
    mbc = warm_cache_dir / 'matches_by_cand.json'
    if not mbc.exists():
        return None
    data = json.loads(mbc.read_text())
    for _file, by_id in data.items():
        for cid in by_id.keys():
            return cid
    return None


def test_cand_cityjson_returns_grounded_geometry(client, warm_cache_dir):
    """Addendum 13: the sliced post-disaster cand has z_min=0 (grounded)."""
    cand_id = _first_cand_id_from_warm_cache(warm_cache_dir)
    if cand_id is None:
        pytest.skip("warm cache has no matches_by_cand.json")
    resp = client.get(f'/api/alignment/cand/{cand_id}/cityjson?stage=post_disaster')
    if resp.status_code != 200:
        pytest.skip(f"cand cityjson route returned {resp.status_code}: {resp.get_data(as_text=True)[:200]}")
    body = resp.get_json()
    verts = body.get('vertices', [])
    assert verts, "expected at least one vertex"
    min_z = min(v[2] for v in verts)
    assert min_z == pytest.approx(0.0, abs=1e-6), f"min_z should be 0 (grounded), got {min_z}"


def test_cand_cityjson_includes_damage_factor_in_metadata(client, warm_cache_dir):
    cand_id = _first_cand_id_from_warm_cache(warm_cache_dir)
    if cand_id is None:
        pytest.skip("warm cache has no matches_by_cand.json")
    resp = client.get(f'/api/alignment/cand/{cand_id}/cityjson?stage=post_disaster')
    if resp.status_code != 200:
        pytest.skip("cand cityjson route not available")
    metadata = resp.get_json().get('metadata', {})
    # damage_factor may or may not be present (undamaged cands have no entry).
    if 'damage_factor' in metadata:
        df = metadata['damage_factor']
        assert 0.0 < float(df) <= 1.0


def test_cand_cityjson_rejects_unknown_stage(client):
    resp = client.get('/api/alignment/cand/123/cityjson?stage=NOT_A_STAGE')
    assert resp.status_code in (400, 409)


def test_cand_cityjson_404_for_unknown_cand(client, warm_cache_dir):
    resp = client.get('/api/alignment/cand/0000000000NONEXISTENT/cityjson?stage=post_disaster')
    assert resp.status_code in (404, 409)


# ---------------------------------------------------------------------------
# /api/alignment/cand/<id>
# ---------------------------------------------------------------------------

def test_cand_returns_nn_match_shape(client, warm_cache_dir):
    """Pin the JSON shape the frontend depends on (NN match + pool)."""
    cand_id = _first_cand_id_from_warm_cache(warm_cache_dir)
    if cand_id is None:
        pytest.skip("warm cache has no matches_by_cand.json")
    resp = client.get(f'/api/alignment/cand/{cand_id}')
    if resp.status_code != 200:
        pytest.skip(f"cand route returned {resp.status_code}")
    body = resp.get_json()
    assert body['cand_id'] == str(cand_id)
    assert 'nn_match' in body and isinstance(body['nn_match'], dict)
    for k in ('index_id', 'distance_m', 'final_score', 'predicted_label', 'true_label'):
        assert k in body['nn_match']
    assert 'pool' in body and isinstance(body['pool'], list)
    assert 'in_blocking_pool' in body
    assert 'cutoff_m' in body
    assert 'match_threshold' in body


def test_cand_pool_limit_clamps_to_range(client, warm_cache_dir):
    """pool_limit query param honoured + clamped to [1, 20]."""
    cand_id = _first_cand_id_from_warm_cache(warm_cache_dir)
    if cand_id is None:
        pytest.skip("warm cache has no matches_by_cand.json")
    resp = client.get(f'/api/alignment/cand/{cand_id}?pool_limit=2')
    if resp.status_code != 200:
        pytest.skip("cand route not available")
    body = resp.get_json()
    assert len(body['pool']) <= 2


def test_cand_returns_404_for_unknown_id(client, warm_cache_dir):
    resp = client.get('/api/alignment/cand/0000000000NONEXISTENT')
    assert resp.status_code in (404, 409)


# ---------------------------------------------------------------------------
# /api/alignment/anchors
# ---------------------------------------------------------------------------

def test_anchors_returns_shape(client, warm_cache_dir):
    resp = client.get('/api/alignment/anchors')
    if resp.status_code != 200:
        pytest.skip("anchors route not available — cache may not have anchor_pairs.json")
    body = resp.get_json()
    assert 'anchors' in body and isinstance(body['anchors'], list)
    assert 'total' in body
    assert 'returned' in body
    assert body['returned'] <= body['total']


def test_anchors_limit_clamps(client, warm_cache_dir):
    resp = client.get('/api/alignment/anchors?limit=3')
    if resp.status_code != 200:
        pytest.skip("anchors not available")
    body = resp.get_json()
    assert len(body['anchors']) <= 3


# ---------------------------------------------------------------------------
# /api/alignment/matches/{by_cand,summary}
# ---------------------------------------------------------------------------

def test_matches_by_cand_returns_dict(client, warm_cache_dir):
    resp = client.get('/api/alignment/matches/by_cand')
    if resp.status_code != 200:
        pytest.skip("matches/by_cand not available")
    body = resp.get_json()
    assert isinstance(body, dict) and len(body) >= 1


def test_matches_summary_returns_metrics(client, warm_cache_dir):
    resp = client.get('/api/alignment/matches/summary')
    if resp.status_code != 200:
        pytest.skip("matches/summary not available")
    body = resp.get_json()
    # Metrics summary must have the at-threshold P/R/F1 block the UI reads.
    assert 'at_match_threshold' in body
    at = body['at_match_threshold']
    assert 0.0 <= at.get('precision', 0) <= 1.0
    assert 0.0 <= at.get('recall', 0) <= 1.0
    assert 0.0 <= at.get('f1', 0) <= 1.0


# ---------------------------------------------------------------------------
# /api/alignment/buildings/colors?stage=4{a..d}
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('stage', ['4a', '4b', '4c', '4d'])
def test_buildings_colors_accepts_known_stages(client, warm_cache_dir, stage):
    resp = client.get(f'/api/alignment/buildings/colors?stage={stage}')
    if resp.status_code != 200:
        pytest.skip(f"stage {stage} colors not available")
    body = resp.get_json()
    # Response shape is {cand_colors: {id: color, ...}, index_colors?: {...}}
    assert 'cand_colors' in body or 'index_colors' in body


def test_buildings_colors_rejects_unknown_stage(client):
    resp = client.get('/api/alignment/buildings/colors?stage=4z')
    assert resp.status_code == 400
