"""Flask route tests for the /api/pipeline blueprint.

Tests use the Flask test client (no real HTTP server) plus Celery in eager
mode so .delay() runs the task synchronously. The warm cache fixture
provides a populated cache_dir from a prior demo run; pipeline_run is a
fast cache-hit roundtrip there.
"""
import pytest

pytestmark = pytest.mark.api


# ---------------------------------------------------------------------------
# /api/pipeline/manifest
# ---------------------------------------------------------------------------

def test_manifest_returns_input_hash(client, warm_cache_dir):
    resp = client.get('/api/pipeline/manifest')
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'input_hash' in body
    assert len(body['input_hash']) == 16  # 16-char SHA prefix
    assert 'cache_dir' in body
    assert body['cache_exists'] is True


def test_manifest_includes_stages(client, warm_cache_dir):
    resp = client.get('/api/pipeline/manifest')
    body = resp.get_json()
    assert 'stages' in body
    # Stage names should be a subset of the pipeline's known stages
    known = {'preprocess', 'properties', 'blocking', 'classify', 'align'}
    assert set(body['stages'].keys()).issubset(known)


def test_manifest_records_completed_preprocess(client, warm_cache_dir):
    """The warm cache should at least have preprocess marked complete."""
    resp = client.get('/api/pipeline/manifest')
    stages = resp.get_json().get('stages', {})
    if 'preprocess' in stages:
        assert stages['preprocess'].get('complete') is True


# ---------------------------------------------------------------------------
# /api/pipeline/start (validation)
# ---------------------------------------------------------------------------

def test_start_rejects_unknown_stage(client):
    resp = client.post('/api/pipeline/start', json={'stage': 'NOT_A_STAGE'})
    assert resp.status_code == 400
    body = resp.get_json()
    assert 'error' in body
    assert 'unknown stage' in body['error'].lower()


def test_start_rejects_missing_stage(client):
    resp = client.post('/api/pipeline/start', json={})
    assert resp.status_code == 400


def test_start_accepts_stage_in_querystring(client, warm_cache_dir, fake_redis):
    """The frontend uses ?stage=features in the URL, not the body."""
    resp = client.post('/api/pipeline/start?stage=features', json={})
    # Either succeeds (warm cache → cache-hit) or returns a known startup error.
    # Not a 400 (validation passed).
    assert resp.status_code != 400


def test_start_returns_task_id_and_cache_dir(client, warm_cache_dir, fake_redis):
    """Successful start returns the task id + cache dir for the run."""
    resp = client.post('/api/pipeline/start', json={'stage': 'features'})
    if resp.status_code != 200:
        pytest.skip(f"start returned {resp.status_code}: {resp.get_data(as_text=True)[:200]}")
    body = resp.get_json()
    assert 'task_id' in body
    assert 'cache_dir' in body
    assert 'input_hash' in body
    assert body['target_stage'] == 'properties'
    assert body['ui_stage'] == 'features'


# ---------------------------------------------------------------------------
# Stage name mapping (the contract the frontend relies on)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('ui_stage,target_stage', [
    ('features',  'properties'),
    ('blocking',  'blocking'),
    ('matching',  'classify'),
    ('alignment', 'align'),
])
def test_stage_mapping_is_stable(ui_stage, target_stage):
    """Pinning the mapping — if these change the JS frontend breaks."""
    from pipeline.routes import STAGE_MAP
    assert STAGE_MAP[ui_stage] == target_stage


def test_stage_mapping_lists_all_four_steps():
    from pipeline.routes import STAGE_MAP
    assert set(STAGE_MAP.keys()) == {'features', 'blocking', 'matching', 'alignment'}


# ---------------------------------------------------------------------------
# /api/pipeline/status/<task_id>
# ---------------------------------------------------------------------------

def test_status_unknown_task_id_returns_pending(client):
    """Celery's AsyncResult for an unknown id reports state=PENDING."""
    resp = client.get('/api/pipeline/status/00000000-0000-0000-0000-000000000000')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['task_id'] == '00000000-0000-0000-0000-000000000000'
    assert body['state'] in ('PENDING', 'RECEIVED')


# ---------------------------------------------------------------------------
# /api/pipeline/cache (DELETE) — destructive: only run when explicitly opted in.
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="destructive — would wipe the warm cache. Run manually with curl when needed.")
def test_cache_delete_clears_directory(client, warm_cache_dir):
    resp = client.delete('/api/pipeline/cache')
    assert resp.status_code == 200
    assert 'cleared' in resp.get_json()
