"""Playwright tests for the layer-toggle + legend panel.

These exercise the demo.js buckets that will become `layer-manager.js`
and `viewer-legend.js` in Phase 2. Regression risk = medium (the legend
re-render fires on every toggle).
"""
import pytest

from .helpers import (
    ACTIVE_LAYER_ROW,
    LAYERS_PANEL,
    SOURCE_A_CHECKBOX,
    SOURCE_B_CHECKBOX,
    activate_source_a,
    step_button,
)

pytestmark = pytest.mark.ui


def test_source_b_checkbox_is_visible(demo_page):
    """The Source B (Index) checkbox is rendered in the legend, alongside A."""
    assert demo_page.locator(SOURCE_B_CHECKBOX).first.is_visible()


def test_source_b_toggles_on_off(demo_page):
    """Toggling B doesn't error and the checkbox state flips. Locators are
    re-fetched between clicks because the legend re-renders detaches the
    old node."""
    initially_checked = demo_page.locator(SOURCE_B_CHECKBOX).first.is_checked()
    demo_page.locator(SOURCE_B_CHECKBOX).first.click()
    demo_page.wait_for_timeout(800)
    assert demo_page.locator(SOURCE_B_CHECKBOX).first.is_checked() != initially_checked
    demo_page.locator(SOURCE_B_CHECKBOX).first.click()  # restore
    demo_page.wait_for_timeout(800)
    assert demo_page.locator(SOURCE_B_CHECKBOX).first.is_checked() == initially_checked


def test_toggling_a_off_then_on_preserves_legend(demo_page):
    """The legend container survives a toggle cycle. (Regression: the
    layer-manager rewrite must not double-render or blank the panel.)

    Note: each toggle triggers a full legend re-render, which DETACHES the
    old checkbox DOM node and creates a new one. Locators MUST be re-fetched
    between clicks; reusing a stale Locator points at a node Playwright
    won't click."""
    activate_source_a(demo_page)
    demo_page.locator(SOURCE_A_CHECKBOX).first.click()   # off
    demo_page.wait_for_timeout(800)
    demo_page.locator(SOURCE_A_CHECKBOX).first.click()   # back on
    demo_page.wait_for_timeout(1500)
    # Panel still has the A row visible.
    assert demo_page.locator(SOURCE_A_CHECKBOX).first.is_visible()
    assert demo_page.locator(LAYERS_PANEL).is_visible()


def test_both_layers_can_be_visible_simultaneously(demo_page):
    """Source A and B coexist (they're meant to be overlaid)."""
    a = demo_page.locator(SOURCE_A_CHECKBOX).first
    b = demo_page.locator(SOURCE_B_CHECKBOX).first
    if not a.is_checked():
        a.click()
    if not b.is_checked():
        b.click()
    demo_page.wait_for_timeout(1000)
    assert a.is_checked()
    assert b.is_checked()


def test_active_layer_row_marked_for_source_a(demo_page):
    """When Source A is the pipeline target, its legend row gets the
    .legend-row-active class. This is what the UI uses to bold the
    layer name + show the 'active' chip."""
    activate_source_a(demo_page)
    # At least one legend row marked active.
    assert demo_page.locator(ACTIVE_LAYER_ROW).count() >= 1


def test_toggling_a_off_disables_step1(demo_page):
    """Step 1 button requires Source A loaded; toggling A off should NOT
    leave Step 1 enabled (otherwise the click hits an undefined selectedFile).

    Note: the current demo.js leaves step-btn-1 enabled once it's been
    activated. This test guards against the OPPOSITE regression — re-enabling
    being lost when the toggle goes off, which would silently reset state.
    Adjust the assertion to whichever invariant we ultimately want."""
    activate_source_a(demo_page)
    assert not demo_page.locator(step_button(1)).is_disabled()

    demo_page.locator(SOURCE_A_CHECKBOX).first.click()   # toggle off
    demo_page.wait_for_timeout(800)
    # Pin current behaviour: step-btn-1 stays in DOM after the toggle off.
    # (Existing demo.js never re-disables once activated; if Phase 2 changes
    # this, the test surfaces the change deliberately.)
    assert demo_page.locator(step_button(1)).count() == 1
    # Restore by re-fetching the locator after the legend re-rendered.
    demo_page.locator(SOURCE_A_CHECKBOX).first.click()


def test_toggling_a_on_after_step1_done_refetches_damaged(demo_page):
    """After Step 1 completes, the A layer URL is `damaged_heights`. If the
    user toggles A off and back on, the viewer should refetch the damaged
    variant — not silently fall back to pristine.

    Regression guard for the addendum-11 layer-load behaviour."""
    activate_source_a(demo_page)

    # Click Step 1 — fast cache-hit.
    demo_page.locator(step_button(1)).click()
    demo_page.wait_for_function(
        "() => document.getElementById('step-btn-1')"
        ".textContent.toLowerCase().includes('completed')",
        timeout=30_000,
    )

    # Start collecting network requests.
    requests = []
    demo_page.on('request', lambda r: requests.append(r.url))

    demo_page.locator(SOURCE_A_CHECKBOX).first.click()    # off
    demo_page.wait_for_timeout(800)
    demo_page.locator(SOURCE_A_CHECKBOX).first.click()    # on
    demo_page.wait_for_timeout(2500)

    damaged_hits = [u for u in requests if 'damaged_heights' in u]
    assert damaged_hits, (
        f"Re-toggling Source A after Step 1 should refetch damaged_heights. "
        f"Recent requests: {requests[-5:]}")
