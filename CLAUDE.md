# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Flask web app that **visualizes pre-computed** 3dSAGER entity-matching results between two CityJSON building datasets (Source A = candidates, Source B = index). It does **not** train or run ML models at request time — it loads JSON outputs produced by the external 3dSAGER inference pipeline. The frontend uses CesiumJS (main 3D viewer) and Three.js (side-by-side building comparison), loaded via CDN — there is no JS build pipeline.

## Common commands

Local dev (Redis and a Celery worker must be running for async endpoints to resolve):

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
redis-server &
celery -A tasks.celery worker --loglevel=info &
python app.py                          # http://localhost:5000
```

Docker (mirrors production layout — `web` on host port 5001, plus `worker` and `redis`):

```bash
docker-compose up --build
```

Build-time data pre-baking (the Dockerfile already runs `prebake_cityjson.py`; run manually only when adding/changing CityJSON inputs):

```bash
python scripts/prebake_cityjson.py
python scripts/convert_joblib_to_parquet.py    # one-time feature-format migration
```

**No test suite exists** in this repo — don't go looking for one.

## Architecture

- **Three-process runtime:** Flask web (`app.py`) ↔ Redis ↔ Celery worker (`tasks.py`). Long operations — feature extraction and BKAFI load — are dispatched as Celery tasks; the frontend polls `/api/features/result` and `/api/bkafi/result` for completion.
- **Pre-computed results only:** Everything served comes from `results_demo/demo_inference/*.json` and `data/RawCitiesData/.../property_dicts/`. XGBoost is in `requirements.txt` but never invoked at runtime.
- **Build-time coordinate pre-baking:** `scripts/prebake_cityjson.py` transforms CityJSON from EPSG:7415 / EPSG:28992 → WGS84 at Docker build time, emitting `.prebaked.json` files. The client viewer relies on this — it generally does **not** do coordinate transforms itself (parsing drops from ~30s → <2s per file as a result).
- **Dual feature format:** Geometric features may live as either `features.parquet` (preferred) or a legacy `*.joblib` dict. `tasks.py` picks Parquet when present.
- **Caching layers:** Redis (TTL `CACHE_TTL_SECONDS`, default 6 h) for features / BKAFI / building-status; plus an in-process `features_cache` dict to skip Redis round-trips. If Redis is unreachable, caching silently degrades to `None` and the app still works.
- **Building color coding** (in the 3D viewer): blue = default, orange = has features, yellow = has BKAFI candidates, green = true positive, red = false positive, gray = no match. Essential context for any frontend change.

## Key files

- `app.py` (~1600 lines) — all Flask routes, caching helpers, data loaders. Single-file backend.
- `tasks.py` — Celery tasks: `calculate_features()`, `load_bkafi_results()`.
- `scripts/prebake_cityjson.py` — build-time CRS transform; touch this if coordinate handling breaks.
- `static/js/demo.js` — main frontend controller.
- `static/js/cesium-cityjson-viewer.js`, `static/js/three-building-viewer.js` — the two 3D viewers.
- `templates/demo.html` — main UI; `templates/index.html` — landing page.
- `deploy/gunicorn.conf.py` — Gunicorn config (4 workers × 2 threads, 180 s timeout).
- `docker-compose.yml` — defines `web` (host 5001 → container 5000), `worker`, `redis`.

## Environment variables

Read by `app.py` and `deploy/gunicorn.conf.py`:

- `REDIS_URL` (default `redis://redis:6379/0`)
- `CACHE_TTL_SECONDS` (default `21600`)
- `CESIUM_ION_TOKEN` (optional — basemap in `demo.html`; the local `.env` only sets this)
- `FLASK_ENV` (`development` enables Flask debug)
- `PORT` (default `5000`)
- `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`, `GUNICORN_BIND`, `GUNICORN_MAX_REQUESTS`, `GUNICORN_MAX_REQUESTS_JITTER`

## Gotchas

- Source A / Source B directories have **inconsistent naming** ("Source A" with a space, etc.). `app.py` tries multiple path patterns — don't normalize naively.
- The confidence cutoff `CONFIDENCE_THRESHOLD = 0.5` is hardcoded at `app.py:37`; promote it to an env var if a caller needs to tune it.
- `saved_model_files/` is empty in the demo — it's a placeholder for the full pipeline, not missing data.
- `__pycache__/` and `logs/` are runtime artifacts.
