#!/usr/bin/env bash
# Run the test suite inside a one-off container based on the worker image,
# with the host repo mounted at /app. Heavy ML deps (faiss, xgboost, scipy)
# come from the baked image; pytest + fakeredis + playwright are installed
# at run time from requirements-dev.txt (cached in a docker volume).
#
# Usage:
#   scripts/run-tests.sh                  # default: unit + api (fast)
#   scripts/run-tests.sh -m integration   # slow pipeline E2E
#   scripts/run-tests.sh -m ui            # Playwright UI tests (web must be up)
#   scripts/run-tests.sh tests/unit/      # specific path
#   scripts/run-tests.sh --co -q          # collect only (smoke check)
set -euo pipefail

cd "$(dirname "$0")/.."

# A named volume keeps pip's cache + the installed dev-deps across runs so
# we don't reinstall pytest etc. every invocation.
PIP_VOLUME=demo_3dsager_test_pip
TEST_IMAGE=demo_app_3dsager-worker

# Build the worker image if missing (cheap when it's already there).
docker image inspect "$TEST_IMAGE" >/dev/null 2>&1 || docker compose build worker

# Run with the repo bind-mounted, redis service available, REDIS_URL pointing
# at the compose redis (works whether or not a worker is currently up).
exec docker run --rm -i \
    --network demo_app_3dsager_default \
    -v "$(pwd):/app" \
    -v "${PIP_VOLUME}:/usr/local/lib/python-extra" \
    -e REDIS_URL=redis://demo_app_3dsager_redis:6379/0 \
    -e PYTHONPATH=/app:/usr/local/lib/python-extra \
    -w /app \
    "$TEST_IMAGE" \
    bash -c '
        python -c "import sys, importlib.util; sys.exit(0 if importlib.util.find_spec(\"pytest\") else 1)" \
            || pip install --quiet --target=/usr/local/lib/python-extra -r requirements-dev.txt
        exec python -m pytest "$@"
    ' -- "$@"
