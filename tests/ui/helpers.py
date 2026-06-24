"""Shared selectors + helpers for the Playwright UI test suite.

Keeps the per-test files focused on assertions; the messy DOM/JS plumbing
(which checkbox to click, how long to wait, what to fire from the console)
lives here so a change in the frontend touches one place.

Selectors are deliberately ID-based when possible — class-based selectors
break when CSS gets reorganised, which is exactly the kind of refactor we
want NOT to spook the tests over.
"""
from typing import Optional

# ─── Selectors ──────────────────────────────────────────────────────────────

LAYERS_PANEL = '#viewer-legend-items'
SOURCE_A_CHECKBOX = '#viewer-legend-items input[data-source="A"]'
SOURCE_B_CHECKBOX = '#viewer-legend-items input[data-source="B"]'
ACTIVE_LAYER_ROW = '#viewer-legend-items .legend-row-active'

BUILDING_PROPERTIES_WINDOW = '#building-properties-window'
BUILDING_PROPS_ID = '#building-props-id'
PROPERTIES_LIST = '#properties-list'
BUILDING_ALIGNMENT_CALLOUT = '#building-alignment-callout'
CALC_FEATURES_BTN = '#calc-features-btn'
RUN_BKAFI_BTN = '#run-bkafi-btn'

COMPARISON_WINDOW = '#bkafi-comparison-window'
COMPARISON_VIEWER_CAND = '#comparison-viewer-candidate'
COMPARISON_PAIRS = '#comparison-pairs-viewers'
GEOM_TOGGLE = '#cand-geom-toggle'

TUTORIAL_GUIDE = '#tutorial-guide'

CFG_BLOCKING_K = '#cfg-blocking-k'
CFG_ALIGN_CUTOFF = '#cfg-align-cutoff'


def step_button(n: int) -> str:
    """`#step-btn-1`, `#step-btn-2`, …"""
    return f'#step-btn-{n}'


# ─── Waits / actions ────────────────────────────────────────────────────────

def activate_source_a(page) -> None:
    """Tick the Source A checkbox in the visible legend panel and wait until
    Step 1 becomes enabled. Step 1 enables when setActiveLayer runs with a
    Source A path (demo.js:1222), which fires after the toggle."""
    a_checkbox = page.locator(SOURCE_A_CHECKBOX).first
    if not a_checkbox.is_checked():
        a_checkbox.click()
    page.wait_for_function(
        "() => { const b = document.getElementById('step-btn-1'); return b && !b.disabled; }",
        timeout=20_000,
    )


def wait_for_step_completed(page, step_num: int, timeout_ms: int = 60_000) -> None:
    """Block until `#step-btn-N` text contains 'completed'."""
    page.wait_for_function(
        f"() => document.getElementById('step-btn-{step_num}')"
        f".textContent.toLowerCase().includes('completed')",
        timeout=timeout_ms,
    )


def wait_for_step_enabled(page, step_num: int, timeout_ms: int = 30_000) -> None:
    """Block until `#step-btn-N` is enabled (button.disabled === false)."""
    page.wait_for_function(
        f"() => {{ const b = document.getElementById('step-btn-{step_num}');"
        f"        return b && !b.disabled; }}",
        timeout=timeout_ms,
    )


def click_through_steps(page, up_to: int) -> None:
    """Sequentially click Steps 1..up_to, waiting between for each to flip
    to Completed. Assumes the cache is warm (each step is a quick cache-hit).
    Activates Source A first if needed."""
    activate_source_a(page)
    for n in range(1, up_to + 1):
        btn = page.locator(step_button(n))
        if 'completed' in (btn.text_content() or '').lower():
            continue
        wait_for_step_enabled(page, n)
        btn.click()
        wait_for_step_completed(page, n)


def first_cand_id_from_warm_data(page) -> Optional[str]:
    """Fetch a known cand id from the live `/api/alignment/matches/by_cand`
    endpoint via the page's fetch (uses the same origin). Returns None when
    Step 4 hasn't run for the current cache."""
    return page.evaluate(
        """async () => {
            const r = await fetch('/api/alignment/matches/by_cand');
            if (!r.ok) return null;
            const data = await r.json();
            for (const file in data) {
                for (const cand_id in data[file]) {
                    return String(cand_id);
                }
            }
            return null;
        }"""
    )


def open_building_properties(page, building_id: str) -> None:
    """Open the building-properties window programmatically.

    Driving Cesium clicks from Playwright is unreliable (canvas pick
    coordinates are camera-dependent). The window's open path is exposed
    as `window.showBuildingProperties(buildingId, cityObject, options)`,
    which is what we drive directly. `cityObject` and `options` are
    optional in the real code path — we pass null and let the function
    fetch what it needs."""
    page.evaluate(
        """(bid) => {
            if (typeof window.showBuildingProperties === 'function') {
                window.showBuildingProperties(bid, null, {});
            }
        }""",
        building_id,
    )
    # Wait for the panel to actually become visible.
    page.wait_for_selector(
        f'{BUILDING_PROPERTIES_WINDOW}:visible',
        timeout=5_000,
    )


def open_comparison_window(page, building_id: str) -> None:
    """Open the BKAFI comparison modal programmatically. Same rationale as
    open_building_properties — we drive the public API instead of clicking
    through Cesium + the building-properties panel."""
    # The window's opener accepts (buildingId, pairs); for our test we
    # don't need real pairs since the carousel will hydrate them itself
    # via the alignment API when Step 4 is done.
    page.evaluate(
        """(bid) => {
            if (typeof window.openBkafiComparisonWindow === 'function') {
                window.openBkafiComparisonWindow(bid, []);
            }
        }""",
        building_id,
    )
    page.wait_for_selector(
        f'{COMPARISON_WINDOW}:visible',
        timeout=5_000,
    )
