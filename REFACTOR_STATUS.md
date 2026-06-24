# Refactor status

Branch: **`refactor-plan`** (pushed to `origin/refactor-plan`).
Full plan: `~/.claude/plans/fluttering-tumbling-moler.md` (search "Plan addendum 15").

## Phase 1 — backend (COMPLETE)

`app.py`: **1626 → 149 LoC** (-91%). All 9 commits pushed.

| # | Commit | Module added | app.py after | Notes |
|---|---|---|---|---|
| 1.1 | `47a14e4` | `lib/config.py` | 1624 | constants (BASE_DIR, REDIS_URL, CONFIDENCE_THRESHOLD, paths) |
| 1.2 | `f213673` | `lib/cache.py` | 1568 | Redis client + cache_{get,set}_json + in-memory mirrors |
| 1.3 | `2054b92` | `lib/id_utils.py` | 1526 | extract_numeric_id replaces 9 inline duplicates + 12 unit tests |
| —   | `5d22855` | Dockerfile fix | — | COPY lib/ into the image |
| 1.4 | `be842e8` | `data_api/` | 1306 | file picker (4 routes), helpers extracted |
| 1.5 | `c371580` | `features_api/` | 966 | features + loaders, 6-strategy ID matcher (HIGH-RISK done) |
| 1.6 | `546d255` | `bkafi_api/` | 700 | per-cand BKAFI lookups, ensure_cache_loaded helper |
| 1.7 | `1b6844a` | `building_api/` | 447 | + 12 geometry unit tests (HIGH-RISK done) |
| 1.8 | `9521dd6` | `status_api/` | 163 | match-status rollup + 24-field metric pivot (HIGH-RISK done) |
| 1.9 | `4efd4b2` | (deletes) | 149 | dropped 3 deprecated routes + load_bkafi_results Celery task |

**Test pack:** 107 passing (was 82 pre-refactor). Two regression-guard test suites added: `tests/unit/test_id_utils.py` (12 tests) + `tests/unit/test_building_geometry.py` (12 tests, covers Solid + MultiSurface vertex remap).

**app.py final shape (~149 LoC):**
- 3 pages: `/`, `/demo`, `/health`
- 7 blueprint registrations
- 1 legacy job-status alias (`/api/jobs/<id>`)
- 1 no-op cache-invalidation shim
- That's it.

**Docker state:** web container healthy on http://localhost:5000.

## Phase 2 — frontend (NOT STARTED)

Carve `static/js/demo.js` (~4500 LoC) into 11 IIFE modules using the same `window.ModuleName = {...}` convention as the existing `pipeline_runner.js` and `alignment.js`. Loaded via `<script>` tags in `demo.html`.

| # | Module | LoC est. | Risk | What it owns |
|---|---|---|---|---|
| 2.1 | `building-colors.js` | ~400 | Low | Cesium colour-update logic (pure utility, no in-bound deps) |
| 2.2 | `mobile-panel.js` | ~140 | Low | mobile slide-out panel |
| 2.3 | `viewer-controls.js` | ~270 | Low | misc viewer controls + dead-code prune |
| 2.4 | `layer-manager.js` | ~300 | Medium | layer toggle/visibility/dim |
| 2.5 | `viewer-legend.js` | ~100 | Medium | legend panel rendering |
| 2.6 | `tutorial-ui.js` + `tutorial.js` | ~600 | Medium | overview modal + beacon helpers |
| 2.7 | `file-loader.js` | ~150 | Low | hydratePipelineFromManifest |
| 2.8 | `pipeline-state.js` | ~150 | Medium | pipelineState + updatePipelineUI + tooltips |
| 2.9 | `pipeline-steps.js` | ~1100 | **HIGH** | Steps 1/2/3 click handlers + async polling |
| 2.10 | `building-panel.js` | ~700 | **HIGH** | properties window + alignment callout |
| 2.11 | `bkafi-comparison.js` | ~1000 | **HIGH** | three.js carousel + Pristine/Post-disaster toggle |

End state: `demo.js` becomes a ~200 LoC shell with shared globals (`pipelineState`, `layerState`, `selectedFile`, `allAvailableFiles`, `selectedBuildingId`) + DOMContentLoaded bootstrap.

**Risk note:** the frontend has no unit-test coverage (just Playwright smoke at click level). The 3 "HIGH" modules each have hundreds of cross-state reads/writes; pre-existing bugs are easy to disturb. Recommend doing 2.1–2.8 first (low/medium-risk wins), pausing for click-through verification, then approaching the big three deliberately.

## How to resume Phase 2

```bash
cd /data/home/sagerdev/demo_app_3dSAGER
git checkout refactor-plan
git log --oneline -1                # confirm 4efd4b2
scripts/run-tests.sh -q             # 107 passing
docker compose ps                   # web healthy
# Then start commit 2.1 (building-colors.js).
```

When Phase 2 is done: push, open PR back to main, manual click-through on /demo, then merge.

## Open questions for Phase 2

- **Module loading order in `demo.html`.** Each new IIFE module is a separate `<script>` tag; dependencies are implicit via `window.X` exports. Order matters — `building-colors.js` must load before `pipeline-steps.js`, etc. I'll add an explicit ordered list in `demo.html` rather than relying on filename order.
- **Tests for the extracted modules.** No unit-test infra for JS today. Options: (a) skip; lean on Playwright smoke. (b) Add a vitest setup (lightweight). I lean toward (a) for now — adding a JS toolchain is a bigger lift than the rest of Phase 2 combined.
