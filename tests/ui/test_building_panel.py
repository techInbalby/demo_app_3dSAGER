"""Playwright tests for the building-properties window.

These exercise the demo.js bucket that will become `building-panel.js`
(HIGH-risk module, ~700 LoC). The panel is driven via
`window.showBuildingProperties(buildingId, ...)` rather than Cesium clicks —
canvas pick coordinates are camera-dependent and unreliable in headless.
"""
import pytest

from .helpers import (
    BUILDING_ALIGNMENT_CALLOUT,
    BUILDING_PROPERTIES_WINDOW,
    BUILDING_PROPS_ID,
    PROPERTIES_LIST,
    activate_source_a,
    click_through_steps,
    first_cand_id_from_warm_data,
    open_building_properties,
    step_button,
)

pytestmark = pytest.mark.ui


# A cand we know exists in the warm cache. Found via /api/alignment/matches/by_cand
# in earlier sessions; if the cache_dir changes it'll still be valid since the
# Source A file is locked. Used as a deterministic id for tests that don't
# care about the data — only that the panel reacts to the open call.
_KNOWN_CAND_ID = '0518100000209206'


def test_properties_window_opens(demo_page):
    """Programmatic call to showBuildingProperties() makes the panel visible."""
    open_building_properties(demo_page, _KNOWN_CAND_ID)
    assert demo_page.locator(BUILDING_PROPERTIES_WINDOW).is_visible()


def test_properties_window_shows_building_id(demo_page):
    """The panel displays the building id that was opened."""
    open_building_properties(demo_page, _KNOWN_CAND_ID)
    id_el = demo_page.locator(BUILDING_PROPS_ID)
    assert id_el.is_visible()
    text = (id_el.text_content() or '').strip()
    assert _KNOWN_CAND_ID in text, f"Expected '{_KNOWN_CAND_ID}' in props panel id text, got: {text!r}"


def test_properties_window_close_button_hides_it(demo_page):
    """Clicking × hides the panel."""
    open_building_properties(demo_page, _KNOWN_CAND_ID)
    demo_page.locator(f'{BUILDING_PROPERTIES_WINDOW} .close-btn').first.click()
    demo_page.wait_for_function(
        """() => {
            const w = document.getElementById('building-properties-window');
            return !w || w.style.display === 'none' || w.offsetParent === null;
        }""",
        timeout=3_000,
    )


def test_properties_window_features_section_exists_before_step1(demo_page):
    """Before Step 1: the properties-list div exists (the JS will populate
    it after click-to-calc) but has no real feature rows yet."""
    activate_source_a(demo_page)
    open_building_properties(demo_page, _KNOWN_CAND_ID)
    # properties-list element present
    pl = demo_page.locator(PROPERTIES_LIST)
    assert pl.count() == 1


def test_properties_window_features_after_step1(demo_page):
    """After Step 1, opening the panel populates features. The 'properties-list'
    div should contain >0 child elements once the features fetch settles."""
    click_through_steps(demo_page, up_to=1)
    open_building_properties(demo_page, _KNOWN_CAND_ID)
    # Allow the per-building feature fetch to resolve.
    demo_page.wait_for_timeout(2000)
    child_count = demo_page.evaluate(
        """() => {
            const el = document.getElementById('properties-list');
            return el ? el.children.length : 0;
        }"""
    )
    assert child_count > 0, "properties-list should be populated after Step 1"


def test_properties_window_alignment_callout_after_step4(demo_page):
    """After Step 4 the building-alignment callout div is populated (green /
    red / purple / grey variant), per addendum 5b."""
    click_through_steps(demo_page, up_to=4)
    # Use the known cand id directly. /api/alignment/matches/by_cand
    # currently emits literal NaN in some score fields (invalid JSON), so
    # the discovery helper isn't reliable.
    open_building_properties(demo_page, _KNOWN_CAND_ID)
    # The callout populates async (fetch /api/alignment/cand/<id>).
    demo_page.wait_for_timeout(2000)
    visible = demo_page.evaluate(
        """() => {
            const el = document.getElementById('building-alignment-callout');
            if (!el) return false;
            return el.style.display !== 'none' && el.innerHTML.trim().length > 0;
        }"""
    )
    assert visible, "building-alignment-callout should be visible + populated after Step 4"
