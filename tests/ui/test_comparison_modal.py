"""Playwright tests for the BKAFI comparison modal.

These exercise the demo.js bucket that will become `bkafi-comparison.js`
(HIGH-risk module, ~1000 LoC — includes carousel, geometry toggle, and
two three.js viewers). Tests drive the modal via
`window.openBkafiComparisonWindow(buildingId, pairs)` rather than the
Cesium click + 'View Pairs Visually' button chain.
"""
import pytest

from .helpers import (
    COMPARISON_PAIRS,
    COMPARISON_VIEWER_CAND,
    COMPARISON_WINDOW,
    GEOM_TOGGLE,
    click_through_steps,
    open_comparison_window,
)

pytestmark = pytest.mark.ui


_KNOWN_CAND_ID = '0518100000209206'


def test_comparison_modal_opens(demo_page):
    """The modal becomes visible when openBkafiComparisonWindow is called."""
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    assert demo_page.locator(COMPARISON_WINDOW).is_visible()


def test_comparison_modal_has_both_viewer_containers(demo_page):
    """Modal has the candidate (left) + pairs carousel (right) divs."""
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    assert demo_page.locator(COMPARISON_VIEWER_CAND).is_visible()
    assert demo_page.locator(COMPARISON_PAIRS).is_visible()


def test_comparison_modal_close_button_hides_it(demo_page):
    """Clicking × hides the modal."""
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    demo_page.locator(f'{COMPARISON_WINDOW} .close-btn').first.click()
    demo_page.wait_for_function(
        """() => {
            const w = document.getElementById('bkafi-comparison-window');
            return !w || w.style.display === 'none' || w.offsetParent === null;
        }""",
        timeout=3_000,
    )


def test_comparison_modal_opens_again_after_close(demo_page):
    """Re-opening the modal after close works (no carousel state leakage
    or stuck event listeners — a common refactor hazard)."""
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    demo_page.locator(f'{COMPARISON_WINDOW} .close-btn').first.click()
    demo_page.wait_for_timeout(300)
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    assert demo_page.locator(COMPARISON_WINDOW).is_visible()


def test_geometry_toggle_visible_after_step4(demo_page):
    """Addendum 7: the Pristine ↔ Post-disaster toggle row sits above
    the candidate viewer and only renders after Step 4."""
    click_through_steps(demo_page, up_to=4)
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    # Allow the carousel to hydrate.
    demo_page.wait_for_timeout(2000)
    assert demo_page.locator(GEOM_TOGGLE).is_visible()


def test_geometry_toggle_to_post_disaster_fires_correct_api(demo_page):
    """Clicking 'Post-disaster' on the toggle should fire the cand-cityjson
    route with stage=post_disaster — a regression here would silently load
    the wrong geometry into the left viewer."""
    click_through_steps(demo_page, up_to=4)
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    demo_page.wait_for_selector(GEOM_TOGGLE, state='visible', timeout=5_000)

    requests = []
    demo_page.on('request', lambda r: requests.append(r.url))

    # Click the post-disaster button (idempotent — clicking the already-
    # active button is a no-op per the JS, so toggle to pristine first if
    # needed). The default state when Step 4 done is post-disaster, so
    # click pristine first to switch away, then back.
    demo_page.locator(f'{GEOM_TOGGLE} .cand-geom-btn[data-variant="pristine"]').click()
    demo_page.wait_for_timeout(1500)
    demo_page.locator(f'{GEOM_TOGGLE} .cand-geom-btn[data-variant="post_disaster"]').click()
    demo_page.wait_for_timeout(1500)

    post_disaster_hits = [u for u in requests if 'stage=post_disaster' in u]
    assert post_disaster_hits, (
        f"Post-disaster toggle should fire /api/alignment/cand/<id>/cityjson?stage=post_disaster. "
        f"Recent requests: {requests[-8:]}")


def test_geometry_toggle_to_pristine_fires_correct_api(demo_page):
    """Clicking 'Pristine' fires the legacy /api/building/single/<id> route
    (pristine geometry comes from there, not from the cache dir)."""
    click_through_steps(demo_page, up_to=4)
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    demo_page.wait_for_selector(GEOM_TOGGLE, state='visible', timeout=5_000)

    requests = []
    demo_page.on('request', lambda r: requests.append(r.url))

    demo_page.locator(f'{GEOM_TOGGLE} .cand-geom-btn[data-variant="pristine"]').click()
    demo_page.wait_for_timeout(1500)

    pristine_hits = [u for u in requests if '/api/building/single/' in u]
    assert pristine_hits, (
        f"Pristine toggle should fire /api/building/single/<id>. "
        f"Recent requests: {requests[-8:]}")


def test_comparison_modal_no_geometry_toggle_before_step4(demo_page):
    """Before Step 4, the geometry toggle should NOT exist — pristine is
    the only available geometry. (Addendum 7: the toggle only renders when
    pipelineState.step4Completed is true.)"""
    open_comparison_window(demo_page, _KNOWN_CAND_ID)
    demo_page.wait_for_timeout(1000)
    # The toggle div is created dynamically; absent or hidden is fine.
    visible = demo_page.evaluate(
        """() => {
            const el = document.getElementById('cand-geom-toggle');
            return !!(el && el.offsetParent !== null);
        }"""
    )
    assert not visible, "Geometry toggle should not show before Step 4"
