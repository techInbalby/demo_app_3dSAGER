# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Flask web app that runs the **3dSAGER inference pipeline live** end-to-end on two locked CityJSON inputs (Source A = candidates, Source B = index), with each UI step driving a Celery task that produces the next pipeline artifact. Step 4 ("Spatial alignment") shows the new `RigidAligner` story across four sub-stages: misaligned → anchors → transform applied → final matches.

The frontend uses CesiumJS (main 3D viewer) and Three.js (side-by-side building comparison), loaded via CDN — there is no JS build pipeline.

## Common commands

Local dev (Redis and a Celery worker must be running):

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -r demo_infrance_pipeline/requirements.txt   # heavier ML deps (faiss, xgboost, ...)
redis-server &
celery -A tasks.celery worker --loglevel=info &
python app.py                          # http://localhost:5000
```

One-time input setup (locks Source A/B to the two pipeline files and archives the rest):

```bash
bash scripts/setup_demo_inputs.sh        # --dry-run for preview
python scripts/prebake_cityjson.py       # generate .prebaked.json siblings
```

Docker (mirrors production layout — `web` on host port 5000, plus `worker` and `redis`):

```bash
docker-compose up --build
```

Standalone pipeline CLI (also runnable outside the app):

```bash
python demo_infrance_pipeline/inference.py                  # full pipeline up to align
python demo_infrance_pipeline/inference.py --stage classify # stop earlier
```

**No test suite exists** in this repo — don't go looking for one.

## Architecture

Four-stage live pipeline driven by the existing Celery worker:

```
Step 1 (Features)  → preprocess + properties      (≈30–60 s cold)
Step 2 (BKAFI)     → blocking                     (≈seconds)
Step 3 (Matches)   → classify  (pre-alignment)    (≈seconds)
Step 4 (Alignment) → align + save + prebake       (≈seconds + 4 sub-stages)
```

- **Per-input-hash cache:** every stage's output lives at `results_demo/cache/<input_hash>/`, where `input_hash` is a 16-char SHA-256 prefix over the two input file paths + sizes + mtimes + a config version. Same inputs → instant cache HITs on re-runs; different inputs auto-bust the cache.
- **Three-process runtime:** Flask web (`app.py`) ↔ Redis ↔ Celery worker (`tasks.py`). The single Celery task `pipeline_run(target_stage, ...)` calls `pipeline_stages.run_through(target_stage, cache_dir, ...)`, which chains all prerequisite stages, each independently cache-aware.
- **Legacy bridging:** after each stage the worker also populates Redis (`features:<file>`, `bkafi:flat`, `bkafi:by_file`) and rewrites `results_demo/demo_inference/demo_metrics_summary_seed1.json` from the cache_dir outputs. This keeps the existing `/api/building/*`, `/api/buildings/status`, `/api/classifier/summary` routes working unchanged.
- **Build-time coordinate pre-baking:** `scripts/prebake_cityjson.py` transforms CityJSON from EPSG:7415 / EPSG:28992 → WGS84. It walks **both** `data/` (locked Source A/B files) and `results_demo/cache/<hash>/` (the new aligned + post-disaster CityJSON files), so the viewer's fast WGS84 path works for all of them. The `pipeline_run` Celery task also calls `prebake_file()` on its alignment outputs at the end of `stage_align`.
- **DisasterSimulator translation override:** demo runs read translation magnitude from `config_demo.DEMO_CRS_TRANSLATION_MAX` (default 500 m). The upstream ±100 km would put post-disaster cands outside the WGS84 projection's valid extent — the override keeps them visible.
- **Building color coding** (in the 3D viewer, `static/js/cesium-cityjson-viewer.js:BUILDING_COLOR_MAP`):
  - Steps 1–3: blue / orange / yellow / green / red / darkgray (default → has features → has BKAFI → TP / FP / no-match)
  - Step 4 sub-stages: `cand_misaligned` / `anchor_cand` / `anchor_index` / `false_negative` (plus reuse of `blue` and `green`/`red`/`darkgray` in 4d)

## Key files

- `app.py` (~1600 lines) — all legacy Flask routes + blueprint registrations. Single-file backend (refactor candidate).
- `tasks.py` — `pipeline_run` Celery task + legacy `calculate_features` / `load_bkafi_results` (deprecated, kept for backward compat) + the legacy-bridging helpers (`_bridge_to_legacy` and friends).
- `pipeline/` — Flask blueprint at `/api/pipeline`: `start`, `status/<task_id>`, `manifest`, `cache`. Cache helpers in `pipeline/cache.py`.
- `align_api/` — Flask blueprint at `/api/alignment`: `status`, `anchors`, `matches/by_cand`, `matches/summary`, `cityjson?stage=…`, `buildings/colors?stage=4a..4d`. Loaders cache file reads by mtime in `align_api/loaders.py`. (Package is named `align_api` rather than `alignment` to avoid colliding with `demo_infrance_pipeline/modules/alignment.py` on sys.path.)
- `demo_infrance_pipeline/` — self-contained inference pipeline bundle.
  - `pipeline_stages.py` — five cache-aware `stage_*` functions + `run_through(target_stage, …)`.
  - `inference.py` — thin CLI around `run_through`.
  - `modules/` — preprocess, properties, blocking, classify, RigidAligner, DisasterSimulator. `alignment.write_cands_cityjson` is the public CityJSON serializer.
  - `saved_models/` — committed; small XGBoost + BKAFI artifacts (~1 MB).
- `static/js/pipeline_runner.js` — generic Celery-task poller (`PipelineRunner.start(stage, {onProgress, onComplete, onError})`).
- `static/js/alignment.js` — Step 4 controller (`AlignmentStep.run / setSubStage / prev / next / toggleAutoAdvance / reset`). Each sub-stage fetches `/api/alignment/buildings/colors?stage=…` and recolors the misaligned / aligned layers.
- `static/js/demo.js` — main frontend controller (~4500 lines, refactor candidate). Calls `PipelineRunner` from each step button.
- `static/js/cesium-cityjson-viewer.js`, `static/js/three-building-viewer.js` — the two 3D viewers.
- `templates/demo.html` — main UI; `templates/index.html` — landing page.
- `scripts/setup_demo_inputs.sh` — one-shot, idempotent input re-stager.
- `scripts/prebake_cityjson.py` — build-time CRS transform; honors `DATA_DIR` and `EXTRA_PREBAKE_DIRS`.
- `deploy/gunicorn.conf.py` — Gunicorn config (4 workers × 2 threads, 180 s timeout).
- `docker-compose.yml` — defines `web` (host 5000 → container 5000), `worker`, `redis`.

## Environment variables

Read by `app.py`, `deploy/gunicorn.conf.py`, and `scripts/prebake_cityjson.py`:

- `REDIS_URL` (default `redis://redis:6379/0`)
- `CACHE_TTL_SECONDS` (default `21600`)
- `CESIUM_ION_TOKEN` (optional — basemap in `demo.html`)
- `FLASK_ENV` (`development` enables Flask debug)
- `PORT` (default `5000`)
- `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`, `GUNICORN_BIND`, `GUNICORN_MAX_REQUESTS`, `GUNICORN_MAX_REQUESTS_JITTER`
- `DATA_DIR` (default `/app/data` in Docker)
- `EXTRA_PREBAKE_DIRS` (comma-separated list; default `<repo>/results_demo/cache`)

## Gotchas

- Source A and Source B directories follow the **inference pipeline's convention** — Source A = cands, Source B = index. The demo is locked to one CityJSON per source (`scripts/setup_demo_inputs.sh` enforces this).
- The legacy confidence cutoff `CONFIDENCE_THRESHOLD = 0.5` is hardcoded at `app.py:37` and `tasks._LEGACY_THRESHOLD`; the new alignment match threshold is 0.65 (`pipeline_stages.DEFAULT_MATCH_THRESHOLD`).
- Aligned CityJSON keys are `bag_<id>` (e.g. `bag_0518100000208854`); `matches.csv` and `anchor_pairs.json` use the **raw** numeric ID. The Cesium viewer's `idMapping` resolves both, and `alignment/loaders.build_sub_stage_colors` keys responses by raw IDs.
- `RigidAligner._write_cityjson` writes to `config.FilePaths.results_path`; `stage_align` overrides this to the run's cache_dir for the duration of `aligner.run()`. Single-worker assumption (fine for the demo today).
- The first cold pipeline run takes ~1–3 min. `PipelineRunner` polls `/api/pipeline/status` every 500 ms and surfaces `{stage, message, elapsed_s}` so the UI shows a live label, not a static spinner.
- `__pycache__/`, `logs/`, and `results_demo/cache/` are runtime artifacts. `results_demo/`, `data/`, `saved_model_files/`, and `*.zip` are gitignored.
