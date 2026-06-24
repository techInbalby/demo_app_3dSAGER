# Refactor status — resume point

Branch: **`refactor-plan`** (4 commits ahead of `origin/main`, not yet pushed).
Full plan: `~/.claude/plans/fluttering-tumbling-moler.md` (search "Plan addendum 15").

## Done (Phase 1 — backend)

| # | Commit | What |
|---|---|---|
| 1.1 | `47a14e4` | Extract `lib/config.py` — constants (BASE_DIR, REDIS_URL, CONFIDENCE_THRESHOLD, …) |
| 1.2 | `f213673` | Extract `lib/cache.py` — Redis client + cache_{get,set}_json + in-memory mirrors |
| 1.3 | `2054b92` | Extract `lib/id_utils.py` — `extract_numeric_id()` replaces 9 inline duplicates + 12 new tests |
| —   | `5d22855` | Dockerfile: COPY `lib/` into the image (web container fix) |

## Test state

- **95 passing** / 2 deliberate skips (was 82 before refactor).
- New: `tests/unit/test_id_utils.py` (12 tests).
- All existing API + integration tests still green.

## Docker state

Last verified: web + worker + redis containers all healthy on http://localhost:5000.

## Pending — Phase 1 (backend, in order)

| # | Task | Approx LoC | Notes |
|---|---|---|---|
| 1.4 | `data_api/` blueprint | ~180 | `/api/data/files`, `/api/data/select`, `/api/data/file` (both forms). Low-risk. |
| 1.5 | `features_api/` blueprint | ~360 | `/api/features/calculate`, `/api/building/features/<id>`. **HIGH RISK** — 6 numeric-ID match strategies. |
| 1.6 | `bkafi_api/` blueprint | ~295 | `/api/bkafi/load`, `/api/building/bkafi/<id>`, `/api/building/matches/<id>`. |
| 1.7 | `building_api/` blueprint | ~240 | `/api/building/single/<id>`, `/api/building/find-file/<id>`. **HIGH RISK** — vertex remap. |
| 1.8 | `status_api/` blueprint | ~290 | `/api/buildings/status`, `/api/classifier/summary`. **HIGH RISK** — match-status prioritization. |
| 1.9 | Delete deprecated routes | -65 | `/api/features/result`, `/api/bkafi/result`, `/api/bkafi/load` (replaced by `/api/pipeline/*`). |

Pattern for each: copy the route(s) into a new blueprint package mirroring `pipeline/` and `align_api/`; register in `app.py`; remove originals; `scripts/run-tests.sh -q` must stay green; rebuild docker; curl-smoke the moved routes.

## Pending — Phase 2 (frontend, after Phase 1)

11 IIFE modules carved out of `static/js/demo.js`. Sequenced low-risk → high. See plan addendum 15 for the full list.

## How to resume

```bash
cd /data/home/sagerdev/demo_app_3dSAGER
git checkout refactor-plan
git log --oneline -6      # confirm last commit is 5d22855
scripts/run-tests.sh -q   # confirm 95 still pass
docker compose ps         # confirm web is healthy
# Then start commit 1.4 (data_api/ blueprint).
```

When all 9 backend commits are in: push, open PR, smoke `/demo` manually, then start Phase 2.

## Open question for next session

Push the 4 current commits to `origin/refactor-plan` before continuing, or batch the whole Phase 1 into one push at the end? My default is push-as-you-go for visibility, but either works.
