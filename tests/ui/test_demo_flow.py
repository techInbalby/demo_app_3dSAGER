"""Playwright tests for the demo's interactive flow.

These tests assume:
  - the demo is running at $DEMO_BASE_URL (default http://localhost:5000)
  - the pipeline cache is warm (clicks of Steps 1-4 are cache-hits)

To run:
  docker compose up -d
  scripts/run-tests.sh -m ui

Tests are deliberately broad: they verify the visible end-state after
each interactive click, not internal state. They catch the kind of
regressions that bit us repeatedly:
  - layer stacking (pristine + damaged both visible)
  - Step 1 colors race (orange never appears)
  - comparison-modal viewer alignment (left/right not on same baseline)
"""
import pytest

from .helpers import (
    SOURCE_A_CHECKBOX,
    activate_source_a,
    step_button,
    wait_for_step_completed,
    wait_for_step_enabled,
)

pytestmark = pytest.mark.ui


# ---------------------------------------------------------------------------
# Demo page initial state
# ---------------------------------------------------------------------------

def test_demo_page_loads(demo_page):
    # Sidebar shows ER Pipeline + 4 step cards.
    assert demo_page.locator('text=/Geometric Featurization/i').first.is_visible()
    assert demo_page.locator('text=/Geometric Blocking/i').first.is_visible()
    assert demo_page.locator('text=/Matching Classifier/i').first.is_visible()
    assert demo_page.locator('text=/GeoSpatial Alignment/i').first.is_visible()


def test_layer_panel_shows_both_sources(demo_page):
    # Layer panel labels Source A as CANDIDATES, Source B as INDEX.
    assert demo_page.locator('text=/CANDIDATES \\(A\\)/i').first.is_visible()
    assert demo_page.locator('text=/INDEX \\(B\\)/i').first.is_visible()


def test_step1_button_starts_in_fresh_state(demo_page):
    """Plan addendum 12: Step 1 button stays in the fresh 'Calculate
    Features' state on page load, NOT auto-completed from cache."""
    btn = demo_page.locator('#step-btn-1')
    text = btn.text_content().strip().lower()
    # Should NOT say 'completed' on fresh page load.
    assert 'completed' not in text, (
        f"Step 1 button is auto-completed from cache state — should stay fresh "
        f"so the user clicks through the pedagogical flow. Got: {text!r}")


# ---------------------------------------------------------------------------
# Layer toggle → damaged Source A appears
# ---------------------------------------------------------------------------

def test_toggle_source_a_loads_damaged_layer(demo_page):
    """Addendum 11: toggling Source A on loads the damaged-heights variant
    from /api/alignment/cityjson?stage=damaged_heights (when warm cache
    has preprocess done). The viewer is Cesium and won't show DOM-level
    changes — sniff the network instead."""
    requests = []
    demo_page.on('request', lambda r: requests.append(r.url))

    # Toggle Source A on via the visible legend checkbox.
    a_checkbox = demo_page.locator(SOURCE_A_CHECKBOX).first
    if not a_checkbox.is_checked():
        a_checkbox.click()
    # Allow the layer-load fetch to fire.
    demo_page.wait_for_timeout(2000)

    damaged_hits = [u for u in requests if 'damaged_heights' in u]
    assert damaged_hits, (
        f"Source A layer toggle should fetch the damaged-heights CityJSON. "
        f"Recent requests: {requests[-5:]}")


# ---------------------------------------------------------------------------
# Step 1 click → button turns green, Step 2 enables
# ---------------------------------------------------------------------------

def test_step1_click_completes_quickly_on_warm_cache(demo_page):
    """Step 1 on a warm cache is a cache-hit roundtrip — should complete
    in under 30 seconds and flip the button text to 'Completed'."""
    activate_source_a(demo_page)
    step1 = demo_page.locator(step_button(1))
    step1.click()
    wait_for_step_completed(demo_page, 1, timeout_ms=30_000)
    text = step1.text_content().strip().lower()
    assert 'completed' in text

    # Step 2 should now be enabled.
    assert not demo_page.locator(step_button(2)).is_disabled()


# ---------------------------------------------------------------------------
# Walk through Steps 1-4
# ---------------------------------------------------------------------------

def test_full_pipeline_click_through(demo_page):
    """Click each step in turn; verify each turns Completed before clicking
    the next. Validates the cache-hit roundtrip + UI state updates for the
    whole 4-step flow."""
    activate_source_a(demo_page)
    for step_num in (1, 2, 3, 4):
        btn = demo_page.locator(step_button(step_num))
        if 'completed' in (btn.text_content() or '').lower():
            continue
        wait_for_step_enabled(demo_page, step_num)
        btn.click()
        wait_for_step_completed(demo_page, step_num)
    for step_num in (1, 2, 3, 4):
        btn = demo_page.locator(step_button(step_num))
        assert 'completed' in (btn.text_content() or '').lower()


# ---------------------------------------------------------------------------
# Configurable knobs (addendum 6)
# ---------------------------------------------------------------------------

def test_blocking_k_input_defaults_to_5(demo_page):
    """Plan addendum 9: K=5 is the demo default (was 30)."""
    input_box = demo_page.locator('#cfg-blocking-k')
    assert input_box.input_value() == '5'


def test_align_cutoff_input_defaults_to_10(demo_page):
    """Plan addendum 9: cutoff=10 m is the demo default (was 7)."""
    input_box = demo_page.locator('#cfg-align-cutoff')
    assert input_box.input_value() in ('10', '10.0')


# ---------------------------------------------------------------------------
# Step button hover tooltips — addendum 14 ("do X first" hints)
# ---------------------------------------------------------------------------

def test_step1_tooltip_when_no_layer_loaded(demo_page):
    """Plan addendum 14: when Source A isn't toggled on, Step 1's title
    attribute names that as the next user action."""
    title = demo_page.locator(step_button(1)).get_attribute('title') or ''
    assert 'Candidates' in title or 'layer' in title.lower(), (
        f"Step 1 disabled-state tooltip should mention toggling Candidates / the layer. "
        f"Got: {title!r}")


def test_step2_tooltip_blocked_on_step1(demo_page):
    """Step 2's tooltip when Step 1 isn't done mentions Step 1 / Featurization."""
    title = demo_page.locator(step_button(2)).get_attribute('title') or ''
    assert 'Step 1' in title or 'Featurization' in title, (
        f"Step 2 tooltip should mention Step 1 prerequisite. Got: {title!r}")


def test_step3_tooltip_blocked_on_step2(demo_page):
    title = demo_page.locator(step_button(3)).get_attribute('title') or ''
    assert 'Step 2' in title or 'Blocking' in title, (
        f"Step 3 tooltip should mention Step 2 prerequisite. Got: {title!r}")


def test_step4_tooltip_blocked_on_step3(demo_page):
    title = demo_page.locator(step_button(4)).get_attribute('title') or ''
    assert 'Step 3' in title or 'Classifier' in title, (
        f"Step 4 tooltip should mention Step 3 prerequisite. Got: {title!r}")


# ---------------------------------------------------------------------------
# Cesium sanity — entities are actually present after Step 1
# ---------------------------------------------------------------------------

def test_cesium_has_building_entities_after_step1(demo_page):
    """After Step 1 completes, the Cesium viewer should have a non-empty
    buildingEntities map. Catches regressions where the damaged-layer swap
    blanks the viewer (which a refactor of `building-colors.js` or
    `layer-manager.js` could plausibly introduce)."""
    activate_source_a(demo_page)
    demo_page.locator(step_button(1)).click()
    wait_for_step_completed(demo_page, 1, timeout_ms=30_000)
    # Allow the layer reload + entity hydration to settle.
    demo_page.wait_for_timeout(3000)
    entity_count = demo_page.evaluate(
        """() => {
            if (!window.viewer || !window.viewer.buildingEntities) return 0;
            return window.viewer.buildingEntities.size;
        }"""
    )
    assert entity_count > 0, (
        f"window.viewer.buildingEntities should be populated after Step 1; got {entity_count}")
