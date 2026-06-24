#!/usr/bin/env bash
# Run the Playwright UI tests against the live demo at http://localhost:5000.
#
# Uses the official mcr.microsoft.com/playwright/python image so the chromium
# binary + system deps are already installed. The demo must be running first
# (docker compose up -d).
#
# Usage:
#   scripts/run-ui-tests.sh                  # run all UI tests
#   scripts/run-ui-tests.sh -k landing       # filter by test name
#   scripts/run-ui-tests.sh --headed         # show the browser
set -euo pipefail

cd "$(dirname "$0")/.."

PIP_VOLUME=demo_3dsager_ui_pip
IMAGE="mcr.microsoft.com/playwright/python:v1.60.0-jammy"

# Quick health check that the demo is actually running.
if ! curl -fs -o /dev/null --max-time 3 http://localhost:5000/; then
    echo "ERROR: demo not reachable at http://localhost:5000/ — bring it up with: docker compose up -d" >&2
    exit 1
fi

exec docker run --rm -i \
    --network host \
    -v "$(pwd):/app" \
    -v "${PIP_VOLUME}:/usr/local/lib/python-extra" \
    -e DEMO_BASE_URL="${DEMO_BASE_URL:-http://localhost:5000}" \
    -e PYTHONPATH=/app:/usr/local/lib/python-extra \
    -w /app \
    "$IMAGE" \
    bash -c '
        python -c "import pytest_playwright" 2>/dev/null \
            || pip install --quiet --target=/usr/local/lib/python-extra \
                pytest==8.3.4 pytest-flask==1.3.0 pytest-playwright==0.6.2 pytest-mock==3.14.0 fakeredis==2.26.1
        exec python -m pytest -m ui "$@"
    ' -- "$@"
