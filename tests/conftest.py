"""Shared pytest fixtures for the 3dSAGER demo test suite.

Three concerns the fixtures handle:

1. **Celery → eager mode** so `task.delay()` runs the task synchronously in
   the test process. No worker, no broker.
2. **Redis → fakeredis** so the legacy bridging code (`tasks._bridge_to_legacy`)
   and the building-status route can read/write Redis without a real server.
3. **Path discovery for the warm cache.** A single demo inference run
   populates `results_demo/cache/<input_hash>/` — many tests look up the
   first (only) cache_dir under that root and read its outputs directly.

Cold-start tests that need to run the full pipeline live (the integration
suite) use the `cache_dir_cold` fixture which creates a tmp dir under
`results_demo/cache_test/`.
"""
import os
import sys
from pathlib import Path

import pytest

# Repo root on sys.path so `import app`, `import tasks`, `import align_api`
# all resolve when pytest is invoked from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Pipeline bundle dir, mirroring tasks.py's runtime sys.path manipulation.
_PIPELINE_DIR = REPO_ROOT / 'demo_infrance_pipeline'
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))


# ---------------------------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def repo_root():
    return REPO_ROOT


@pytest.fixture(scope='session')
def cache_root():
    """The pipeline cache root (`results_demo/cache/`)."""
    return REPO_ROOT / 'results_demo' / 'cache'


@pytest.fixture(scope='session')
def warm_cache_dir(cache_root):
    """The most-recently-written cache_dir that's also for the current
    CONFIG_VERSION (i.e. matches the hash from compute_input_hash on the
    locked inputs). Older runs from a prior CONFIG_VERSION are skipped so
    tests don't pick up a stale dir that's missing newer artifacts."""
    if not cache_root.exists():
        pytest.skip(f"warm cache root missing: {cache_root}")
    # Prefer the cache_dir that matches the current input_hash. Falls back
    # to the newest dir if compute_input_hash can't be imported (e.g. on a
    # minimal test env).
    target = None
    try:
        from tests.conftest import REPO_ROOT  # type: ignore  # self-ref ok
        import pipeline_stages
        cands = REPO_ROOT / 'data' / 'source_a' / '10-248-580.city.json'
        index = REPO_ROOT / 'data' / 'source_b' / 'TheHague3D_Batch_07_Loosduinen_2022-08-08.json'
        if cands.exists() and index.exists():
            target = cache_root / pipeline_stages.compute_input_hash(str(cands), str(index))
    except Exception:
        target = None
    if target and target.exists() and (target / 'object_dict.joblib').exists():
        return target

    subdirs = sorted(
        [p for p in cache_root.iterdir() if p.is_dir() and (p / 'object_dict.joblib').exists()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not subdirs:
        pytest.skip("no warm cache dir under results_demo/cache/ — run the pipeline first")
    return subdirs[0]


@pytest.fixture(scope='session')
def locked_inputs(repo_root):
    """Paths to the two locked CityJSON inputs the demo runs on."""
    cands = repo_root / 'data' / 'source_a' / '10-248-580.city.json'
    index = repo_root / 'data' / 'source_b' / 'TheHague3D_Batch_07_Loosduinen_2022-08-08.json'
    if not cands.exists() or not index.exists():
        # Fall back to RawCitiesData layout (older deployments).
        cands = repo_root / 'data' / 'RawCitiesData' / 'The Hague' / 'Source A' / '10-248-580.city.json'
        index = repo_root / 'data' / 'RawCitiesData' / 'The Hague' / 'Source B' / 'TheHague3D_Batch_07_Loosduinen_2022-08-08.json'
    return {'cands': cands, 'index': index}


# ---------------------------------------------------------------------------
# Celery + Redis fakes
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session', autouse=True)
def _celery_eager():
    """Run Celery tasks inline. Applied once for the whole session.

    Heavy dependencies (numpy, joblib, faiss…) get imported transitively
    via `import tasks`. UI tests run in a Playwright container that lacks
    those deps; in that env we skip the eager-config silently — UI tests
    drive the live demo over HTTP and don't need Celery at all."""
    os.environ.setdefault('REDIS_URL', 'redis://fake')
    try:
        import tasks as tasks_module  # noqa: E402
    except ImportError:
        yield None
        return
    tasks_module.celery.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        broker_url='memory://',
        result_backend='cache+memory://',
    )
    yield tasks_module


@pytest.fixture
def fake_redis(monkeypatch):
    """Drop a fakeredis server in place of the real one for the duration of
    one test. Patches both `app._redis_client` (Flask side) and `tasks._redis_client`
    (Celery side) so the legacy-bridging keys are visible to both."""
    fakeredis = pytest.importorskip('fakeredis')
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)

    import app as app_module
    import tasks as tasks_module
    monkeypatch.setattr(app_module, '_redis_client', client, raising=False)
    monkeypatch.setattr(tasks_module, '_redis_client', lambda: client)
    yield client


# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------

@pytest.fixture
def app_obj():
    """The live Flask app (module-level singleton in app.py)."""
    import app as app_module
    app_module.app.config['TESTING'] = True
    return app_module.app


@pytest.fixture
def client(app_obj):
    """A Flask test client for hitting routes without a real HTTP server."""
    return app_obj.test_client()
