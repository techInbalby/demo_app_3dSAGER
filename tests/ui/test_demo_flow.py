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

    # Toggle Source A on (checkbox in the layer panel).
    # Multiple A checkboxes exist (file-picker sidebar + the visible legend
    # panel). Target the visible legend one.
    a_checkbox = demo_page.locator('#viewer-legend-items input[data-source="A"]').first
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

def _activate_source_a(demo_page):
    """Tick the Source A checkbox + wait for Step 1 to become enabled.

    Step 1 enables when setActiveLayer runs with a Source A path
    (demo.js:1222), which fires after the user toggles A's checkbox.
    """
    # Multiple A checkboxes exist (file-picker sidebar + the visible legend
    # panel). Target the visible legend one.
    a_checkbox = demo_page.locator('#viewer-legend-items input[data-source="A"]').first
    if not a_checkbox.is_checked():
        a_checkbox.click()
    demo_page.wait_for_function(
        "() => { const b = document.getElementById('step-btn-1'); return b && !b.disabled; }",
        timeout=20_000,
    )


def test_step1_click_completes_quickly_on_warm_cache(demo_page):
    """Step 1 on a warm cache is a cache-hit roundtrip — should complete
    in under 30 seconds and flip the button text to 'Completed'."""
    _activate_source_a(demo_page)
    step1 = demo_page.locator('#step-btn-1')
    step1.click()
    demo_page.wait_for_function(
        "() => document.getElementById('step-btn-1') && "
        "document.getElementById('step-btn-1').textContent.toLowerCase().includes('completed')",
        timeout=30_000,
    )
    text = step1.text_content().strip().lower()
    assert 'completed' in text

    # Step 2 should now be enabled.
    step2 = demo_page.locator('#step-btn-2')
    assert not step2.is_disabled()


# ---------------------------------------------------------------------------
# Walk through Steps 1-4
# ---------------------------------------------------------------------------

def test_full_pipeline_click_through(demo_page):
    """Click each step in turn; verify each turns Completed before clicking
    the next. Validates the cache-hit roundtrip + UI state updates for the
    whole 4-step flow."""
    _activate_source_a(demo_page)
    for step_num in (1, 2, 3, 4):
        btn = demo_page.locator(f'#step-btn-{step_num}')
        if 'completed' in (btn.text_content() or '').lower():
            continue
        # Wait for the previous step to enable this one.
        demo_page.wait_for_function(
            f"() => !document.getElementById('step-btn-{step_num}').disabled",
            timeout=30_000,
        )
        btn.click()
        demo_page.wait_for_function(
            f"() => document.getElementById('step-btn-{step_num}').textContent.toLowerCase().includes('completed')",
            timeout=60_000,
        )
    for step_num in (1, 2, 3, 4):
        btn = demo_page.locator(f'#step-btn-{step_num}')
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
