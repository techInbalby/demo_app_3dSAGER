# Tests

Four-layer test suite for the 3dSAGER demo, designed so a future refactor of `app.py` / `demo.js` can be verified end-to-end before merging.

| Layer | Path | Speed | When it runs |
|---|---|---|---|
| Unit | `tests/unit/` | <2 s | Every PR (default) |
| API | `tests/api/` | ~3 s | Every PR (default) |
| Integration (pipeline E2E) | `tests/integration/` | ~5 s warm / ~30 s cold | `-m integration` opt-in |
| UI (Playwright) | `tests/ui/` | ~70 s | `-m ui` opt-in, needs demo running |

## Quick start

```bash
# Fast suite (unit + API).
scripts/run-tests.sh

# Pipeline integration smoke (run the full 4 stages inline).
scripts/run-tests.sh -m integration -v

# Browser-driven UI tests against the live demo.
docker compose up -d
scripts/run-ui-tests.sh -v
```

## What's covered

### Unit (`tests/unit/`)
- `pipeline_stages.py` helpers: `compute_input_hash` determinism + config-version dependency, `_atomic_write_json`, `_record_stage`, `_invalidate_downstream_of_blocking` cascade, pinned `CONFIG_VERSION` + `DEFAULT_MATCH_THRESHOLD`.
- `align_api/loaders.py`: `slice_cand_from_post_disaster` (boundary walk + vertex remap + z=0 normalization), `damage_factor_for_cand` (raw + bag-prefix lookup), `cityjson_path` (prebaked preference + per-stage stem map), `_read_json_cached` mtime invalidation.

### API (`tests/api/`)
- `/api/pipeline/*` — manifest contract, stage-name mapping, validation errors, status route for unknown tasks.
- `/api/alignment/*` — every route's success + error path; pins the JSON shape (`nn_match`, `pool`, `cutoff_m`, etc.) the frontend reads.
- `/api/data/*`, `/api/building/single`, `/api/buildings/status`, `/api/classifier/summary`, `/api/jobs/<id>` — file-picker output, path-traversal rejection, response shape.

Uses Flask's test client (no real HTTP). Celery runs in eager mode (`task_always_eager=True`); Redis is faked with `fakeredis` where needed.

### Integration (`tests/integration/`)
- Runs `tasks.pipeline_run(target_stage='align', …)` inline against the locked Hague inputs.
- Asserts every expected cache artifact exists (`object_dict.joblib`, `metrics_summary.json`, `damaged_heights_only_cands.json`, …).
- Asserts `alignment_succeeded`, F1 + precision + recall in plausible ranges, manifest marks all 5 stages complete.
- Sanity-checks that the pipeline still applies the full disaster (CRS R/t + height damage) by comparing X/Y coords between the pristine-grounded `damaged_heights_only_cands.json` and the CRS-shifted `post_disaster_cands.json`.

### UI (`tests/ui/`)
- Landing page — pipeline-stage labels present, tunable defaults advertised.
- Demo flow — Step 1 doesn't auto-complete from cache state, Source A layer-toggle fires the damaged URL, full 1→2→3→4 click-through reaches all green Completed buttons, K + cutoff input defaults pinned.

Driven via Playwright in a separate `mcr.microsoft.com/playwright/python` container so the browser is bundled.

## Markers

```
unit         pure-Python helper tests (fast, no I/O)
api          Flask route tests via the test client
integration  exercises the live pipeline (slow)
ui           Playwright browser tests (requires demo running)
```

Default `pytest` invocation excludes `integration` and `ui` (see `pytest.ini`).

## Adding tests

- New API route → add a test in `tests/api/` pinning the JSON shape.
- New helper in `pipeline_stages.py` / `align_api/loaders.py` → add unit test.
- New cached artifact → add to the expected-artifacts list in `tests/integration/test_pipeline_smoke.py::test_align_outputs_exist_after_run`.
- New UI interaction → add a Playwright test in `tests/ui/`.

## Known limitations

- `fakeredis` is per-test (scope=function); session-wide redis state isn't shared between tests.
- UI tests assume warm cache. Wiping it (`curl -X DELETE /api/pipeline/cache`) before the UI suite would force ~30 s of preprocess before tests can start clicking — not done by default.
- The cold-cache pipeline E2E (`tests/integration/test_pipeline_smoke.py::test_pipeline_runs_through_align`) runs against the warm cache today; for a true cold run, delete the cache_dir first.
