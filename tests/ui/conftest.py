"""Playwright fixtures + base URL for the UI test suite.

The UI tests drive a real browser against the running demo at
http://localhost:5000. Bring it up first with `docker compose up -d`.
"""
import os

import pytest


@pytest.fixture(scope='session')
def base_url():
    return os.environ.get('DEMO_BASE_URL', 'http://localhost:5000')


@pytest.fixture(scope='session')
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        'viewport': {'width': 1440, 'height': 900},
        'ignore_https_errors': True,
    }


@pytest.fixture
def demo_page(page, base_url):
    """Open /demo, wait for the layer-picker to render."""
    page.goto(f'{base_url}/demo')
    page.wait_for_selector('#viewer-legend-items', state='attached', timeout=10_000)
    return page
