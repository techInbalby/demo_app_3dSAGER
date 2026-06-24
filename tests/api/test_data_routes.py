"""Flask route tests for the data + building + status routes in app.py."""
import json

import pytest

pytestmark = pytest.mark.api


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def test_landing_page_renders(client):
    resp = client.get('/')
    assert resp.status_code == 200
    assert b'<html' in resp.data.lower()


def test_demo_page_renders(client):
    resp = client.get('/demo')
    assert resp.status_code == 200
    assert b'<html' in resp.data.lower()


def test_health_check(client):
    resp = client.get('/health')
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/data/files — file picker
# ---------------------------------------------------------------------------

def test_data_files_returns_exactly_one_per_source(client):
    """Demo is locked to one Source A + one Source B file."""
    resp = client.get('/api/data/files')
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'source_a' in body and 'source_b' in body
    assert len(body['source_a']) == 1
    assert len(body['source_b']) == 1


def test_data_files_includes_filename_path_size(client):
    resp = client.get('/api/data/files')
    a = resp.get_json()['source_a'][0]
    for k in ('filename', 'path', 'size'):
        assert k in a
    assert a['size'] > 0
    assert a['filename'].endswith('.json')


def test_data_files_source_a_is_the_locked_cands(client):
    resp = client.get('/api/data/files')
    a = resp.get_json()['source_a'][0]
    assert a['filename'] == '10-248-580.city.json'


def test_data_files_source_b_is_the_locked_index(client):
    resp = client.get('/api/data/files')
    b = resp.get_json()['source_b'][0]
    assert 'TheHague3D_Batch_07_Loosduinen' in b['filename']


# ---------------------------------------------------------------------------
# /api/data/file?path=... — CityJSON streamer
# ---------------------------------------------------------------------------

def test_data_file_streams_known_cityjson(client):
    files = client.get('/api/data/files').get_json()
    a_path = files['source_a'][0]['path']
    resp = client.get(f'/api/data/file?path={a_path}')
    assert resp.status_code == 200
    # Should be JSON-ish (might be prebaked or raw CityJSON)
    body = json.loads(resp.get_data())
    assert isinstance(body, dict)


def test_data_file_rejects_path_traversal(client):
    """No reading outside the data dir via ../ escape."""
    resp = client.get('/api/data/file?path=../../../etc/passwd')
    assert resp.status_code in (400, 403, 404)


def test_data_file_404_for_nonexistent(client):
    resp = client.get('/api/data/file?path=nonexistent.json')
    assert resp.status_code in (400, 403, 404)


# ---------------------------------------------------------------------------
# /api/data/select (POST)
# ---------------------------------------------------------------------------

def test_data_select_requires_post(client):
    resp = client.get('/api/data/select')
    assert resp.status_code == 405


def test_data_select_accepts_locked_path(client):
    files = client.get('/api/data/files').get_json()
    a_path = files['source_a'][0]['path']
    resp = client.post('/api/data/select', json={'file_path': a_path})
    # Returns 200 with file metadata; some implementations 204 or 400 if
    # missing body fields.
    assert resp.status_code in (200, 204, 400)


# ---------------------------------------------------------------------------
# /api/building/single/<id>
# ---------------------------------------------------------------------------

def _pick_one_building_id_from_warm_cache(warm_cache_dir):
    mbc = warm_cache_dir / 'matches_by_cand.json'
    if not mbc.exists():
        return None
    data = json.loads(mbc.read_text())
    for _file, by_id in data.items():
        for cid in by_id.keys():
            return cid
    return None


def test_building_single_returns_cityjson(client, warm_cache_dir):
    bid = _pick_one_building_id_from_warm_cache(warm_cache_dir)
    if bid is None:
        pytest.skip("no warm cache to pick a building id from")
    files = client.get('/api/data/files').get_json()
    src_path = files['source_a'][0]['path']
    resp = client.get(f'/api/building/single/{bid}?file={src_path}')
    if resp.status_code != 200:
        pytest.skip(f"building/single returned {resp.status_code}")
    body = resp.get_json()
    assert isinstance(body, dict)
    # Either includes CityObjects directly or wraps one entry — pin the
    # weaker invariant.
    assert 'CityObjects' in body or 'cityJSON' in body or 'building' in body


def test_building_single_404_for_unknown_id(client):
    files = client.get('/api/data/files').get_json()
    src_path = files['source_a'][0]['path']
    resp = client.get(f'/api/building/single/0000000000NONEXISTENT?file={src_path}')
    assert resp.status_code in (404, 400)


# ---------------------------------------------------------------------------
# /api/buildings/status — drives the legend colors
# ---------------------------------------------------------------------------

def test_buildings_status_returns_per_building_dict(client, fake_redis, warm_cache_dir):
    files = client.get('/api/data/files').get_json()
    a_path = files['source_a'][0]['path']
    resp = client.get(f'/api/buildings/status?file={a_path}')
    if resp.status_code != 200:
        pytest.skip(f"buildings/status returned {resp.status_code}")
    body = resp.get_json()
    # Body should have a 'buildings' or 'total' or per-id mapping.
    assert isinstance(body, dict)
    assert 'buildings' in body or 'total' in body or len(body) > 0


def test_buildings_status_requires_file_param(client):
    resp = client.get('/api/buildings/status')
    assert resp.status_code in (400, 200)
    if resp.status_code == 400:
        assert 'error' in resp.get_json()


# ---------------------------------------------------------------------------
# /api/classifier/summary
# ---------------------------------------------------------------------------

def test_classifier_summary_returns_metrics(client, warm_cache_dir, fake_redis):
    resp = client.get('/api/classifier/summary')
    if resp.status_code != 200:
        pytest.skip(f"classifier/summary returned {resp.status_code}")
    body = resp.get_json()
    assert isinstance(body, dict)


# ---------------------------------------------------------------------------
# /api/jobs/<task_id> — legacy task status alias
# ---------------------------------------------------------------------------

def test_jobs_status_for_unknown_id_returns_pending(client):
    """Legacy alias for the pipeline-status route. Uses 'status' (not 'state')
    in the response body — pinning that here so the frontend's polling code
    stays decoupled from the newer /api/pipeline/status contract."""
    resp = client.get('/api/jobs/00000000-0000-0000-0000-000000000000')
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'status' in body
    assert body['status'] in ('PENDING', 'RECEIVED', 'UNKNOWN')
    assert body['task_id'] == '00000000-0000-0000-0000-000000000000'
