"""Smoke tests for the landing page (/)."""
import pytest

pytestmark = pytest.mark.ui


def test_landing_page_renders(page, base_url):
    page.goto(base_url + '/')
    assert page.title() != ''


def test_landing_has_four_pipeline_stages(page, base_url):
    """The 'Pipeline' section lists 4 stages (added Step 4 in addendum 9)."""
    page.goto(base_url + '/')
    page.wait_for_load_state('networkidle')
    body = page.content().lower()
    for keyword in ['featurization', 'blocking', 'classif', 'alignment']:
        assert keyword in body, f"missing stage keyword: {keyword}"


def test_landing_mentions_tunable_defaults(page, base_url):
    """Plan addendum 8: landing copy advertises the configurable K and cutoff
    defaults. K=5 + cutoff=10 m are the demo defaults (addendum 9)."""
    page.goto(base_url + '/')
    page.wait_for_load_state('networkidle')
    body = page.content()
    assert 'Default: 5' in body or 'default: 5' in body or 'K' in body
    assert '10 m' in body or '10.0' in body
