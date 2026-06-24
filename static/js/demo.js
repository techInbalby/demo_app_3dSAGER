// 3dSAGER Demo JavaScript
let currentSource = 'A';
let currentSessionId = null;
let locationMap = null;
let selectedFile = null; // Store selected file path
let selectedBuildingId = null; // Store selected building ID
let selectedBuildingData = null; // Store selected building data
let featuresLoaded = false; // Track if features have been calculated for current file
let bkafiLoaded = false; // Track if BKAFI results have been loaded
let buildingFeaturesCache = {}; // Cache features for all buildings
let buildingBkafiCache = {}; // Cache BKAFI pairs for buildings
let buildingStatusCache = null; // Cache building status to avoid repeated API calls
let _cachedOptimizedStatusMap = null; // Cached result of buildOptimizedStatusMap
let _colorUpdateTicket = null; // Concurrency guard for color update calls
let pipelineState = {
    step1Completed: false, // Geometric Featurization
    step2Completed: false, // BKAFI Blocking
    step3Completed: false, // Matching Classifier (pre-alignment, geometric score)
    step4Completed: false  // Spatial Alignment (post-alignment, final score)
};

let layerState = {};
// Expose layerState on window so cesium-cityjson-viewer.js can read dimmed/hidden flags
window.layerState = layerState;

// All files returned from /api/data/files — used by the legend to show unloaded files too
let allAvailableFiles = { A: [], B: [] };

function getSelectedSummaryFiles() {
    // Only Candidates (Source A) files have classifier metrics
    const visibleA = Object.entries(layerState)
        .filter(([, state]) => state.visible && state.source === 'A')
        .map(([filePath]) => filePath);
    if (visibleA.length > 0) {
        return visibleA;
    }
    if (selectedFile) {
        return [selectedFile];
    }
    return [];
}

// ─── Tutorial system ──────────────────────────────────────────────────────────
let tutorialState = {
    currentStep: 0,
    completed: false,
    fileLoaded: false,
    buildingClicked: false,
    pairsOpened: false,
    featuresCalculated: false,
    bkafiRun: false,
    classifierRun: false,
    demoRunning: false   // true while "Run for me" is in progress
};

// ── Tutorial step definitions ─────────────────────────────────────────────────
// highlight   = CSS selector for the spotlight backdrop (section-level)
// beaconSelector = CSS selector for the pulsing beacon ring (button-level)
// waitForAction  = key in tutorialState that must be true before auto-advancing
// autoDemo    = function called by "Run for me" button
const tutorialSteps = [
    {
        title: "How to run the demo",
        content: `
            <div class="tutorial-step-content">
                <p>The pipeline runs in <strong>four ordered stages</strong>. Click each button in the sidebar in turn — every stage uses the output of the previous one, so the buttons unlock as you go.</p>
                <ol class="tutorial-step-list">
                    <li>
                        <strong>1 — Geometric Featurization</strong>
                        <span>Compute geometric features for every candidate building (shape fingerprint).</span>
                    </li>
                    <li>
                        <strong>2 — Geometric Blocking</strong>
                        <span>Shortlist a few likely index buildings per candidate via BKAFI nearest-neighbour search.</span>
                        <span class="tutorial-step-hint">Adjust <strong>K</strong> below the button to make the pool larger (better recall) or smaller (cleaner anchors for alignment). Changing K re-runs Steps 3 + 4.</span>
                    </li>
                    <li>
                        <strong>3 — Match Classification</strong>
                        <span>Score each shortlisted pair with the trained XGBoost classifier.</span>
                    </li>
                    <li>
                        <strong>4 — GeoSpatial Alignment</strong>
                        <span>Recover the rigid transform from the high-confidence matches and re-block spatially. Headline P / R / F1 appear in the sidebar after this stage.</span>
                        <span class="tutorial-step-hint">Adjust the <strong>cutoff distance</strong> below the button — only nearest matches within this distance count as confirmed. Tightening the cutoff trades recall for precision.</span>
                    </li>
                </ol>
                <p class="tutorial-hint">Buildings recolour after each stage; the legend on the right of the viewer updates to show what each colour means.</p>
            </div>
        `,
        highlight: null,
        beaconSelector: null,
        waitForAction: false,
        autoDemo: null,
    },
];

// Mobile panel state
let mobilePanelState = {
    open: false,
    sectionId: null,
    sectionEl: null,
    originalParent: null,
    originalNextSibling: null
};

function isMobileView() {
    return window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
}

function initMobilePanelControls() {
    const buttons = document.querySelectorAll('.mobile-action-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-target');
            const title = btn.getAttribute('data-title') || 'Panel';
            openMobilePanel(targetId, title);
        });
    });

    const overlay = document.getElementById('mobile-panel-overlay');
    if (overlay) {
        overlay.addEventListener('click', closeMobilePanel);
    }

    window.addEventListener('resize', () => {
        if (!isMobileView() && mobilePanelState.open) {
            closeMobilePanel();
        }
    });
}

function openMobilePanel(sectionId, title) {
    if (!isMobileView()) {
        return;
    }

    const panel = document.getElementById('mobile-panel');
    const overlay = document.getElementById('mobile-panel-overlay');
    const panelBody = document.getElementById('mobile-panel-body');
    const panelTitle = document.getElementById('mobile-panel-title');
    const sectionEl = document.getElementById(sectionId);

    if (!panel || !overlay || !panelBody || !sectionEl) {
        return;
    }

    if (mobilePanelState.open) {
        closeMobilePanel();
    }

    mobilePanelState.open = true;
    mobilePanelState.sectionId = sectionId;
    mobilePanelState.sectionEl = sectionEl;
    mobilePanelState.originalParent = sectionEl.parentNode;
    mobilePanelState.originalNextSibling = sectionEl.nextSibling;

    panelTitle.textContent = title;
    panelBody.appendChild(sectionEl);
    overlay.style.display = 'block';
    panel.style.display = 'flex';
    document.body.classList.add('mobile-panel-open');

    if (sectionId === 'location-section' && locationMap) {
        setTimeout(() => {
            locationMap.invalidateSize();
        }, 200);
    }

    if (sectionId === 'viewer-section' && window.viewer && window.viewer.viewer) {
        setTimeout(() => {
            if (window.viewer.viewer.resize) {
                window.viewer.viewer.resize();
            }
            if (window.viewer.viewer.scene && window.viewer.viewer.scene.requestRender) {
                window.viewer.viewer.scene.requestRender();
            }
        }, 200);
    }
}

function closeMobilePanel() {
    if (!mobilePanelState.open) {
        return;
    }

    const panel = document.getElementById('mobile-panel');
    const overlay = document.getElementById('mobile-panel-overlay');

    const { sectionEl, originalParent, originalNextSibling } = mobilePanelState;
    if (sectionEl && originalParent) {
        if (originalNextSibling && originalNextSibling.parentNode === originalParent) {
            originalParent.insertBefore(sectionEl, originalNextSibling);
        } else {
            originalParent.appendChild(sectionEl);
        }
    }

    if (overlay) {
        overlay.style.display = 'none';
    }
    if (panel) {
        panel.style.display = 'none';
    }

    document.body.classList.remove('mobile-panel-open');

    if (mobilePanelState.sectionId === 'location-section' && locationMap) {
        setTimeout(() => {
            locationMap.invalidateSize();
        }, 200);
    }

    if (mobilePanelState.sectionId === 'viewer-section' && window.viewer && window.viewer.viewer) {
        setTimeout(() => {
            if (window.viewer.viewer.resize) {
                window.viewer.viewer.resize();
            }
            if (window.viewer.viewer.scene && window.viewer.viewer.scene.requestRender) {
                window.viewer.viewer.scene.requestRender();
            }
        }, 200);
    }

    mobilePanelState = {
        open: false,
        sectionId: null,
        sectionEl: null,
        originalParent: null,
        originalNextSibling: null
    };
}

function closeMobilePanelIfOpen(sectionId) {
    if (mobilePanelState.open && mobilePanelState.sectionId === sectionId) {
        closeMobilePanel();
    }
}

// Show tutorial on first visit
function showWelcomeGuideIfNeeded() {
    const dontShowAgain = localStorage.getItem('3dSAGER_dontShowTutorial');
    if (dontShowAgain === 'true') {
        console.log('Tutorial skipped (user preference)');
        return;
    }
    
    console.log('Showing tutorial...');
    setTimeout(() => {
        showTutorial();
    }, 500);
}

// Make tutorial functions available globally for debugging
window.showTutorial = showTutorial;
window.closeTutorialGuide = closeTutorialGuide;
window.nextTutorialStep = nextTutorialStep;
window.prevTutorialStep = prevTutorialStep;
window.skipTutorial = skipTutorial;
window.runDemoStep = runDemoStep;
// ── showTutorial — opens the modal and renders the single overview step. ─────
function showTutorial() {
    const tutorialGuide = document.getElementById('tutorial-guide');
    if (!tutorialGuide) return;
    tutorialGuide.style.display = 'flex';
    tutorialGuide.style.opacity = '';
    tutorialGuide.style.pointerEvents = '';
    document.body.classList.add('tutorial-open');
    tutorialState.currentStep = 0;
    updateTutorialStep();
}

// ── updateTutorialStep — renders the single overview step + hides the legacy
// multi-step controls (progress bar, prev / demo / skip). The Next button is
// kept and relabeled "Got it" — nextTutorialStep already calls
// closeTutorialGuide() when there is no further step. ──────────────────────
function updateTutorialStep() {
    const step = tutorialSteps[0];
    if (!step) return;

    const titleEl    = document.getElementById('tutorial-title');
    const contentEl  = document.getElementById('tutorial-step-content');
    const nextBtn    = document.getElementById('tutorial-next-btn');
    const prevBtn    = document.getElementById('tutorial-prev-btn');
    const skipBtn    = document.getElementById('tutorial-skip-btn');
    const demoBtn    = document.getElementById('tutorial-demo-btn');
    const progress   = document.querySelector('.tutorial-progress');

    if (titleEl)   titleEl.textContent = step.title;
    if (contentEl) contentEl.innerHTML = step.content;
    if (prevBtn)   prevBtn.style.display = 'none';
    if (demoBtn)   demoBtn.style.display = 'none';
    if (skipBtn)   skipBtn.style.display = 'none';
    if (progress)  progress.style.display = 'none';
    if (nextBtn) {
        nextBtn.style.display = 'inline-block';
        nextBtn.disabled = false;
        nextBtn.textContent = 'Got it';
    }
}


// ── checkTutorialAction ───────────────────────────────────────────────────────
function checkTutorialAction(action) {
    switch (action) {
        case 'fileLoaded':        return tutorialState.fileLoaded;
        case 'buildingClicked':   return tutorialState.buildingClicked;
        case 'pairsOpened':       return tutorialState.pairsOpened;
        case 'calculateFeatures': return tutorialState.featuresCalculated;
        case 'runBKAFI':          return tutorialState.bkafiRun;
        case 'viewResults':       return tutorialState.classifierRun;
        default:                  return true;
    }
}

// ── startActionWatcher ────────────────────────────────────────────────────────
// Single, centralised mechanism: polls every 400 ms to detect when the current
// step's waitForAction flag becomes true, then advances the tutorial.
// Replaces the scattered setTimeout(restoreAndAdvanceTutorial) calls that were
// spread across toggleLayer / selectFile / advanceTutorialForPipelineAction.
let _actionWatcher = null;

function startActionWatcher(stepIndex) {
    if (_actionWatcher) { clearInterval(_actionWatcher); _actionWatcher = null; }

    const step = tutorialSteps[stepIndex];
    if (!step || !step.waitForAction) return;

    // Reset the flag so a previously-completed action from an earlier step
    // doesn't fire the watcher immediately (e.g. buildingClicked stays true
    // from step 8 and would otherwise instantly advance step 16).
    if (step.waitForAction === 'buildingClicked') tutorialState.buildingClicked = false;

    _actionWatcher = setInterval(() => {
        // Stop if the user navigated away from this step
        if (tutorialState.currentStep !== stepIndex || tutorialState.completed) {
            clearInterval(_actionWatcher); _actionWatcher = null;
            return;
        }
        if (checkTutorialAction(step.waitForAction)) {
            clearInterval(_actionWatcher); _actionWatcher = null;
            // Honour per-step advanceDelay (default 800 ms) so the user sees the action
            // before the panel moves on (e.g. pairsOpened uses a longer delay).
            const delay = step.advanceDelay ?? 800;
            setTimeout(() => {
                if (tutorialState.currentStep === stepIndex) {
                    const guide = document.querySelector('.tutorial-guide-content');
                    if (guide) { guide.style.opacity = ''; guide.style.pointerEvents = ''; }
                    tutorialState.demoRunning = false;
                    nextTutorialStep();
                }
            }, 800);
        }
    }, 400);
}

function stopActionWatcher() {
    if (_actionWatcher) { clearInterval(_actionWatcher); _actionWatcher = null; }
}

// ── scrollSidebarToElement ────────────────────────────────────────────────────
function scrollSidebarToElement(selector) {
    const sidebar = document.querySelector('.sidebar');
    const element = document.querySelector(selector);
    if (!sidebar || !element || !sidebar.contains(element)) return;

    const elementTop    = element.offsetTop;
    const sidebarHeight = sidebar.clientHeight;
    const elementHeight = element.offsetHeight;
    const target        = elementTop - (sidebarHeight / 2) + (elementHeight / 2);

    sidebar.scrollTop = Math.max(0, target);
}

// ── highlightTutorialElement — spotlight backdrop ─────────────────────────────
// selector can be a single CSS string OR an array of CSS strings.
// Computes a union bounding rect over all matched elements so a single
// #tutorial-highlight div covers all targets — no extra DOM elements needed.
let _highlightTimer = null; // stores rAF id so hideHighlight can cancel it

function highlightTutorialElement(selector) {
    document.querySelectorAll('.tutorial-highlight-extra').forEach(el => el.remove());

    const highlight = document.getElementById('tutorial-highlight');
    if (!highlight || !selector) {
        if (highlight) highlight.style.display = 'none';
        return;
    }

    const selectors = Array.isArray(selector) ? selector : [selector];
    const rects = selectors
        .map(s => document.querySelector(s))
        .filter(Boolean)
        .map(el => el.getBoundingClientRect())
        .filter(r => r.width > 0 && r.height > 0);

    if (rects.length === 0) { highlight.style.display = 'none'; return; }

    if (_highlightTimer) cancelAnimationFrame(_highlightTimer);
    _highlightTimer = requestAnimationFrame(() => {
        _highlightTimer = requestAnimationFrame(() => {
            _highlightTimer = null;
            const top    = Math.min(...rects.map(r => r.top));
            const left   = Math.min(...rects.map(r => r.left));
            const right  = Math.max(...rects.map(r => r.right));
            const bottom = Math.max(...rects.map(r => r.bottom));
            highlight.style.display  = 'block';
            highlight.style.position = 'fixed';
            highlight.style.top    = (top    - 6) + 'px';
            highlight.style.left   = (left   - 6) + 'px';
            highlight.style.width  = (right  - left + 12) + 'px';
            highlight.style.height = (bottom - top  + 12) + 'px';
        });
    });
}

function hideHighlight() {
    if (_highlightTimer) { cancelAnimationFrame(_highlightTimer); _highlightTimer = null; }
    const highlight = document.getElementById('tutorial-highlight');
    if (highlight) highlight.style.display = 'none';
    document.querySelectorAll('.tutorial-highlight-extra').forEach(el => el.remove());
}

// ── positionBeacon — pulsing ring on the target button ────────────────────────
let _beaconTimer = null; // track pending show-timer so hideBeacon can cancel it

function positionBeacon(selector, labelText) {
    const beacon  = document.getElementById('tutorial-beacon');
    const labelEl = document.getElementById('tutorial-beacon-label');
    if (!beacon) return;

    const element = document.querySelector(selector);
    if (!element) { hideBeacon(); return; }

    if (labelEl) labelEl.textContent = labelText || 'Click here';

    // Scroll the element into view inside any scrollable parent (e.g. properties window)
    const findScrollParent = (el) => {
        let node = el.parentElement;
        while (node && node !== document.body) {
            if (node.scrollHeight > node.clientHeight + 2 &&
                ['auto', 'scroll'].includes(getComputedStyle(node).overflowY)) return node;
            node = node.parentElement;
        }
        return null;
    };
    const sp = findScrollParent(element);
    if (sp) {
        const elOffsetTop = element.getBoundingClientRect().top - sp.getBoundingClientRect().top + sp.scrollTop;
        sp.scrollTo({ top: elOffsetTop - sp.clientHeight / 2 + element.offsetHeight / 2, behavior: 'smooth' });
    } else {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    // Re-read position after scroll settles — store timer so hideBeacon can cancel it
    if (_beaconTimer) clearTimeout(_beaconTimer);
    _beaconTimer = setTimeout(() => {
        _beaconTimer = null;
        const rect = element.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) { hideBeacon(); return; }
        beacon.style.display  = 'block';
        beacon.style.position = 'fixed';
        beacon.style.top      = (rect.top  - 4) + 'px';
        beacon.style.left     = (rect.left - 4) + 'px';
        beacon.style.width    = (rect.width  + 8) + 'px';
        beacon.style.height   = (rect.height + 8) + 'px';
        beacon.style.zIndex   = '10002';
        beacon.style.pointerEvents = 'none';
    }, 650);
}

// ── hideBeacon ────────────────────────────────────────────────────────────────
function hideBeacon() {
    // Cancel any pending show-timer first so it can't re-show the beacon
    if (_beaconTimer) { clearTimeout(_beaconTimer); _beaconTimer = null; }
    const beacon = document.getElementById('tutorial-beacon');
    if (beacon) beacon.style.display = 'none';
    // Also remove any multi-beacons
    document.querySelectorAll('.tutorial-beacon-multi').forEach(el => el.remove());
}

// ── positionBeaconMulti — show pulsing rings on multiple targets ──────────────
function positionBeaconMulti(cssSelector, labelText) {
    // Remove any existing multi-beacons
    document.querySelectorAll('.tutorial-beacon-multi').forEach(el => el.remove());
    // Also hide the single beacon so they don't overlap
    hideBeacon();

    const targets = Array.from(document.querySelectorAll(cssSelector));
    targets.forEach((element, i) => {
        const wrapper = document.createElement('div');
        wrapper.className = 'tutorial-beacon-multi';
        wrapper.style.cssText = 'position:fixed;pointer-events:none;z-index:10003;border-radius:8px;display:none;';

        const ring = document.createElement('div');
        ring.className = 'tutorial-beacon-ring';
        wrapper.appendChild(ring);

        // Only show label on the first beacon to avoid clutter
        if (i === 0) {
            const label = document.createElement('div');
            label.className = 'tutorial-beacon-label';
            label.textContent = labelText || 'Try dimming';
            wrapper.appendChild(label);
        }

        document.body.appendChild(wrapper);

        // Position after scroll settles
        setTimeout(() => {
            const rect = element.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return;
            wrapper.style.top    = (rect.top  - 4) + 'px';
            wrapper.style.left   = (rect.left - 4) + 'px';
            wrapper.style.width  = (rect.width  + 8) + 'px';
            wrapper.style.height = (rect.height + 8) + 'px';
            wrapper.style.display = 'block';
        }, 700);
    });
}

// ── runDemoStep — called by "▶ Run for me" button ─────────────────────────────
function runDemoStep() {
    const step = tutorialSteps[tutorialState.currentStep];
    if (!step || !step.autoDemo || tutorialState.demoRunning) return;

    tutorialState.demoRunning = true;

    // Dim the tutorial panel so the user can watch the action in the sidebar/viewer
    const guide = document.querySelector('.tutorial-guide-content');
    if (guide) {
        guide.style.transition = 'opacity 0.3s';
        guide.style.opacity    = '0.25';
        guide.style.pointerEvents = 'none';
    }

    // Flash the beacon to simulate a "click" on the target
    const beacon = document.getElementById('tutorial-beacon');
    if (beacon) {
        beacon.style.background = 'rgba(102,126,234,0.25)';
        setTimeout(() => { beacon.style.background = ''; }, 400);
    }

    // Execute the action
    try {
        step.autoDemo();
    } catch (e) {
        console.warn('Tutorial autoDemo error:', e);
    }

    // Restore tutorial panel after 3 s (pipeline step will auto-advance via advanceTutorialForPipelineAction)
    setTimeout(() => {
        tutorialState.demoRunning = false;
        if (guide) {
            guide.style.opacity = '';
            guide.style.pointerEvents = '';
        }
    }, 3000);
}

// ── advanceTutorialForPipelineAction ──────────────────────────────────────────
// Step layout:
//   0  Welcome
//   1  Load Candidates layer   (fileLoaded — handled in selectFile)
//   2  Pipeline overview
//   3  Featurization           (calculateFeatures)
//   4  BKAFI                   (runBKAFI)
//   5  Classifier              (viewResults)
//   6  Zoom + marker on example building
//   7  Load Index layer + dim
//   8  Click building          (buildingClicked — handled in showBuildingProperties)
//   9  Building Properties: Stage 1 features
//  10  Building Properties: Stage 2 pairs  (pairsOpened — handled in openBkafiComparisonWindow)
//  11  Comparison view explanation
//  12  Reveal Model's Answer
//  13  Show Matches on Map
//  14  True match result
//  15  Full classification map
//  16  False positive example — fly + arrows
//  17  False positive — open building properties
//  18  False positive pairs view
//  19  Summary
function advanceTutorialForPipelineAction(actionType) {
    if (tutorialState.completed) return;

    const actionStepMap = {
        'fileLoaded':        1,
        'calculateFeatures': 3,
        'runBKAFI':          4,
        'viewResults':       5
    };

    if (actionType === 'fileLoaded')        tutorialState.fileLoaded         = true;
    if (actionType === 'calculateFeatures') tutorialState.featuresCalculated = true;
    if (actionType === 'runBKAFI')          tutorialState.bkafiRun           = true;
    if (actionType === 'viewResults')       tutorialState.classifierRun      = true;
    // State flags are now set above; startActionWatcher (via updateTutorialStep) is the
    // single mechanism that detects the flag and calls nextTutorialStep().
}

// ── nextTutorialStep ──────────────────────────────────────────────────────────
function nextTutorialStep() {
    hideBeacon();
    if (tutorialState.currentStep < tutorialSteps.length - 1) {
        tutorialState.currentStep++;
        localStorage.setItem('3dSAGER_tutorialStep', tutorialState.currentStep);
        updateTutorialStep();
    } else {
        closeTutorialGuide();
    }
}

// ── prevTutorialStep ──────────────────────────────────────────────────────────
function prevTutorialStep() {
    hideBeacon();
    if (tutorialState.currentStep > 0) {
        tutorialState.currentStep--;
        localStorage.setItem('3dSAGER_tutorialStep', tutorialState.currentStep);
        updateTutorialStep();
    }
}

// ── skipTutorial ──────────────────────────────────────────────────────────────
function skipTutorial() {
    if (confirm('Skip the tutorial? You can always reopen it with the Tutorial button.')) {
        localStorage.setItem('3dSAGER_dontShowTutorial', 'true');
        localStorage.removeItem('3dSAGER_tutorialStep');
        closeTutorialGuide();
    }
}

// ── hideTutorialForNow — X button hides panel, saves step for resuming ────────
function hideTutorialForNow() {
    // Persist current step so reopening offers a "Continue" option
    if (tutorialState.currentStep > 0) {
        localStorage.setItem('3dSAGER_tutorialStep', tutorialState.currentStep);
    }
    stopActionWatcher();
    const tutorialGuide = document.getElementById('tutorial-guide');
    if (tutorialGuide) tutorialGuide.style.display = 'none';
    hideHighlight();
    hideBeacon();
    removeTutorialMarker();
    if (window.viewer && window.viewer.clearBuildingMarkers) window.viewer.clearBuildingMarkers();
    window.tutorialSuppressAutoFly = false;
    document.body.classList.remove('tutorial-open');
}

// ── restoreAndAdvanceTutorial — restores opacity then advances one step ───────
// Used by action handlers (fileLoaded, buildingClicked, pairsOpened) so the
// tutorial panel comes back before the next step is shown.
function restoreAndAdvanceTutorial() {
    const guide = document.querySelector('.tutorial-guide-content');
    if (guide) { guide.style.opacity = ''; guide.style.pointerEvents = ''; }
    tutorialState.demoRunning = false;
    nextTutorialStep();
}

// ── closeTutorialGuide ────────────────────────────────────────────────────────
function closeTutorialGuide() {
    stopActionWatcher();
    const tutorialGuide = document.getElementById('tutorial-guide');
    if (tutorialGuide) tutorialGuide.style.display = 'none';
    hideHighlight();
    hideBeacon();
    removeTutorialMarker();
    if (window.viewer && window.viewer.clearBuildingMarkers) window.viewer.clearBuildingMarkers();
    window.tutorialSuppressAutoFly = false;
    document.body.classList.remove('tutorial-open');
    tutorialState.completed = true;
    localStorage.removeItem('3dSAGER_tutorialStep'); // Clear saved step on full completion
}

// Initialize when page loads
document.addEventListener('DOMContentLoaded', function() {
    console.log('3dSAGER Demo initialized');
    loadDataFiles();
    updatePipelineUI(); // Initialize pipeline UI
    showWelcomeGuideIfNeeded(); // Show tutorial if needed
    setupWelcomeGuideClickHandlers(); // Setup click handlers for tutorial
    initMobilePanelControls();
});

// Setup click handlers for tutorial guide
function setupWelcomeGuideClickHandlers() {
    const tutorialGuide = document.getElementById('tutorial-guide');
    if (tutorialGuide) {
        // Don't close on overlay click - require explicit close or completion
        tutorialGuide.addEventListener('click', function(e) {
            e.stopPropagation();
        });
    }
}



// Load data files from API — render layer list but do NOT auto-load.
// Users (or the tutorial) must check the checkboxes to load layers.
function loadDataFiles() {
    fetch('/api/data/files')
        .then(response => response.json())
        .then(data => {
            console.log('Files loaded:', data);

            allAvailableFiles = {
                A: data.source_a || [],
                B: data.source_b || []
            };

            renderFileList('A', data.source_a);
            renderFileList('B', data.source_b);

            // Show the legend with all files unchecked — user loads manually
            updateViewerLegend();
        })
        .catch(error => {
            console.error('Error loading files:', error);
        });
}

// Render file list
function renderFileList(source, files) {
    const container = document.getElementById(`files${source}`);
    container.innerHTML = '';
    
    files.forEach(file => {
        const fileItem = document.createElement('div');
        fileItem.className = 'file-item';
        const isChecked = layerState[file.path]?.visible === true;
        fileItem.innerHTML = `
            <label class="file-toggle" title="${isChecked ? 'Hide layer' : 'Show layer'}">
                <input type="checkbox" data-path="${file.path}" data-source="${source}" ${isChecked ? 'checked' : ''}>
            </label>
            <div class="file-meta">
                <div class="file-name">${file.filename}</div>
                <div class="file-size">${(file.size / 1024 / 1024).toFixed(2)} MB</div>
            </div>
            <button class="zoom-layer-btn" title="Zoom to layer" ${isChecked ? '' : 'disabled'}>⌖</button>
        `;
        fileItem.querySelector('input').addEventListener('change', (event) => {
            const checked = event.target.checked;
            toggleLayer(file.path, source, checked);
            // enable/disable zoom button together with layer visibility
            const zoomBtn = fileItem.querySelector('.zoom-layer-btn');
            if (zoomBtn) zoomBtn.disabled = !checked;
        });
        fileItem.querySelector('.file-meta').addEventListener('click', () => {
            selectFile(file.path, source);
        });
        fileItem.querySelector('.zoom-layer-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            zoomToLayer(file.path);
        });
        container.appendChild(fileItem);
    });
    updateActiveFileHighlight();
}

// Show source tab
function showSource(source) {
    // Update tabs
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector(`[onclick="showSource('${source}')"]`).classList.add('active');
    
    // Update content
    document.querySelectorAll('.source-content').forEach(content => content.classList.remove('active'));
    document.getElementById(`source${source}`).classList.add('active');
    
    currentSource = source;
}

// Source colour map — must match cesium-cityjson-viewer.js getMaterialForObjectType
const SOURCE_COLORS = {
    A: 'rgb(116,151,223)',  // blue  — Candidates
    B: 'rgb(38,166,154)'    // teal  — Index
};

/**
 * Load a CityJSON file into the viewer, retrying until window.viewer is ready.
 * This handles the race between DOMContentLoaded (which fires loadDataFiles)
 * and the Cesium viewer's async initialisation (setTimeout polling for Cesium).
 */
function waitForViewerThenLoad(filePath, source, attempts) {
    attempts = attempts || 0;
    // Must wait for BOTH the viewer object AND its async init() to complete (isInitialized).
    // Without the isInitialized check, loadCityJSON returns early with an error when
    // the Cesium Ion imagery await is still in flight.
    if (window.viewer && window.viewer.isInitialized && window.viewer.loadCityJSON) {
        window.viewer.loadCityJSON(filePath, { append: true, source });
    } else if (attempts < 60) {
        // Retry up to ~18 s (60 × 300 ms) while Cesium finishes initialising
        setTimeout(() => waitForViewerThenLoad(filePath, source, attempts + 1), 300);
    } else {
        console.warn('Viewer never became ready for', filePath);
    }
}

function toggleLayer(filePath, source, shouldShow) {
    layerState[filePath] = {
        visible: shouldShow,
        source
    };

    const escapeForSelector = (value) => {
        if (window.CSS && typeof window.CSS.escape === 'function') {
            return window.CSS.escape(value);
        }
        return value.replace(/"/g, '\\"');
    };
    const checkbox = document.querySelector(`input[type="checkbox"][data-path="${escapeForSelector(filePath)}"]`);
    if (checkbox && checkbox.checked !== shouldShow) {
        checkbox.checked = shouldShow;
    }

    if (shouldShow) {
        // Use the retry helper so the load works even when viewer isn't ready yet
        waitForViewerThenLoad(filePath, source);
        // When the first Source A layer is loaded, select it as the pipeline file.
        // Call selectFile (not just setPipelineFile) so the backend is notified and the
        // tutorial fileLoaded flag gets set.  selectFile guards against re-calling toggleLayer
        // because layerState[filePath].visible is already true at this point.
        if (source === 'A' && !selectedFile) {
            selectFile(filePath, 'A');
        }
        // Advance tutorial directly — does not depend on the API callback
        if (source === 'A') {
            advanceTutorialForPipelineAction('fileLoaded');
        }
        // When the user manually loads an Index (B) layer on tutorial step 8, shift spotlight to viewer
        if (source === 'B' && tutorialState.active && tutorialState.currentStep === 7) {
            setTimeout(() => highlightTutorialElement(['#legend-col', '#viewer']), 2600);
            setTimeout(() => positionBeaconMulti('.legend-dim-btn:not([disabled])', 'Try dimming'), 3400);
        }
    } else {
        if (window.viewer && window.viewer.removeLayer) {
            window.viewer.removeLayer(filePath);
        }
        // If the pipeline file was hidden, pick another visible Source A file or clear
        if (source === 'A' && selectedFile === filePath) {
            const nextA = Object.entries(layerState).find(
                ([fp, s]) => s.source === 'A' && s.visible && fp !== filePath
            );
            setPipelineFile(nextA ? nextA[0] : null);
        }
        // Layer removed — refresh styles (remaining layer should return to full color)
        if (window.viewer && window.viewer.applyLayerVisualStyles) {
            window.viewer.applyLayerVisualStyles(selectedFile);
        }
    }

    updateActiveFileHighlight();
    updateViewerLegend();
}

/**
 * Toggle entity visibility for an already-loaded layer (eye button in legend).
 * Does NOT unload the layer — it just hides or shows the Cesium entities.
 */
function toggleLayerVisible(filePath) {
    if (!layerState[filePath]) return;
    const isCurrentlyHidden = !!layerState[filePath].hidden;
    layerState[filePath].hidden = !isCurrentlyHidden;
    if (window.viewer && window.viewer.setLayerEntityShow) {
        window.viewer.setLayerEntityShow(filePath, isCurrentlyHidden); // flip
    }
    updateViewerLegend();
}

/**
 * Toggle the "dimmed" (semi-transparent) state for a layer (opacity button in legend).
 * Dimmed layers always render as semi-transparent regardless of pipeline selection.
 */
function toggleLayerDimmed(filePath) {
    if (!layerState[filePath]) return;
    layerState[filePath].dimmed = !layerState[filePath].dimmed;
    if (window.viewer && window.viewer.applyLayerVisualStyles) {
        window.viewer.applyLayerVisualStyles(selectedFile);
    }
    updateViewerLegend();
}

// Expose to inline onclick handlers in the legend
window.toggleLayerVisible = toggleLayerVisible;
window.toggleLayerDimmed = toggleLayerDimmed;

function setPipelineFile(filePath) {
    selectedFile = filePath;
    buildingStatusCache = null;
    _cachedOptimizedStatusMap = null;
    const btn = document.getElementById('step-btn-1');
    if (btn) btn.disabled = !filePath;
    if (!filePath) {
        resetPipelineState();
        updatePipelineUI();
    }
    // Update the active-file banner in the pipeline section
    const banner = document.getElementById('pipeline-active-file');
    if (banner) {
        if (filePath) {
            const name = filePath.split('/').pop();
            banner.innerHTML = `<span class="pipeline-active-dot"></span><span class="pipeline-active-text">Running on: <strong>${name}</strong></span>`;
            banner.style.display = 'flex';
        } else {
            banner.style.display = 'none';
        }
    }
    updateActiveFileHighlight();
    // Refresh visual styles: selected layer → full fill; others → outline/contour only
    if (window.viewer && window.viewer.applyLayerVisualStyles) {
        window.viewer.applyLayerVisualStyles(selectedFile);
    }
    // Also re-run pipeline stage colors if a stage has already been completed.
    // Step 4 (alignment) wins over Step 3 if both are done — Step 4 owns the
    // final post-alignment coloring.
    if (window.viewer && selectedFile) {
        if (pipelineState.step4Completed && window.AlignmentStep && typeof window.AlignmentStep.reapplyColors === 'function') {
            window.AlignmentStep.reapplyColors();
        } else if (pipelineState.step3Completed) updateBuildingColorsForStage3(true);
        else if (pipelineState.step2Completed) updateBuildingColorsForStage2(true);
        else if (pipelineState.step1Completed) updateBuildingColorsForStage1(true);
    }
}

function updateActiveFileHighlight() {
    // Mark the active pipeline file in the file list
    document.querySelectorAll('.file-item').forEach(item => {
        const cb = item.querySelector('input[type="checkbox"]');
        if (!cb) return;
        const fp = cb.getAttribute('data-path');
        item.classList.toggle('file-item--active', fp === selectedFile);
    });
}

function updateViewerLegend() {
    const colEl    = document.getElementById('legend-col');
    const itemsEl  = document.getElementById('viewer-legend-items');
    if (!colEl || !itemsEl) return;

    const allA = allAvailableFiles.A || [];
    const allB = allAvailableFiles.B || [];

    if (allA.length === 0 && allB.length === 0) {
        colEl.style.display = 'none';
        return;
    }
    colEl.style.display = 'flex';

    /**
     * Each row: [checkbox] [swatch] [name] [Set Active btn (A only)] [dim◑] [zoom⌖]
     * Checkbox checked  = layer is loaded in the viewer
     * Checkbox unchecked = layer is unloaded (entities removed)
     * "Set Active" button appears for loaded Source-A layers that aren't the active pipeline layer
     */
    const buildGroup = (label, files, color, source) => {
        if (files.length === 0) return '';
        let rows = `<div class="viewer-legend-group">${label}</div>`;
        files.forEach(file => {
            const fp    = file.path;
            const name  = file.filename || fp.split('/').pop();
            const state = layerState[fp] || {};
            const loaded   = !!state.visible;
            const dimmed   = !!state.dimmed;
            const isActive = fp === selectedFile;
            const safeFp   = fp.replace(/\\/g, '\\\\').replace(/'/g, "\\'");

            const cbTitle  = loaded ? 'Unload layer from viewer' : 'Load layer into viewer';
            const dimTitle = dimmed ? 'Restore full opacity'     : 'Dim layer';

            const badge = isActive
                ? '<span class="legend-active-tag">active</span>'
                : '';

            rows += `<div class="viewer-legend-row legend-layer-row${!loaded ? ' legend-row-unloaded' : ''}${isActive ? ' legend-row-active' : ''}" title="${name}">
                <input type="checkbox" class="legend-layer-cb" data-source="${source}" ${loaded ? 'checked' : ''}
                    onchange="toggleLayer('${safeFp}','${source}',this.checked)"
                    title="${cbTitle}">
                <span class="viewer-legend-swatch" style="background:${color};opacity:${loaded ? 1 : 0.35};flex-shrink:0;"></span>
                <span class="legend-layer-name">${name}${badge}</span>
                <button class="legend-dim-btn${dimmed ? ' btn-dimmed' : ''}" title="${dimTitle}" data-source="${source}"
                    onclick="toggleLayerDimmed('${safeFp}')" ${!loaded ? 'disabled' : ''}>◑</button>
                <button class="legend-zoom-btn" title="Zoom to layer"
                    onclick="zoomToLayer('${safeFp}')" ${!loaded ? 'disabled' : ''}>⌖</button>
            </div>`;
        });
        return rows;
    };

    let html = '';
    html += buildGroup('Candidates (A)', allA, SOURCE_COLORS.A, 'A');
    html += buildGroup('Index (B)',       allB, SOURCE_COLORS.B, 'B');

    // Building-status colour key (shown when at least one layer is loaded)
    const anyALoaded = allA.some(f => layerState[f.path]?.visible);
    const anyBLoaded = allB.some(f => layerState[f.path]?.visible);
    if (anyALoaded || anyBLoaded) {
        const ps = pipelineState || {};
        html += `<div class="viewer-legend-group" style="margin-top:6px;">Building status</div>`;
        if (anyALoaded) {
            html += `<div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgb(116,151,223);"></span>Candidates (default)</div>`;
        }
        if (anyBLoaded) {
            html += `<div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgb(38,166,154);"></span>Index (default)</div>`;
        }
        if (anyALoaded) {
            html += `<div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgb(255,152,0);"></span>Has features</div>`;
        }
        if (anyALoaded && ps.step2Completed) {
            html += `<div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgb(255,235,59);"></span>Has blocking pairs</div>`;
        }
        if (anyALoaded && ps.step3Completed) {
            html += `<div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgb(76,175,80);"></span>True match</div>
            <div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgb(244,67,54);"></span>False positive</div>
            <div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgb(97,97,97);"></span>No match</div>`;
        }
        if (anyALoaded && ps.step4Completed) {
            // Step 4 adds one new color on top of Step 3's TP/FP/no-match —
            // false_negative for cands whose true match was missed post-alignment.
            html += `<div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgb(120,80,140);"></span>False negative (post-alignment)</div>`;
        }
        const loadedCount = allA.filter(f => layerState[f.path]?.visible).length
                          + allB.filter(f => layerState[f.path]?.visible).length;
        if (loadedCount > 1) {
            html += `<div class="viewer-legend-row"><span class="viewer-legend-swatch" style="background:rgba(180,180,180,0.22);border:2px solid rgb(80,80,80);"></span>Dimmed layers</div>`;
        }
    }

    itemsEl.innerHTML = html;
}

function zoomToLayer(filePath) {
    if (window.viewer && window.viewer.zoomToLayer) {
        window.viewer.zoomToLayer(filePath);
    }
}

// Legend is now a fixed panel column — minimize is handled by the column itself.
function toggleLegendMinimize() { /* no-op: legend is now a sidebar panel */ }
window.toggleLegendMinimize = toggleLegendMinimize;

// Fly the Cesium camera to a specific building by ID
function zoomToBuilding(buildingId) {
    if (!window.viewer || !window.viewer.buildingEntities) return false;
    // CityJSON ids may or may not carry the 'bag_' prefix — try both variants
    const variants = [buildingId, buildingId.replace(/^bag_/, ''), 'bag_' + buildingId.replace(/^bag_/, '')];
    for (const id of variants) {
        const entities = window.viewer.buildingEntities.get(id);
        if (entities && entities.length > 0) {
            try {
                window.viewer.viewer.flyTo(entities, {
                    duration: 1.5,
                    offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), 300)
                });
            } catch (_) { /* viewer may not be ready */ }
            return true;
        }
    }
    return false;
}

// ── Tutorial building marker (Cesium label + arrow placed above the example building) ──
let tutorialMarkerEntity = null;
let tutorialMarkerArrow  = null;

function addTutorialMarker(buildingId) {
    removeTutorialMarker();
    if (!window.viewer || !window.viewer.buildingEntities) return;
    const variants = [buildingId, buildingId.replace(/^bag_/, ''), 'bag_' + buildingId.replace(/^bag_/, '')];
    for (const id of variants) {
        const entities = window.viewer.buildingEntities.get(id);
        if (entities && entities.length > 0) {
            const entity = entities[0];
            try {
                if (entity.polygon && entity.polygon.hierarchy) {
                    const hier = entity.polygon.hierarchy.getValue
                        ? entity.polygon.hierarchy.getValue(Cesium.JulianDate.now())
                        : entity.polygon.hierarchy;
                    if (hier && hier.positions && hier.positions.length > 0) {
                        let sumX = 0, sumY = 0, sumZ = 0;
                        hier.positions.forEach(p => { sumX += p.x; sumY += p.y; sumZ += p.z; });
                        const n = hier.positions.length;
                        const center = new Cesium.Cartesian3(sumX / n, sumY / n, sumZ / n);
                        const carto = Cesium.Cartographic.fromCartesian(center);

                        const labelPos    = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, carto.height + 50);
                        const arrowTipPos = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, carto.height + 4);
                        const arrowColor  = Cesium.Color.fromCssColorString('#1a7aea');

                        tutorialMarkerEntity = window.viewer.viewer.entities.add({
                            position: labelPos,
                            label: {
                                text: 'Example building',
                                font: 'bold 13px sans-serif',
                                fillColor: Cesium.Color.WHITE,
                                outlineColor: arrowColor,
                                outlineWidth: 3,
                                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                                showBackground: true,
                                backgroundColor: arrowColor.withAlpha(0.85),
                                backgroundPadding: new Cesium.Cartesian2(10, 5),
                                pixelOffset: new Cesium.Cartesian2(0, 0),
                                disableDepthTestDistance: Number.POSITIVE_INFINITY
                            }
                        });

                        // Arrow polyline — arrowhead lands on the building roof
                        tutorialMarkerArrow = window.viewer.viewer.entities.add({
                            polyline: {
                                positions: [labelPos, arrowTipPos],
                                width: 10,
                                material: new Cesium.PolylineArrowMaterialProperty(arrowColor.withAlpha(0.9)),
                                depthFailMaterial: new Cesium.PolylineArrowMaterialProperty(arrowColor.withAlpha(0.4)),
                                clampToGround: false
                            }
                        });
                    }
                }
            } catch (e) {
                console.warn('addTutorialMarker error:', e);
            }
            return;
        }
    }
}

function removeTutorialMarker() {
    if (window.viewer && window.viewer.viewer) {
        if (tutorialMarkerEntity) {
            try { window.viewer.viewer.entities.remove(tutorialMarkerEntity); } catch (_) {}
            tutorialMarkerEntity = null;
        }
        if (tutorialMarkerArrow) {
            try { window.viewer.viewer.entities.remove(tutorialMarkerArrow); } catch (_) {}
            tutorialMarkerArrow = null;
        }
    }
}

function setActiveFileFromViewer(filePath) {
    if (!filePath) {
        return;
    }
    if (selectedFile === filePath) {
        return;
    }
    selectedFile = filePath;
    if (filePath.includes('/Source A/')) {
        currentSource = 'A';
        document.getElementById('step-btn-1').disabled = false;
    } else if (filePath.includes('/Source B/')) {
        currentSource = 'B';
    }
    if (window.viewer && window.viewer.applyLayerVisualStyles) {
        window.viewer.applyLayerVisualStyles(selectedFile);
    }
}

// Select a file
// selectFile is a function declaration so it is already accessible as window.selectFile
function selectFile(filePath, source) {
    console.log('Selecting file:', filePath, source);

    closeMobilePanelIfOpen('file-selection-section');
    
    // Allow selecting from any source (Candidates or Index)
    // Users can view files from both sources in any order
    // Reset pipeline state and store file only if from Candidates (for pipeline steps)
    if (source === 'A' && selectedFile !== filePath) {
        resetPipelineState();
        setPipelineFile(filePath);
        updatePipelineUI();
        updateViewerLegend(); // refresh active badge + "Set active" buttons
        if (window.viewer && window.viewer.applyLayerVisualStyles) {
            window.viewer.applyLayerVisualStyles(filePath);
        }
    }
    
    // Ensure layer is visible when selected
    if (!layerState[filePath]?.visible) {
        toggleLayer(filePath, source, true);
    }
    
    // Call API to select file
    fetch('/api/data/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath, source: source })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            currentSessionId = data.session_id;
            
            // Update tutorial state when file is selected (watcher handles advancement)
            if (!tutorialState.completed) {
                tutorialState.fileLoaded = true;
            }
        } else {
            console.error('Error selecting file:', data.error);
        }
    })
    .catch(error => {
        console.error('Error selecting file:', error);
    });
}

// Reset pipeline state
function resetPipelineState() {
    // Mutate in place — don't reassign. window.pipelineState holds the same
    // object reference and alignment.js / cesium viewer modules read from it;
    // reassigning to a fresh literal would orphan their reference at the
    // previous (now-stale) object.
    pipelineState.step1Completed = false;
    pipelineState.step2Completed = false;
    pipelineState.step3Completed = false;
    pipelineState.step4Completed = false;
    selectedBuildingId = null;
    selectedBuildingData = null;
    featuresLoaded = false;
    bkafiLoaded = false;
    buildingStatusCache = null;
    _cachedOptimizedStatusMap = null;
    buildingFeaturesCache = {};
    buildingBkafiCache = {};
    if (window.AlignmentStep && typeof window.AlignmentStep.reset === 'function') {
        window.AlignmentStep.reset();
    }
    
    // Reset sidebar buttons to initial state
    const stepBtn1 = document.getElementById('step-btn-1');
    const stepBtn2 = document.getElementById('step-btn-2');
    const stepBtn3 = document.getElementById('step-btn-3');
    
    if (stepBtn1) {
        stepBtn1.textContent = 'Calculate Features';
        stepBtn1.style.background = '#667eea';
        stepBtn1.disabled = false; // Enable for new file
    }
    
    if (stepBtn2) {
        stepBtn2.textContent = 'Run Blocking';
        stepBtn2.style.background = '#667eea';
        stepBtn2.disabled = true; // Disable until step 1 is completed
    }
    
    if (stepBtn3) {
        stepBtn3.textContent = 'Run Classifier';
        stepBtn3.style.background = '#667eea';
        stepBtn3.disabled = true; // Disable until step 2 is completed
    }
    
    updatePipelineUI();
}

// Initialize location map
let cityPolygon = null; // Store the city polygon layer

function initLocationMap() {
    // Wait for Leaflet to load
    if (typeof L === 'undefined') {
        setTimeout(initLocationMap, 100);
        return;
    }
    
    try {
        // Initialize Leaflet map (The Hague coordinates)
        locationMap = L.map('location-map').setView([52.0705, 4.3007], 13);
        
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 19
        }).addTo(locationMap);
        
        // Add marker for The Hague
        L.marker([52.0705, 4.3007])
            .addTo(locationMap)
            .bindPopup('The Hague, Netherlands<br>Demo Location')
            .openPopup();
        
        console.log('Location map initialized');
    } catch (error) {
        console.error('Error initializing location map:', error);
    }
}

// Update map with city bounds
function updateMapWithCityBounds(bounds) {
    if (!locationMap || !bounds) {
        console.warn('Cannot update map: locationMap or bounds missing', { locationMap: !!locationMap, bounds });
        return;
    }
    
    // Validate bounds
    if (!bounds.min || !bounds.max || 
        typeof bounds.min.lat !== 'number' || typeof bounds.min.lon !== 'number' ||
        typeof bounds.max.lat !== 'number' || typeof bounds.max.lon !== 'number') {
        console.error('Invalid bounds format:', bounds);
        return;
    }
    
    // Validate coordinate ranges (lat: -90 to 90, lon: -180 to 180)
    if (bounds.min.lat < -90 || bounds.max.lat > 90 || 
        bounds.min.lon < -180 || bounds.max.lon > 180) {
        console.error('Bounds out of valid range:', bounds);
        return;
    }
    
    // Check if bounds are reasonable (not all zeros or same point)
    if (Math.abs(bounds.max.lat - bounds.min.lat) < 0.0001 || 
        Math.abs(bounds.max.lon - bounds.min.lon) < 0.0001) {
        console.warn('Bounds too small (likely a point, not an area):', bounds);
    }
    
    try {
        // Remove existing polygon if any
        if (cityPolygon) {
            locationMap.removeLayer(cityPolygon);
            cityPolygon = null;
        }
        
        console.log('Creating polygon with bounds:', {
            min: { lat: bounds.min.lat, lon: bounds.min.lon },
            max: { lat: bounds.max.lat, lon: bounds.max.lon },
            center: bounds.center
        });
        
        // Create rectangle polygon from bounds
        const polygonBounds = [
            [bounds.min.lat, bounds.min.lon], // Southwest corner
            [bounds.min.lat, bounds.max.lon], // Southeast corner
            [bounds.max.lat, bounds.max.lon], // Northeast corner
            [bounds.max.lat, bounds.min.lon], // Northwest corner
            [bounds.min.lat, bounds.min.lon]  // Close the polygon
        ];
        
        // Create and add polygon
        cityPolygon = L.polygon(polygonBounds, {
            color: '#667eea',
            fillColor: '#667eea',
            fillOpacity: 0.3,
            weight: 2
        }).addTo(locationMap);
        
        // Fit map to show the polygon with some padding
        locationMap.fitBounds(cityPolygon.getBounds(), {
            padding: [20, 20], // Add padding around the bounds
            maxZoom: 15 // Don't zoom in too much
        });
        
        // Add popup to polygon with bounds info
        const boundsInfo = `City Model Bounds<br>
            Lat: ${bounds.min.lat.toFixed(6)} to ${bounds.max.lat.toFixed(6)}<br>
            Lon: ${bounds.min.lon.toFixed(6)} to ${bounds.max.lon.toFixed(6)}`;
        cityPolygon.bindPopup(boundsInfo).openPopup();
        
        console.log('Map updated successfully with city bounds');
    } catch (error) {
        console.error('Error updating map with city bounds:', error);
        console.error('Bounds that caused error:', bounds);
    }
}

// Map update callback disabled to improve performance
// window.onCityJSONLoaded = function(bounds) {
//     console.log('CityJSON loaded, updating map with bounds:', bounds);
//     updateMapWithCityBounds(bounds);
// };

// Load file in 3D viewer
function loadFileInViewer(filePath) {
    console.log('Loading file in viewer:', filePath);
    console.log('Viewer available:', !!window.viewer);
    console.log('Cesium available:', typeof Cesium !== 'undefined');
    
    // Wait for viewer to be ready (with retry)
    const tryLoad = (attempts = 0) => {
        if (window.viewer && window.viewer.loadCityJSON) {
            // Use the file path as-is (it should already be in the correct format from the API)
            // The path from the API is already relative to the data directory
            console.log('Using file path:', filePath);
            try {
                window.viewer.loadCityJSON(filePath);
            } catch (error) {
                console.error('Error calling loadCityJSON:', error);
                const viewer = document.getElementById('viewer');
                if (viewer) {
                    viewer.innerHTML = `
                        <div class="placeholder">
                            <div class="placeholder-icon">!</div>
                            <p>Error loading file: ${error.message}</p>
                        </div>
                    `;
                }
            }
            
            // Also try to fit camera after a delay
            setTimeout(() => {
                if (window.viewer && window.viewer.zoomToModel) {
                    window.viewer.zoomToModel();
                }
                
                // Update tutorial state when file is loaded (watcher handles advancement)
                if (!tutorialState.completed && window.viewer && window.viewer.buildingEntities && window.viewer.buildingEntities.size > 0) {
                    tutorialState.fileLoaded = true;
                }
            }, 2000);
        } else if (attempts < 20) {
            // Retry up to 20 times (2 seconds total)
            console.log(`Waiting for viewer to initialize... (attempt ${attempts + 1})`);
            setTimeout(() => tryLoad(attempts + 1), 100);
        } else {
            // Show error after retries exhausted
            console.error('Cesium viewer not available after waiting');
            const viewer = document.getElementById('viewer');
            if (viewer) {
                let errorMsg = '3D Viewer not ready. ';
                if (typeof Cesium === 'undefined') {
                    errorMsg += 'Cesium library failed to load. Check your internet connection and Cesium CDN.';
                } else {
                    errorMsg += 'Viewer initialization failed. Please refresh the page.';
                }
                viewer.innerHTML = `
                    <div class="placeholder">
                        <div class="placeholder-icon">!</div>
                        <p>${errorMsg}</p>
                        <button onclick="location.reload()" style="margin-top: 10px; padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 4px; cursor: pointer;">Refresh Page</button>
                    </div>
                `;
            }
        }
    };
    
    tryLoad();
}

// Show building properties window
// options.sources = [{ filePath, source: 'A'|'B', name }] when building exists in multiple layers
function showBuildingProperties(buildingId, cityObject, options) {
    options = options || {};
    closeMobilePanel();
    // Update tutorial state when building is clicked
    if (!tutorialState.completed) {
        tutorialState.buildingClicked = true;
    }
    selectedBuildingId = buildingId;
    selectedBuildingData = cityObject;
    
    const propsWindow      = document.getElementById('building-properties-window');
    const propsNameEl      = document.getElementById('building-props-name');
    const propsIdEl        = document.getElementById('building-props-id');
    const propsListEl      = document.getElementById('properties-list');
    const calcBtn          = document.getElementById('calc-features-btn');
    const bkafiBtn         = document.getElementById('run-bkafi-btn');

    if (!propsWindow || !propsNameEl || !propsIdEl || !propsListEl) {
        console.error('Building properties window elements not found');
        return;
    }
    
    // Show only the building ID (no name)
    propsNameEl.textContent = '';
    propsIdEl.textContent = `ID: ${buildingId}`;
    
    // Show all sources that contain this building (when more than one)
    const sourcesEl = document.getElementById('building-props-sources');
    if (sourcesEl) {
        const sources = options.sources || [];
        if (sources.length > 1) {
            const label = sources.length === 2 ? 'Sources' : 'In all sources';
            const list = sources.map(s => `${s.source === 'B' ? 'Index (B)' : 'Candidates (A)'}: ${s.name}`).join('; ');
            sourcesEl.innerHTML = `<p class="building-sources-label">${label}:</p><p class="building-sources-list" title="${sources.map(s => s.name).join(', ')}">${list}</p>`;
            sourcesEl.style.display = 'block';
        } else {
            sourcesEl.innerHTML = '';
            sourcesEl.style.display = 'none';
        }
    }
    
    // Clear properties list
    propsListEl.innerHTML = '';
    
    // Hide BKAFI button initially
    if (bkafiBtn) bkafiBtn.style.display = 'none';

    // Enable calculate features button (only if a candidates file is selected)
    // Pipeline steps only work with candidates files
    // Check if we have a selected candidates file (source A)
    const isCandidatesFile = selectedFile && currentSource === 'A';
    
    if (isCandidatesFile) {
        if (featuresLoaded) {
            // Features already calculated, load and show them
            calcBtn.disabled = true;
            calcBtn.textContent = 'Features Calculated';
            calcBtn.style.background = '#28a745';
            loadBuildingFeatures(buildingId);
            
            // Also load BKAFI pairs if BKAFI has been run
            if (bkafiLoaded) {
                loadBuildingBkafiPairs(buildingId);
            }
            // After Step 4: show the spatial-alignment callout for this cand.
            if (pipelineState && pipelineState.step4Completed) {
                loadBuildingAlignmentInfo(buildingId);
            }
        } else {
            // Features not calculated yet
            calcBtn.disabled = false;
            calcBtn.textContent = 'Calculate Geometric Features';
            calcBtn.style.background = '#667eea'; // Reset button color
        }
    } else {
        calcBtn.disabled = true;
        if (currentSource === 'B') {
            calcBtn.textContent = 'Select Candidates File for Pipeline';
        } else {
            calcBtn.textContent = 'Select Candidates File First';
        }
    }
    
    // Show the window
    propsWindow.style.display = 'block';
    
    // Add overlay
    let overlay = document.getElementById('properties-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'properties-overlay';
        overlay.className = 'properties-overlay';
        overlay.onclick = closeBuildingProperties;
        document.body.appendChild(overlay);
    }
    overlay.classList.add('active');
}

// ── Spatial-alignment callout for the building-properties panel ────────────
// Populates #building-alignment-callout from /api/alignment/cand/<id>. Four
// outcome variants (green/red/purple/grey) + a "not in blocking pool" chip.
// Also decorates the BKAFI pairs list with a ⭐ on whichever row matches the
// NN-selected index_id.
window._lastAlignmentInfo = null;

function loadBuildingAlignmentInfo(buildingId) {
    const wrap = document.getElementById('building-alignment-callout');
    if (!wrap) return;
    wrap.style.display = 'none';
    wrap.innerHTML = '';
    window._lastAlignmentInfo = null;

    // Strip bag_/NL.IMBAG.Pand. prefixes so we hit the numeric ID the backend
    // keys on. Same trick the existing extractNumericId helper uses.
    const m = String(buildingId).match(/(\d{10,})/);
    const numericId = m ? m[1] : String(buildingId);

    fetch(`/api/alignment/cand/${encodeURIComponent(numericId)}`)
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(payload => {
            window._lastAlignmentInfo = payload;
            renderBuildingAlignmentCallout(payload);
            decorateBkafiListWithNN(payload);
        })
        .catch(err => {
            // 404 = pipeline ran but this cand isn't in matches_by_cand (e.g.
            // it's an index-only building or a pre-baked extra). Stay quiet.
            console.debug('alignment/cand fetch:', err);
        });
}

function renderBuildingAlignmentCallout(payload) {
    const wrap = document.getElementById('building-alignment-callout');
    if (!wrap || !payload || !payload.nn_match) return;
    const nn       = payload.nn_match;
    const d        = nn.distance_m;
    const dNum     = (d != null && !Number.isNaN(d)) ? Number(d) : null;
    const dTxt     = (dNum != null) ? `${dNum.toFixed(2)} m` : 'n/a';
    const cutoff   = payload.cutoff_m || 7.0;
    const inPool   = !!payload.in_blocking_pool;
    const within   = (dNum != null) && (dNum <= cutoff);
    const trueLbl  = Number(nn.true_label) === 1;
    let variant, title, body;
    if (within && trueLbl) {
        variant = 'green';
        title   = 'Confirmed match (true positive)';
        body    = `1-NN landed on the correct same-ID building <code>${nn.index_id}</code> at <strong>${dTxt}</strong> — within the ${cutoff} m cutoff distance.`;
    } else if (within && !trueLbl) {
        variant = 'red';
        title   = 'Predicted match — ground truth disagrees (false positive)';
        body    = `1-NN picked <code>${nn.index_id}</code> at <strong>${dTxt}</strong> (within the ${cutoff} m cutoff distance), but the true counterpart is a different building.`;
    } else if (!within && trueLbl) {
        variant = 'purple';
        title   = 'Same-ID building found, but too far away';
        body    = `1-NN landed on the correct same-ID building <code>${nn.index_id}</code>, but at <strong>${dTxt}</strong> — beyond the ${cutoff} m cutoff distance, so the spatial step considers it too far to confirm.`;
    } else {
        variant = 'grey';
        title   = 'No reliable match';
        body    = `1-NN landed on <code>${nn.index_id}</code> at <strong>${dTxt}</strong>. ${dNum != null && dNum > cutoff ? `Beyond the ${cutoff} m cutoff distance and ` : ''}this candidate has no same-ID counterpart in the index dataset.`;
    }
    const poolChip = inPool
        ? `<span class="nn-pool-chip nn-pool-yes" title="The 1-NN's index_id was also in the BKAFI blocking pool">in blocking pool</span>`
        : `<span class="nn-pool-chip nn-pool-no" title="The 1-NN's index_id was NOT in the BKAFI blocking pool — only the spatial step surfaced it">not in blocking pool</span>`;
    wrap.style.display = 'block';
    wrap.innerHTML = `
        <div class="nn-callout nn-callout-${variant}">
            <div class="nn-callout-title">${title} ${poolChip}</div>
            <div class="nn-callout-body">${body}</div>
        </div>`;
}

function decorateBkafiListWithNN(payload) {
    if (!payload || !payload.nn_match) return;
    const nnId = String(payload.nn_match.index_id);
    // The BKAFI list renders later (loadBuildingBkafiPairs fetches async).
    // Re-decorate a few times to land after the DOM appears.
    let attempts = 0;
    const tryDecorate = () => {
        const rows = document.querySelectorAll('#properties-list .bkafi-pair-row, #properties-list [data-index-id]');
        if (!rows.length && attempts++ < 6) {
            setTimeout(tryDecorate, 200);
            return;
        }
        rows.forEach(row => {
            const rowId = row.getAttribute('data-index-id') || row.dataset.indexId;
            if (rowId && String(rowId) === nnId) {
                if (!row.querySelector('.nn-star')) {
                    const star = document.createElement('span');
                    star.className = 'nn-star';
                    star.title = 'Selected by the spatial 1-NN step (Step 4)';
                    star.textContent = ' ⭐';
                    row.appendChild(star);
                }
            }
        });
    };
    tryDecorate();
}

// Close building properties window
function closeBuildingProperties() {
    const wrap = document.getElementById('building-alignment-callout');
    if (wrap) { wrap.style.display = 'none'; wrap.innerHTML = ''; }
    window._lastAlignmentInfo = null;
    const propsWindow = document.getElementById('building-properties-window');
    const overlay = document.getElementById('properties-overlay');
    
    if (propsWindow) {
        propsWindow.style.display = 'none';
    }
    
    if (overlay) {
        overlay.classList.remove('active');
    }
}

// Calculate geometric features (Step 1) - for all buildings
function calculateGeometricFeatures() {
    if (!selectedFile) {
        alert('Please select a candidates file first.');
        return;
    }
    
    // Can be called from sidebar button or building properties window
    const stepBtn = document.getElementById('step-btn-1');
    const calcBtn = document.getElementById('calc-features-btn');
    
    // Update button states
    stepBtn.textContent = 'Loading...';
    stepBtn.disabled = true;
    if (calcBtn) {
        calcBtn.textContent = 'Calculating...';
        calcBtn.disabled = true;
    }
    
    // Show loading overlay
    showLoading('Calculating geometric features for all buildings...');

    const handleFeatureSuccess = (message) => {
        console.log('Features calculated:', message);
        var safetyTimeout = setTimeout(function () {
            console.warn('Feature success: safety timeout — hiding loading overlay');
            hideLoading();
        }, 45000);
        var clearSafety = function () { clearTimeout(safetyTimeout); };
        try {
            showLoading('Updating building colors...');
            pipelineState.step1Completed = true;
            featuresLoaded = true;
            updatePipelineUI();
            updateViewerLegend();
            var step2Btn = document.getElementById('step-btn-2');
            if (step2Btn) step2Btn.disabled = false;
            stepBtn.textContent = 'Completed';
            stepBtn.style.background = '#28a745';
            // Advance tutorial only after the UI is ready (button enabled, colors updating)
            advanceTutorialForPipelineAction('calculateFeatures');
            updateBuildingColorsForStage1(true, function () {
                clearSafety();
                hideLoading();
            });
            if (selectedBuildingId) loadBuildingFeatures(selectedBuildingId);
            if (calcBtn) {
                calcBtn.textContent = 'Features Calculated';
                calcBtn.style.background = '#28a745';
            }
        } catch (err) {
            clearSafety();
            console.error('Error in handleFeatureSuccess:', err);
            hideLoading();
        }
    };

    const handleFeatureError = (errorMessage) => {
        console.error('Error calculating features:', errorMessage);
        hideLoading();
        alert('Error calculating geometric features: ' + errorMessage);
        stepBtn.textContent = 'Calculate Features';
        stepBtn.disabled = false;
        if (calcBtn) {
            calcBtn.textContent = 'Calculate Geometric Features';
            calcBtn.disabled = false;
        }
    };
    
    // Run pipeline stages up to 'features' (preprocess + properties) live in
    // the Celery worker. PipelineRunner handles the polling.
    window.PipelineRunner.start('features', {
        onProgress: ({ sub_stage, message, elapsed_s }) => {
            const label = sub_stage ? `[${sub_stage}] ${message || ''}` : (message || 'Running…');
            showLoading(`${label} (${elapsed_s || 0}s)`);
        },
        onComplete: ({ result }) => handleFeatureSuccess(result && result.cache_dir ? 'Features calculated' : 'Features calculated'),
        onError:    ({ error })  => handleFeatureError(error),
    }).catch(err => handleFeatureError(err.message));
}

// Load features for a specific building
function loadBuildingFeatures(buildingId) {
    if (!selectedFile) {
        console.warn('Cannot load features: no file selected');
        return;
    }
    
    if (!featuresLoaded) {
        console.warn('Features not yet calculated. Please run Step 1 first.');
        return;
    }
    
    console.log('Loading features for building:', buildingId);
    
    // Check cache first
    if (buildingFeaturesCache[buildingId]) {
        console.log('Using cached features for building:', buildingId);
        showGeometricFeatures(buildingFeaturesCache[buildingId]);
        return;
    }
    
    // Load from API
    console.log('Fetching features from API for building:', buildingId);
    fetch(`/api/building/features/${buildingId}?file=${encodeURIComponent(selectedFile)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error loading features:', data.error);
                const propsListEl = document.getElementById('properties-list');
                if (propsListEl) {
                    propsListEl.innerHTML = `<p style="color: red; padding: 20px;">Error loading features: ${data.error}</p>`;
                }
                return;
            }
            
            // Check if building was found
            if (data.found === false || !data.features || Object.keys(data.features).length === 0) {
                console.warn('No features returned for building:', buildingId);
                const propsListEl = document.getElementById('properties-list');
                if (propsListEl) {
                    const message = data.message || 'No features found for this building. This building may not be in the feature calculation dataset.';
                    propsListEl.innerHTML = `<div style="padding: 20px; color: #666;">
                        <p style="margin: 0 0 10px 0;">${message}</p>
                        <p style="margin: 0; font-size: 12px; color: #999;">Building ID: ${buildingId}</p>
                    </div>`;
                }
                return;
            }
            
            console.log('Features loaded successfully:', Object.keys(data.features).length, 'features');
            
            // Cache the features
            buildingFeaturesCache[buildingId] = data.features;
            
            // Show all features in properties window
            showGeometricFeatures(data.features);
        })
        .catch(error => {
            console.error('Error loading building features:', error);
            const propsListEl = document.getElementById('properties-list');
            if (propsListEl) {
                propsListEl.innerHTML = `<p style="color: red; padding: 20px;">Error: ${error.message}</p>`;
            }
        });
}

// Show geometric features in properties window
function showGeometricFeatures(features) {
    const propsListEl = document.getElementById('properties-list');
    const calcBtn = document.getElementById('calc-features-btn');
    const bkafiBtn = document.getElementById('run-bkafi-btn');
    if (!propsListEl || !features) {
        console.warn('Cannot show features: propsListEl or features missing', { propsListEl: !!propsListEl, features: !!features });
        return;
    }
    
    console.log('Displaying features for building:', selectedBuildingId);
    console.log('Number of features:', Object.keys(features).length);
    console.log('Feature keys:', Object.keys(features));
    
    // Remove existing geometric features section if it exists
    const existingFeaturesSection = propsListEl.querySelector('.geometric-features-section');
    if (existingFeaturesSection) {
        existingFeaturesSection.remove();
    }
    
    // Create container for geometric features
    const featuresContainer = document.createElement('div');
    featuresContainer.className = 'geometric-features-section';
    
    // Add heading
    const heading = document.createElement('div');
    heading.className = 'property-separator';
    const featureCount = Object.keys(features).length;
    heading.innerHTML = `<h4 style="margin: 0 0 15px 0; color: #667eea; font-size: 16px;">Geometric Features (${featureCount})</h4>`;
    featuresContainer.appendChild(heading);
    
    // Sort features alphabetically for better readability
    const sortedKeys = Object.keys(features).sort();
    
    // Add all features from the joblib file
    sortedKeys.forEach(key => {
        const value = features[key];
        const propItem = document.createElement('div');
        propItem.className = 'property-item feature-item';
        
        // Format the value appropriately
        let displayValue = value;
        if (typeof value === 'number') {
            displayValue = value.toFixed(4);
        } else if (Array.isArray(value)) {
            displayValue = `[${value.length} items]`;
        } else if (typeof value === 'object' && value !== null) {
            displayValue = JSON.stringify(value);
        }
        
        propItem.innerHTML = `
            <div class="property-key">${key}:</div>
            <div class="property-value">${displayValue}</div>
        `;
        featuresContainer.appendChild(propItem);
    });
    
    // Insert geometric features AFTER BKAFI section if it exists, otherwise at the beginning
    // This ensures BKAFI pairs always appear above geometric features
    const existingBkafiSection = propsListEl.querySelector('.bkafi-pairs-section');
    if (existingBkafiSection) {
        // Insert after BKAFI section
        existingBkafiSection.parentNode.insertBefore(featuresContainer, existingBkafiSection.nextSibling);
    } else {
        // Insert at the beginning if no BKAFI section
        propsListEl.insertBefore(featuresContainer, propsListEl.firstChild);
    }
    
    // Disable and update button text
    if (calcBtn) {
        calcBtn.disabled = true;
        calcBtn.textContent = 'Features Calculated';
        calcBtn.style.background = '#28a745';
    }
    
    // Show and enable BKAFI button if features exist and Step 1 is completed
    if (bkafiBtn && featureCount > 0 && pipelineState.step1Completed) {
        bkafiBtn.style.display = 'block';
        if (!pipelineState.step2Completed) {
            bkafiBtn.disabled = false;
            bkafiBtn.textContent = 'Run Blocking';
            bkafiBtn.style.background = '#667eea';
        } else {
            bkafiBtn.disabled = true;
            bkafiBtn.textContent = 'Blocking Completed';
            bkafiBtn.style.background = '#28a745';
        }
    }
}

// Run BKAFI (Step 2)
function runBKAFI() {
    if (!pipelineState.step1Completed) {
        alert('Please complete Geometric Featurization first.');
        return;
    }
    
    console.log('Loading BKAFI results');
    
    const stepBtn = document.getElementById('step-btn-2');
    stepBtn.textContent = 'Loading...';
    stepBtn.disabled = true;
    
    // Show loading overlay
    showLoading('Loading BKAFI results...');

    const handleBkafiSuccess = (message) => {
        console.log('BKAFI results loaded:', message);
        
        // Update loading message
        showLoading('Updating building colors...');
        
        // Mark step 2 as completed
        pipelineState.step2Completed = true;
        bkafiLoaded = true;
        updatePipelineUI();
        updateViewerLegend();
        
        // Enable step 3
        document.getElementById('step-btn-3').disabled = false;
        
        stepBtn.textContent = 'Completed';
        stepBtn.style.background = '#28a745';
        // Advance tutorial only after the UI is ready (step 3 enabled, colors updating)
        advanceTutorialForPipelineAction('runBKAFI');
        
        // Update building colors based on BKAFI pairs (use cached data if available)
        updateBuildingColorsForStage2(true, () => {
            hideLoading();
        });
        
        // Update BKAFI button in properties window if open
        const bkafiBtn = document.getElementById('run-bkafi-btn');
        if (bkafiBtn) {
            bkafiBtn.disabled = true;
            bkafiBtn.textContent = 'Blocking Completed';
            bkafiBtn.style.background = '#28a745';
        }
        
        // If building properties window is open, load BKAFI pairs
        if (selectedBuildingId) {
            loadBuildingBkafiPairs(selectedBuildingId);
        }

        // If blocking just recomputed with a new K, the downstream classify+align
        // outputs were invalidated server-side (see
        // pipeline_stages._invalidate_downstream_of_blocking). Detect that by
        // checking the manifest — if 'classify' no longer has a complete entry,
        // reset Step 3 + Step 4 so the user knows to re-click them.
        fetch('/api/pipeline/manifest').then(r => r.ok ? r.json() : null).then(m => {
            if (!m || !m.stages) return;
            const classifyStale = !m.stages.classify || !m.stages.classify.complete;
            if (classifyStale && pipelineState.step3Completed) {
                console.log('[runBKAFI] downstream invalidated — resetting Step 3 + Step 4');
                pipelineState.step3Completed = false;
                pipelineState.step4Completed = false;
                const s3 = document.getElementById('step-btn-3');
                if (s3) { s3.textContent = 'Run Classifier'; s3.style.background = ''; s3.disabled = false; }
                if (window.AlignmentStep && typeof window.AlignmentStep.reset === 'function') {
                    window.AlignmentStep.reset();
                }
                updatePipelineUI();
                updateViewerLegend();
            }
        }).catch(() => { /* network hiccup — ignore */ });
    };

    const handleBkafiError = (errorMessage) => {
        console.error('Error loading BKAFI results:', errorMessage);
        hideLoading();
        alert('Error loading BKAFI results: ' + errorMessage);
        stepBtn.textContent = 'Run Blocking';
        stepBtn.disabled = false;
    };
    
    // Run the BKAFI blocking stage live (cache HIT if K matches the cached run).
    const kInput = document.getElementById('cfg-blocking-k');
    const kVal = kInput ? parseInt(kInput.value, 10) : NaN;
    const body = (Number.isFinite(kVal) && kVal >= 1) ? { nn_count: kVal } : undefined;
    window.PipelineRunner.start('blocking', {
        body,
        onProgress: ({ sub_stage, message, elapsed_s }) => {
            const label = sub_stage ? `[${sub_stage}] ${message || ''}` : (message || 'Running…');
            showLoading(`${label} (${elapsed_s || 0}s)`);
        },
        onComplete: () => handleBkafiSuccess('BKAFI results loaded'),
        onError:    ({ error }) => handleBkafiError(error),
    }).catch(err => handleBkafiError(err.message));
}

// Load BKAFI pairs for a specific building
function loadBuildingBkafiPairs(buildingId) {
    if (!selectedFile) return;
    
    if (!bkafiLoaded) {
        console.warn('BKAFI results not loaded yet');
        return;
    }
    
    // Check cache first (cache always stores the full API response object)
    const cachedEntry = buildingBkafiCache[buildingId];
    if (cachedEntry) {
        const cachedPairs = Array.isArray(cachedEntry) ? cachedEntry : cachedEntry.pairs;
        if (cachedPairs && cachedPairs.length > 0) {
            showBkafiPairs(cachedPairs, buildingId);
            return;
        }
    }

    // Load from API
    console.log('Fetching BKAFI pairs from API for building:', buildingId);
    fetch(`/api/building/bkafi/${buildingId}?file=${encodeURIComponent(selectedFile)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error loading BKAFI pairs:', data.error);
                return;
            }
            
            if (!data.pairs || data.pairs.length === 0) {
                console.warn('No BKAFI pairs returned for building:', buildingId);
                return;
            }
            
            console.log('BKAFI pairs loaded successfully:', data.pairs.length, 'pairs');
            
            // Cache the full response object
            buildingBkafiCache[buildingId] = data;
            
            // Show pairs in properties window - pass the buildingId explicitly
            showBkafiPairs(data.pairs, buildingId);
        })
        .catch(error => {
            console.error('Error loading BKAFI pairs:', error);
        });
}

// Show BKAFI pairs in properties window
function showBkafiPairs(pairs, buildingId = null) {
    // Use provided buildingId or fall back to selectedBuildingId
    const currentBuildingId = buildingId || selectedBuildingId;
    
    const propsListEl = document.getElementById('properties-list');
    if (!propsListEl || !pairs || pairs.length === 0) return;
    
    if (!currentBuildingId) {
        console.error('Cannot show BKAFI pairs: no building ID available');
        return;
    }
    
    // Remove existing BKAFI section if it exists
    const existingBkafiSection = propsListEl.querySelector('.bkafi-pairs-section');
    if (existingBkafiSection) {
        existingBkafiSection.remove();
    }
    
    // Create container for BKAFI pairs
    const bkafiContainer = document.createElement('div');
    bkafiContainer.className = 'bkafi-pairs-section';
    
    // Store the building ID and pairs in data attributes for the button
    bkafiContainer.setAttribute('data-building-id', currentBuildingId);
    bkafiContainer.setAttribute('data-pairs', JSON.stringify(pairs));
    
    // Add separator and heading
    const separator = document.createElement('div');
    separator.className = 'property-separator';
    separator.innerHTML = `<h4 style="margin: 0 0 15px 0; color: #667eea; font-size: 16px;">Blocking Pairs (${pairs.length})</h4>`;
    bkafiContainer.appendChild(separator);
    
    // Add pairs (without prediction/true label - those will be shown after entity resolution)
    pairs.forEach((pair, index) => {
        const pairItem = document.createElement('div');
        pairItem.className = 'property-item feature-item';
        pairItem.style.background = '#f8f9fa';
        pairItem.style.borderLeft = '4px solid #667eea';
        pairItem.style.padding = '12px';
        pairItem.style.marginBottom = '8px';
        pairItem.style.borderRadius = '4px';
        
        pairItem.innerHTML = `
            <div style="margin-bottom: 6px;">
                <strong style="color: #333;">Pair ${index + 1}</strong>
            </div>
            <div style="font-size: 12px; color: #666;">
                <div><strong>Index Building ID:</strong> ${pair.index_id}</div>
            </div>
        `;
        bkafiContainer.appendChild(pairItem);
    });
    
    // Add button to view pairs visually - use the building ID and pairs from this specific section
    const viewButton = document.createElement('button');
    viewButton.className = 'action-btn';
    viewButton.id = 'tutorial-view-pairs-btn';  // used by tutorial beacon
    viewButton.style.marginTop = '15px';
    viewButton.style.width = '100%';
    viewButton.textContent = 'View Pairs Visually';
    viewButton.onclick = async () => {
        const containerBuildingId = bkafiContainer.getAttribute('data-building-id');
        let containerPairs = JSON.parse(bkafiContainer.getAttribute('data-pairs'));
        // After Step 4, replace the BKAFI-ranked carousel with the post-align
        // pool (BKAFI pairs ∪ NN pick, ranked by final_score). The backend
        // returns top-N already sorted; we just map field names so the existing
        // carousel render keeps working.
        if (window.pipelineState && window.pipelineState.step4Completed) {
            try {
                const m = String(containerBuildingId).match(/(\d{10,})/);
                const numericId = m ? m[1] : String(containerBuildingId);
                const r = await fetch(`/api/alignment/cand/${encodeURIComponent(numericId)}?pool_limit=3`);
                if (r.ok) {
                    const info = await r.json();
                    window._lastAlignmentInfo = info;
                    if (Array.isArray(info.pool) && info.pool.length > 0) {
                        containerPairs = info.pool.map(p => ({
                            index_id:        p.index_id,
                            // Confidence column shows the XGBoost classifier score
                            // for the BKAFI pairs; the NN-only pick has none (null).
                            confidence:      (p.confidence != null) ? p.confidence : null,
                            predicted_label: p.predicted_label,
                            true_label:      p.true_label,
                            final_score:     p.final_score,
                            distance_m:      p.distance_m,
                            _nnInjected:     !!p.is_nn_pick,
                            _nnInPool:       !!p.is_nn_pick && p.confidence != null,
                        }));
                    }
                }
            } catch (e) { console.debug('alignment pool fetch failed:', e); }
        }
        openBkafiComparisonWindow(containerBuildingId, containerPairs);
    };
    bkafiContainer.appendChild(viewButton);
    
    // Always insert BKAFI pairs at the very beginning (top of properties window)
    // This ensures BKAFI pairs always appear above geometric features
    propsListEl.insertBefore(bkafiContainer, propsListEl.firstChild);
    
    // If geometric features section exists, move it after BKAFI section
    const existingFeaturesSection = propsListEl.querySelector('.geometric-features-section');
    if (existingFeaturesSection && existingFeaturesSection !== bkafiContainer.nextSibling) {
        // Remove and re-insert after BKAFI section
        existingFeaturesSection.parentNode.removeChild(existingFeaturesSection);
        bkafiContainer.parentNode.insertBefore(existingFeaturesSection, bkafiContainer.nextSibling);
    }
}

// Open BKAFI comparison window
// ─── Parallel loader helpers ────────────────────────────────────────────────
/** Fetch file path + minimal CityJSON for a building (2 API calls, returns promise) */
async function _fetchBuildingDataForComparison(buildingId) {
    const numericId = buildingId.replace(/^[^_]*_/, '');
    const fileData = await fetch(`/api/building/find-file/${encodeURIComponent(numericId)}`).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    });
    if (fileData.error || !fileData.file_path) throw new Error(fileData.error || 'Building not found');
    const cityJSON = await fetch(`/api/building/single/${encodeURIComponent(numericId)}?file=${encodeURIComponent(fileData.file_path)}`).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    });
    return { buildingId, numericId, cityJSON, filePath: fileData.file_path };
}

// Slice one cand out of post_disaster_cands.json — the actual geometry the
// pipeline ran on (rotated + translated + height-damaged). Available only
// after Step 4 runs.
async function _fetchCandPostDisasterGeometry(buildingId) {
    // Extract the 10+ digit numeric BAG id from whatever form the viewer uses
    // (works for "bag_<id>", "NL.IMBAG.Pand.<id>-0", and raw "<id>").
    const m = String(buildingId).match(/(\d{10,})/);
    const numericId = m ? m[1] : String(buildingId);
    const r = await fetch(`/api/alignment/cand/${encodeURIComponent(numericId)}/cityjson?stage=post_disaster`);
    if (!r.ok) {
        let detail; try { detail = (await r.json()).error || r.statusText; } catch { detail = r.statusText; }
        throw new Error(detail);
    }
    const cityJSON = await r.json();
    return { buildingId, numericId, cityJSON };
}

/** Initialise a ThreeBuildingViewer inside viewerEl and load cityJSON (returns promise) */
// Default blue matches the Cesium map default building color (116, 151, 223).
// Pair slots each get a distinct vivid color so buildings are easy to tell apart.
const _CANDIDATE_VIEWER_COLOR = 0x7497DF;
const _PAIR_VIEWER_COLORS     = [0xF44336, 0x4CAF50, 0x9C27B0]; // red, green, purple

function _loadCityJSONInViewer(buildingId, cityJSON, viewerEl, viewerType, color = null) {
    return new Promise((resolve) => {
        if (!viewerEl) { resolve(null); return; }
        // Use a CHILD wrapper so viewerEl's original ID stays intact for future lookups
        const containerId = `cv-${viewerType}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
        const vkey = `comparison-viewer-${viewerType}`;
        // Dispose previous viewer for this slot
        if (window[vkey] && window[vkey].dispose) {
            try { window[vkey].dispose(); } catch (_) {}
            delete window[vkey];
        }
        viewerEl.innerHTML = '';
        viewerEl.style.position = 'relative';
        // Create an inner wrapper with the unique ID so the outer element keeps its stable ID
        const wrapper = document.createElement('div');
        wrapper.id = containerId;
        wrapper.style.cssText = 'position:absolute;inset:0;';
        const loadingMsg = document.createElement('div');
        loadingMsg.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#888;';
        loadingMsg.textContent = 'Rendering…';
        viewerEl.appendChild(loadingMsg);
        viewerEl.appendChild(wrapper);
        const resolvedColor = color !== null ? color
            : viewerType === 'candidate' ? _CANDIDATE_VIEWER_COLOR
            : _PAIR_VIEWER_COLORS[0];
        let viewer;
        try { viewer = new ThreeBuildingViewer(containerId, resolvedColor); }
        catch (e) { viewerEl.innerHTML = `<div style="padding:12px;color:#dc3545;font-size:12px;">Init error: ${e.message}</div>`; resolve(null); return; }
        window[vkey] = viewer;
        // ThreeBuildingViewer.init() is synchronous — isInitialized is true by the time
        // the constructor returns, so we can call loadBuilding immediately.
        if (viewer.isInitialized) {
            try { viewer.loadBuilding(cityJSON); } catch (_) {}
            if (loadingMsg.parentNode) loadingMsg.remove();
            resolve(viewer);
        } else {
            viewerEl.innerHTML = '<div style="padding:12px;color:#dc3545;font-size:12px;">Init failed</div>';
            resolve(null);
        }
    });
}
// ────────────────────────────────────────────────────────────────────────────

function openBkafiComparisonWindow(candidateBuildingId, pairs) {
    const comparisonWindow = document.getElementById('bkafi-comparison-window');
    if (!comparisonWindow) return;

    // ── state ────────────────────────────────────────────────────────────────
    const pairsToShow   = pairs.slice(0, 3);
    let   currentIdx    = 0;          // which option is shown in the carousel
    let   userGuess     = null;       // null = not picked yet
    let   pairCityData  = [];         // [{buildingId, cityJSON}] filled after parallel fetch
    let   pairViewer    = null;       // single ThreeBuildingViewer for the right side

    comparisonWindow.setAttribute('data-candidate-id', candidateBuildingId);
    comparisonWindow.setAttribute('data-pairs', JSON.stringify(pairs));
    comparisonWindow.removeAttribute('data-revealed');
    comparisonWindow.style.display = 'flex';

    // overlay
    let overlay = document.getElementById('comparison-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'comparison-overlay';
        overlay.className = 'comparison-overlay';
        overlay.onclick = closeBkafiComparisonWindow;
        document.body.appendChild(overlay);
    }
    overlay.classList.add('active');

    const headerTitle = comparisonWindow.querySelector('.comparison-header h3');
    if (headerTitle) headerTitle.textContent = 'Find the Best Match';

    // Hide any tutorial beacon/highlight — the comparison window covers the BPW spotlight
    hideBeacon();
    hideHighlight();

    // Advance tutorial if waiting for pairs to be opened (watcher handles advancement with advanceDelay)
    if (!tutorialState.completed) {
        tutorialState.pairsOpened = true;
    }

    cleanupComparisonViewers();

    // ── candidate viewer (left) ───────────────────────────────────────────────
    const candidateViewerEl = document.getElementById('comparison-viewer-candidate');
    const candidateIdEl     = document.getElementById('comparison-candidate-id');
    const pairsViewersEl    = document.getElementById('comparison-pairs-viewers');

    if (candidateViewerEl) {
        candidateViewerEl.innerHTML = '';
        candidateViewerEl.id = 'comparison-viewer-candidate';
        candidateViewerEl.style.cssText = 'width:100%;height:240px;min-height:240px;max-height:240px;position:relative;';
    }
    if (candidateIdEl) candidateIdEl.textContent = `Candidate: ${candidateBuildingId}`;
    if (pairsViewersEl) pairsViewersEl.innerHTML = '';

    // ── Geometry toggle (Post-disaster ↔ Pristine) ───────────────────────────
    // The model runs on post-disaster geometry, so default to that whenever
    // Step 4 has completed and the data is available. Pristine is the
    // original Source A geometry, kept as a reference view.
    const step4Done = !!(window.pipelineState && window.pipelineState.step4Completed);
    let geomVariant = step4Done ? 'post_disaster' : 'pristine';
    const damageNoteEl = document.createElement('div');
    damageNoteEl.id = 'cand-damage-note';
    damageNoteEl.className = 'cand-damage-note';
    if (candidateViewerEl && candidateViewerEl.parentNode) {
        // Insert toggle ABOVE the candidate viewer (only when step4 is done).
        const old = document.getElementById('cand-geom-toggle');
        if (old) old.remove();
        const oldNote = document.getElementById('cand-damage-note');
        if (oldNote) oldNote.remove();
        if (step4Done) {
            const toggleRow = document.createElement('div');
            toggleRow.id = 'cand-geom-toggle';
            toggleRow.className = 'cand-geom-toggle';
            toggleRow.innerHTML = `
                <span class="cand-geom-label">Geometry:</span>
                <button type="button" class="cand-geom-btn active" data-variant="post_disaster" title="What the model actually saw (rotated + translated + height-damaged)">Post-disaster</button>
                <button type="button" class="cand-geom-btn" data-variant="pristine" title="Original Source A geometry, before DisasterSimulator">Pristine</button>
            `;
            candidateViewerEl.parentNode.insertBefore(toggleRow, candidateViewerEl);
            candidateViewerEl.parentNode.insertBefore(damageNoteEl, candidateViewerEl.nextSibling);
            toggleRow.querySelectorAll('.cand-geom-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    if (btn.classList.contains('active')) return;
                    toggleRow.querySelectorAll('.cand-geom-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    geomVariant = btn.getAttribute('data-variant');
                    await _loadCandidateVariant(geomVariant);
                });
            });
        }
    }

    async function _loadCandidateVariant(variant) {
        if (!candidateViewerEl) return;
        try {
            let res;
            if (variant === 'post_disaster') {
                res = await _fetchCandPostDisasterGeometry(candidateBuildingId);
                const df = res.cityJSON && res.cityJSON.metadata && res.cityJSON.metadata.damage_factor;
                if (damageNoteEl) {
                    if (df != null) {
                        const pct = Math.round(Number(df) * 100);
                        damageNoteEl.textContent = `Damage applied — height reduced to ${pct}% of original.`;
                        damageNoteEl.style.display = 'block';
                    } else {
                        damageNoteEl.textContent = '';
                        damageNoteEl.style.display = 'none';
                    }
                }
            } else {
                res = await _fetchBuildingDataForComparison(candidateBuildingId);
                if (damageNoteEl) { damageNoteEl.textContent = ''; damageNoteEl.style.display = 'none'; }
            }
            await _loadCityJSONInViewer(res.buildingId, res.cityJSON, candidateViewerEl, 'candidate', _CANDIDATE_VIEWER_COLOR);
        } catch (e) {
            console.warn(`[cand toggle] failed to load ${variant}:`, e);
            if (damageNoteEl) {
                damageNoteEl.textContent = `Could not load ${variant} geometry.`;
                damageNoteEl.style.display = 'block';
            }
        }
    }
    // Expose so the parallel-load block below can use the same code path.
    comparisonWindow._loadCandidateVariant = _loadCandidateVariant;
    comparisonWindow._currentGeomVariant   = () => geomVariant;

    // ── intro bar ─────────────────────────────────────────────────────────────
    const compContent = comparisonWindow.querySelector('.comparison-content');
    let introBar = compContent && compContent.querySelector('.game-intro-bar');
    if (!introBar && compContent) {
        introBar = document.createElement('div');
        introBar.className = 'game-intro-bar';
        compContent.insertBefore(introBar, compContent.firstChild);
    }
    if (introBar) introBar.innerHTML = 'Look at the <strong>candidate building</strong>. Navigate the BKAFI options on the right and <strong>pick the best match</strong>.';

    // ── carousel card (right side) ─────────────────────────────────────────────
    //
    //  ┌─────────────────────────────────┐
    //  │  ← [Option 1 / 3] →            │  ← nav row
    //  │  [3D viewer]                    │
    //  │  ● ○ ○  (dots)                  │
    //  │  Index: 051810...               │
    //  │  [Pick This Match]              │
    //  │  [result badge]                 │
    //  └─────────────────────────────────┘

    const card = document.createElement('div');
    card.id = 'carousel-pair-card';
    card.className = 'carousel-pair-card';
    card.style.cssText = 'display:flex;flex-direction:column;';

    // 3-D viewer area — arrows and counter are overlaid INSIDE the viewer so it takes no extra height
    const pairViewerEl = document.createElement('div');
    pairViewerEl.id = 'comparison-viewer-pair-carousel';
    // Same CSS class as the candidate so both boxes look identical
    pairViewerEl.className = 'comparison-viewer';
    pairViewerEl.style.position = 'relative';
    pairViewerEl.innerHTML = '<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#999;">Loading…</div>';

    // Counter overlay (top-center of viewer)
    const counter = document.createElement('div');
    counter.id = 'carousel-counter';
    counter.className = 'carousel-counter-overlay';
    pairViewerEl.appendChild(counter);

    // Prev / next arrows (overlaid on left/right edges of viewer)
    const prevBtn = document.createElement('button');
    prevBtn.className = 'carousel-nav-btn carousel-nav-prev';
    prevBtn.setAttribute('aria-label', 'Previous option');
    prevBtn.innerHTML = '&#8249;';
    pairViewerEl.appendChild(prevBtn);

    const nextBtn = document.createElement('button');
    nextBtn.className = 'carousel-nav-btn carousel-nav-next';
    nextBtn.setAttribute('aria-label', 'Next option');
    nextBtn.innerHTML = '&#8250;';
    pairViewerEl.appendChild(nextBtn);

    // "Your Pick" ribbon (overlaid inside the viewer)
    const yourPickRibbon = document.createElement('div');
    yourPickRibbon.id = 'carousel-your-pick';
    yourPickRibbon.className = 'carousel-your-pick';
    yourPickRibbon.textContent = 'Your Pick';
    pairViewerEl.appendChild(yourPickRibbon);

    card.appendChild(pairViewerEl);

    // Dot indicator row
    const dotRow = document.createElement('div');
    dotRow.className = 'carousel-dot-row';
    const dots = pairsToShow.map((_, i) => {
        const d = document.createElement('div');
        d.className = 'carousel-dot' + (i === 0 ? ' active' : '');
        d.title = `Option ${i + 1}`;
        d.addEventListener('click', () => goTo(i));
        dotRow.appendChild(d);
        return d;
    });
    card.appendChild(dotRow);

    // Building ID label
    const pairIdEl = document.createElement('div');
    pairIdEl.className = 'viewer-building-id';
    pairIdEl.id = 'carousel-pair-id';
    pairIdEl.style.textAlign = 'center';
    pairIdEl.textContent = 'Loading…';
    card.appendChild(pairIdEl);

    // Pick button
    const pickBtn = document.createElement('button');
    pickBtn.id = 'carousel-pick-btn';
    pickBtn.className = 'pair-pick-btn';
    pickBtn.textContent = 'Pick This Match';
    pickBtn.disabled = true;
    pickBtn.addEventListener('click', () => selectGuess(currentIdx));
    card.appendChild(pickBtn);

    // Result badge (shown after reveal)
    const resultBadge = document.createElement('div');
    resultBadge.id = 'carousel-result-badge';
    resultBadge.className = 'carousel-result-badge';
    card.appendChild(resultBadge);

    if (pairsViewersEl) pairsViewersEl.appendChild(card);

    // ── guess bar + reveal button (below both viewers) ───────────────────────
    const classifierSection = document.getElementById('comparison-classifier-section');
    if (classifierSection) {
        classifierSection.style.display = 'flex';
        classifierSection.innerHTML = '';

        const guessBar = document.createElement('div');
        guessBar.id = 'game-guess-bar';
        guessBar.className = 'game-guess-bar';
        guessBar.style.cssText = 'margin-bottom:0;';
        guessBar.innerHTML = '<span style="color:#94a3b8;">Browse the options above, then pick your best match.</span>';
        classifierSection.appendChild(guessBar);

        const actRow = document.createElement('div');
        actRow.style.cssText = 'display:flex;gap:10px;align-items:center;justify-content:center;flex-wrap:wrap;';

        const revealBtn = document.createElement('button');
        revealBtn.id = 'reveal-answer-btn';
        revealBtn.className = 'reveal-answer-btn';
        revealBtn.style.cssText = 'margin:0;';
        revealBtn.textContent = "Reveal Model's Answer";
        revealBtn.disabled = true;
        revealBtn.addEventListener('click', () => {
            hideBeacon();
            const sp = JSON.parse(comparisonWindow.getAttribute('data-pairs') || '[]');
            showClassifierResultsInComparisonWindow(
                comparisonWindow.getAttribute('data-candidate-id'), sp, userGuess
            );
        });
        actRow.appendChild(revealBtn);

        const skipLink = document.createElement('a');
        skipLink.href = '#';
        skipLink.style.cssText = 'font-size:11px;color:#94a3b8;text-decoration:underline;white-space:nowrap;';
        skipLink.textContent = 'Skip — just show results';
        skipLink.addEventListener('click', (e) => {
            e.preventDefault();
            hideBeacon();
            const sp = JSON.parse(comparisonWindow.getAttribute('data-pairs') || '[]');
            showClassifierResultsInComparisonWindow(
                comparisonWindow.getAttribute('data-candidate-id'), sp, null
            );
        });
        actRow.appendChild(skipLink);

        classifierSection.appendChild(actRow);
    }

    // ── helpers ───────────────────────────────────────────────────────────────
    function updateCarouselUI() {
        counter.textContent = `Option ${currentIdx + 1} / ${n}`;
        prevBtn.disabled = false;
        nextBtn.disabled = false;
        const currentPair = pairsToShow[currentIdx];
        // The NN-injected marker is only revealed AFTER the user clicks Reveal —
        // otherwise it spoils which pair the spatial step picked. Before reveal,
        // every slide shows only its index_id like a normal BKAFI option.
        const revealed = comparisonWindow.getAttribute('data-revealed') === '1';
        if (revealed && currentPair && currentPair._nnInjected) {
            const chip = currentPair._nnInPool
                ? '<span class="nn-pool-chip nn-pool-yes" style="margin-left:6px">also in blocking pool</span>'
                : '<span class="nn-pool-chip nn-pool-no" style="margin-left:6px">not in blocking pool</span>';
            const dTxt = (currentPair.distance_m != null) ? ` · d=${Number(currentPair.distance_m).toFixed(2)} m` : '';
            pairIdEl.innerHTML = `⭐ Spatial NN match — Index: ${currentPair.index_id}${dTxt} ${chip}`;
        } else {
            pairIdEl.textContent = currentPair ? `Index: ${currentPair.index_id}` : '';
        }
        // dots
        dots.forEach((d, i) => {
            d.classList.toggle('active', i === currentIdx);
        });
        // ribbon
        yourPickRibbon.classList.toggle('visible', userGuess === currentIdx);
        // pick button label
        if (comparisonWindow.getAttribute('data-revealed') !== '1') {
            pickBtn.textContent = userGuess === currentIdx ? 'Your Pick' : 'Pick This Match';
            pickBtn.classList.toggle('selected', userGuess === currentIdx);
        }
    }

    function goTo(idx) {
        if (pairCityData.length === 0) return; // data not loaded yet
        currentIdx = idx;
        updateCarouselUI();
        // Switch the building in the viewer (both before AND after reveal)
        if (pairViewer && pairViewer.isInitialized && pairCityData[idx]) {
            try {
                pairViewer.buildingColor = _PAIR_VIEWER_COLORS[idx % _PAIR_VIEWER_COLORS.length];
                pairViewer.loadBuilding(pairCityData[idx].cityJSON);
            } catch (_) {}
        }
        // If already revealed, refresh the result styling for the new current option
        if (comparisonWindow.getAttribute('data-revealed') === '1') {
            applyRevealToCurrent();
        }
    }

    const n = pairsToShow.length;
    prevBtn.addEventListener('click', () => goTo((currentIdx - 1 + n) % n));
    nextBtn.addEventListener('click', () => goTo((currentIdx + 1) % n));

    function selectGuess(idx) {
        if (comparisonWindow.getAttribute('data-revealed') === '1') return;
        userGuess = idx;
        // Update dot colors
        dots.forEach((d, i) => d.classList.toggle('picked', i === idx));
        updateCarouselUI();
        // Update guess bar
        const guessBar = document.getElementById('game-guess-bar');
        if (guessBar) guessBar.innerHTML = `You picked: <span class="guess-text">Option ${idx + 1}</span> — ready?`;
        const revealBtn = document.getElementById('reveal-answer-btn');
        if (revealBtn) revealBtn.disabled = false;
    }

    // Called after reveal to paint the current carousel slot with the result
    function applyRevealToCurrent() {
        const sp = JSON.parse(comparisonWindow.getAttribute('data-pairs') || '[]');
        const pair = sp[currentIdx];
        if (!pair) return;
        const pred = pair.prediction !== undefined ? pair.prediction : (pair.confidence > 0.5 ? 1 : 0);
        const tl   = pair.true_label !== undefined && pair.true_label !== null ? pair.true_label : null;
        card.classList.remove('pair-result-true','pair-result-false-positive','pair-result-no-match','pair-selected');
        resultBadge.className = 'carousel-result-badge visible';
        if (pred === 1 && tl === 1) {
            card.classList.add('pair-result-true');
            resultBadge.classList.add('true-match');
            resultBadge.textContent = 'True Match';
        } else if (pred === 1 && tl === 0) {
            card.classList.add('pair-result-false-positive');
            resultBadge.classList.add('false-pos');
            resultBadge.textContent = 'False Positive';
        } else if (tl === 1) {
            card.classList.add('pair-result-true');
            resultBadge.classList.add('true-match');
            resultBadge.textContent = 'True Match (AI missed it)';
        } else {
            card.classList.add('pair-result-no-match');
            resultBadge.classList.add('no-match');
            resultBadge.textContent = 'No Match';
        }
        // Update dots with result colors
        sp.slice(0, 3).forEach((p, i) => {
            const pr = p.prediction !== undefined ? p.prediction : (p.confidence > 0.5 ? 1 : 0);
            const lt = p.true_label !== undefined && p.true_label !== null ? p.true_label : null;
            dots[i].classList.remove('picked','result-true','result-fp','result-none');
            if (pr === 1 && lt === 1) dots[i].classList.add('result-true');
            else if (pr === 1 && lt === 0) dots[i].classList.add('result-fp');
            else if (lt === 1) dots[i].classList.add('result-true');
            else dots[i].classList.add('result-none');
        });
    }
    // expose so showClassifierResultsInComparisonWindow can trigger the first paint
    comparisonWindow._applyRevealToCurrent = applyRevealToCurrent;
    comparisonWindow._goTo = goTo;
    // After Reveal the carousel needs to re-render so the ⭐ NN-match label
    // and "in / not in blocking pool" chip appear on the current slide. The
    // reveal handler calls this — it's harmless to no-op before reveal too.
    comparisonWindow._refreshCarouselLabels = updateCarouselUI;

    // ── parallel fetch + load ──────────────────────────────────────────────────
    updateCarouselUI(); // show counters immediately (disabled state)
    counter.textContent = `Loading… (0 / ${pairsToShow.length})`;

    // Pair fetches stay pristine (Source B is never disaster-shifted). Cand is
    // loaded via the variant-aware helper so the initial render respects the
    // toggle default (post_disaster when Step 4 has run).
    const pairIds = pairsToShow.map(p => p.index_id);

    Promise.all(pairIds.map(id =>
        _fetchBuildingDataForComparison(id).catch(e => ({ error: e.message, buildingId: id }))
    )).then(async (results) => {
        // Store pair city data
        pairCityData = results.map(r => r.error ? null : r);

        // Load candidate viewer first, then pair (stagger to avoid dual WebGL init issues)
        if (candidateViewerEl) {
            await _loadCandidateVariant(geomVariant);
        }

        // Small pause between WebGL context creations
        await new Promise(r => setTimeout(r, 80));

        // Load pair carousel viewer (only ONE Three.js viewer for all pairs)
        const firstPairIdx = pairCityData.findIndex(d => d !== null);
        const firstPair = firstPairIdx !== -1 ? pairCityData[firstPairIdx] : null;
        if (firstPair && pairViewerEl) {
            pairViewer = await _loadCityJSONInViewer(firstPair.buildingId, firstPair.cityJSON, pairViewerEl, 'pair-carousel', _PAIR_VIEWER_COLORS[firstPairIdx % _PAIR_VIEWER_COLORS.length]);
            // Re-attach all overlay controls (innerHTML reset in _loadCityJSONInViewer wipes them)
            pairViewerEl.style.position = 'relative';
            pairViewerEl.appendChild(counter);
            pairViewerEl.appendChild(prevBtn);
            pairViewerEl.appendChild(nextBtn);
            pairViewerEl.appendChild(yourPickRibbon);
            pickBtn.disabled = false;
        }
        // Store pair cityJSON data so "Show Matches on Map" can use it for buildings not in the Cesium viewer
        comparisonWindow._pairCityData = pairCityData;
        // Store pairCityData on the window element so the "Show Matches on Map" button can access it
        comparisonWindow._pairCityData = pairCityData;
        updateCarouselUI();
    });
}

// Find which file contains a building and load it
function findAndLoadBuilding(buildingId, viewerEl, idEl, viewerType, onComplete) {
    console.log(`Finding file for building ${buildingId} (${viewerType})`);
    
    if (!viewerEl) {
        console.error(`Viewer element not found for ${viewerType}`);
        if (onComplete) onComplete();
        return;
    }
    
    // Update loading message without clearing the entire element (to preserve any existing structure)
    let existingLoading = viewerEl.querySelector('div[style*="padding: 20px"]');
    if (existingLoading) {
        existingLoading.textContent = 'Finding building file...';
        existingLoading.style.fontSize = '12px';
    } else {
        // Only add loading message if one doesn't exist
        existingLoading = document.createElement('div');
        existingLoading.style.cssText = 'padding: 20px; text-align: center; color: #666; font-size: 12px;';
        existingLoading.textContent = 'Finding building file...';
        viewerEl.appendChild(existingLoading);
    }
    
    // Store reference to loading element for cleanup
    const loadingElement = existingLoading;
    
    // Extract numeric ID if building ID has prefix (e.g., "bag_0518100000239978" -> "0518100000239978")
    const numericId = buildingId.replace(/^[^_]*_/, '');
    console.log(`Searching for building with ID: ${buildingId} (numeric: ${numericId})`);
    
    fetch(`/api/building/find-file/${encodeURIComponent(numericId)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.error || !data.file_path) {
                console.error(`Error finding file for building ${buildingId}:`, data.error || data.message);
                const errorDiv = document.createElement('div');
                errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545;';
                errorDiv.textContent = `Building not found: ${data.error || data.message || 'Unknown error'}`;
                // Remove loading message and add error
                if (loadingElement && loadingElement.parentNode) {
                    loadingElement.parentNode.removeChild(loadingElement);
                }
                viewerEl.appendChild(errorDiv);
                if (onComplete) onComplete();
                return;
            }
            
            console.log(`Found building ${buildingId} in file ${data.file_path} (source: ${data.source})`);
            // Remove loading message before loading building
            if (loadingElement && loadingElement.parentNode) {
                loadingElement.parentNode.removeChild(loadingElement);
            }
            loadBuildingInComparisonViewer(buildingId, data.file_path, viewerEl, idEl, viewerType, onComplete);
        })
        .catch(error => {
            console.error(`Error finding file for building ${buildingId}:`, error);
            const errorDiv = document.createElement('div');
            errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545;';
            errorDiv.textContent = `Error: ${error.message}`;
            // Remove loading message and add error
            if (loadingElement && loadingElement.parentNode) {
                loadingElement.parentNode.removeChild(loadingElement);
            }
            viewerEl.appendChild(errorDiv);
            if (onComplete) onComplete();
        });
}

// Load a single building in a comparison viewer (shows ONLY that building)
function loadBuildingInComparisonViewer(buildingId, filePath, viewerEl, idEl, viewerType, onComplete) {
    console.log(`=== loadBuildingInComparisonViewer called ===`);
    console.log(`Building ID: ${buildingId}`);
    console.log(`File path: ${filePath}`);
    console.log(`Viewer type: ${viewerType}`);
    console.log(`Viewer element:`, viewerEl);
    console.log(`ID element:`, idEl);
    
    if (!viewerEl) {
        console.error(`Cannot load building ${buildingId}: viewer element is null`);
        if (onComplete) onComplete();
        return;
    }
    
    console.log(`Loading ONLY building ${buildingId} from ${filePath} in ${viewerType} viewer`);
    
    // Store original ID if it exists (so we can find the element later)
    const originalId = viewerEl.id || `comparison-viewer-${viewerType}`;
    
    // Create a unique container ID for this viewer (but keep original ID as data attribute)
    const containerId = `comparison-viewer-${viewerType}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    console.log(`Container ID: ${containerId}, Original ID: ${originalId}`);
    viewerEl.id = containerId;
    viewerEl.setAttribute('data-original-id', originalId); // Store original ID
    // Don't clear innerHTML - we'll add loading message as a child element instead
    // This prevents removing the Three.js canvas later
    viewerEl.style.position = 'relative'; // Ensure positioning context for loading message
    
    // Store viewer reference - use unique key per viewer type
    const viewerKey = `comparison-viewer-${viewerType}`;
    console.log(`Viewer key: ${viewerKey}`);
    
    // Extract numeric ID if building ID has prefix (e.g., "bag_0518100000239978" -> "0518100000239978")
    const numericId = buildingId.replace(/^[^_]*_/, '');
    console.log(`Loading building with ID: ${buildingId} (using numeric: ${numericId}) from file: ${filePath}`);
    
    // Load ONLY the single building (minimal CityJSON with just this building)
    fetch(`/api/building/single/${encodeURIComponent(numericId)}?file=${encodeURIComponent(filePath)}`)
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => Promise.reject(new Error(err.error || `HTTP ${response.status}`)));
            }
            return response.json();
        })
        .then(minimalCityJSON => {
            console.log(`=== Minimal CityJSON loaded ===`);
            console.log(`Building ID: ${buildingId}`);
            console.log(`CityJSON keys:`, Object.keys(minimalCityJSON));
            console.log(`CityJSON has ${Object.keys(minimalCityJSON.CityObjects || {}).length} city objects`);
            if (minimalCityJSON.CityObjects) {
                console.log(`City object IDs:`, Object.keys(minimalCityJSON.CityObjects));
            }
            
            // Clear any existing content but keep the container structure
            // Remove loading messages but keep the element itself
            const existingLoading = viewerEl.querySelector('div[style*="padding: 20px"]');
            if (existingLoading) {
                existingLoading.remove();
            }
            
            // Add new loading message
            const loadingDiv = document.createElement('div');
            loadingDiv.id = `loading-msg-${containerId}`;
            loadingDiv.style.cssText = 'padding: 20px; text-align: center; color: #666; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10; background: rgba(255,255,255,0.9); border-radius: 4px;';
            loadingDiv.textContent = 'Initializing viewer...';
            viewerEl.appendChild(loadingDiv);
            
            // Check if Three.js is loaded - wait for it if needed
            const checkThreeJS = (attempts = 0) => {
                if (typeof THREE !== 'undefined') {
                    initializeThreeViewer();
                } else if (attempts < 50) {
                    // Wait up to 5 seconds for Three.js to load
                    setTimeout(() => checkThreeJS(attempts + 1), 100);
                } else {
                    console.error('Three.js library failed to load after 5 seconds!');
                    const errorDiv = document.createElement('div');
                    errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10;';
                    errorDiv.textContent = 'Three.js library not loaded. Please refresh the page.';
                    viewerEl.appendChild(errorDiv);
                    if (onComplete) onComplete();
                }
            };
            
            const initializeThreeViewer = () => {
                setTimeout(() => {
                try {
                    console.log(`=== USING THREE.JS VIEWER (NOT CESIUM) ===`);
                    console.log(`Creating Three.js viewer for ${buildingId} in container ${containerId}`);
                    console.log(`Three.js available:`, typeof THREE !== 'undefined');
                    console.log(`ThreeBuildingViewer available:`, typeof ThreeBuildingViewer !== 'undefined');
                    
                    // Get or create loading message
                    let loadingMsg = viewerEl.querySelector(`#loading-msg-${containerId}`);
                    if (!loadingMsg) {
                        loadingMsg = document.createElement('div');
                        loadingMsg.id = `loading-msg-${containerId}`;
                        loadingMsg.style.cssText = 'padding: 20px; text-align: center; color: #666; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10; background: rgba(255,255,255,0.9); border-radius: 4px;';
                        loadingMsg.textContent = 'Initializing viewer...';
                        viewerEl.appendChild(loadingMsg);
                    }
                    
                    // Dispose old viewer if it exists
                    if (window[viewerKey]) {
                        try {
                            const oldViewer = window[viewerKey];
                            if (oldViewer.dispose) {
                                oldViewer.dispose();
                            }
                        } catch (e) {
                            console.warn('Error disposing old viewer:', e);
                        }
                        delete window[viewerKey];
                    }
                    
                    // Dispose old viewer if it exists
                    if (window[viewerKey]) {
                        try {
                            const oldViewer = window[viewerKey];
                            if (oldViewer.dispose) {
                                oldViewer.dispose();
                            }
                        } catch (e) {
                            console.warn('Error disposing old viewer:', e);
                        }
                        delete window[viewerKey];
                    }
                    
                    // Create Three.js viewer (lightweight, fast) - NOT Cesium!
                    const buildingColor = viewerType === 'candidate'
                        ? _CANDIDATE_VIEWER_COLOR
                        : _PAIR_VIEWER_COLORS[0];
                    const viewer = new ThreeBuildingViewer(containerId, buildingColor);
                    window[viewerKey] = viewer; // Store reference
                    console.log(`Stored viewer with key: ${viewerKey} for building: ${buildingId}`);
                    
                    // Wait for viewer to initialize
                    let attempts = 0;
                    const maxAttempts = 30; // 3 seconds max
                    const checkInitialized = setInterval(() => {
                        attempts++;
                        console.log(`Checking Three.js viewer initialization for ${buildingId}, attempt ${attempts}, initialized: ${viewer.isInitialized}`);
                        
                        if (viewer.isInitialized) {
                            clearInterval(checkInitialized);
                            
                            console.log(`Three.js viewer initialized for ${buildingId}, loading building...`);
                            
                            // Find and update loading message (don't clear container - it has the canvas!)
                            let loadingMsg = viewerEl.querySelector(`#loading-msg-${containerId}`);
                            if (!loadingMsg) {
                                loadingMsg = viewerEl.querySelector('div[style*="padding: 20px"]');
                            }
                            if (loadingMsg) {
                                loadingMsg.textContent = 'Loading building...';
                            }
                            
                            // Load the building
                            try {
                                console.log(`Calling loadBuilding for ${buildingId}`);
                                viewer.loadBuilding(minimalCityJSON);
                                
                                // Wait a moment for rendering
                                setTimeout(() => {
                                    // Update ID display
                                    if (idEl) {
                                        idEl.textContent = `${viewerType === 'candidate' ? 'Candidate' : 'Index'}: ${buildingId}`;
                                    }
                                    
                                    // Remove loading message (but keep the canvas!)
                                    if (loadingMsg && loadingMsg.parentNode) {
                                        loadingMsg.parentNode.removeChild(loadingMsg);
                                    }
                                    
                                    console.log(`Successfully loaded building ${buildingId} in Three.js viewer`);
                                    
                                    // Call completion callback
                                    if (onComplete) {
                                        setTimeout(onComplete, 100);
                                    }
                                }, 500); // Increased delay to ensure building is rendered
                            } catch (loadError) {
                                console.error(`Error loading building in Three.js viewer:`, loadError);
                                console.error('Error stack:', loadError.stack);
                                if (loadingMsg) {
                                    loadingMsg.textContent = `Error: ${loadError.message}`;
                                    loadingMsg.style.color = '#dc3545';
                                } else {
                                    const errorDiv = document.createElement('div');
                                    errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10;';
                                    errorDiv.textContent = `Error: ${loadError.message}`;
                                    viewerEl.appendChild(errorDiv);
                                }
                                if (onComplete) onComplete();
                            }
                        } else if (attempts >= maxAttempts) {
                            clearInterval(checkInitialized);
                            console.error(`Three.js viewer initialization timeout for ${containerId}`);
                            let loadingMsg = viewerEl.querySelector(`#loading-msg-${containerId}`);
                            if (loadingMsg) {
                                loadingMsg.textContent = 'Viewer initialization timeout. Check console for errors.';
                                loadingMsg.style.color = '#dc3545';
                            } else {
                                const errorDiv = document.createElement('div');
                                errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10;';
                                errorDiv.textContent = 'Viewer initialization timeout. Check console for errors.';
                                viewerEl.appendChild(errorDiv);
                            }
                            if (onComplete) onComplete();
                        }
                    }, 100);
                } catch (error) {
                    console.error(`Error creating Three.js viewer for ${containerId}:`, error);
                    console.error('Error stack:', error.stack);
                    const errorDiv = document.createElement('div');
                    errorDiv.style.cssText = 'padding: 20px; text-align: center; color: #dc3545; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10;';
                    errorDiv.textContent = `Error: ${error.message}`;
                    viewerEl.appendChild(errorDiv);
                    if (onComplete) onComplete();
                }
            }, 100);
            };
            
            // Start checking for Three.js
            checkThreeJS();
        })
        .catch(error => {
            console.error(`Error loading single building ${buildingId} from ${filePath}:`, error);
            viewerEl.innerHTML = `<div style="padding: 20px; text-align: center; color: #dc3545;">Error: ${error.message}</div>`;
            if (onComplete) onComplete();
        });
}


var _lastCleanupTime = 0;
function cleanupComparisonViewers() {
    var now = performance.now();
    if (now - _lastCleanupTime > 500) {
        console.log('Cleaning up old comparison viewer instances');
    }
    _lastCleanupTime = now;
    
    // Dispose candidate viewer
    if (window['comparison-viewer-candidate']) {
        try {
            const viewer = window['comparison-viewer-candidate'];
            if (viewer.dispose) {
                viewer.dispose();
            }
            delete window['comparison-viewer-candidate'];
        } catch (e) {
            console.warn('Error disposing candidate viewer:', e);
        }
    }
    
    // Dispose pair viewers (both old 'pair' key and new 'pair-{index}' keys)
    for (let i = 0; i < 10; i++) {
        const viewerKey = `comparison-viewer-pair-${i}`;
        if (window[viewerKey]) {
            try {
                const viewer = window[viewerKey];
                if (viewer.dispose) {
                    viewer.dispose();
                }
                delete window[viewerKey];
            } catch (e) {
                console.warn(`Error disposing pair viewer ${i}:`, e);
            }
        }
    }
    
    // Also try to dispose any viewer stored with 'pair' key (old format)
    if (window['comparison-viewer-pair']) {
        try {
            const viewer = window['comparison-viewer-pair'];
            if (viewer.dispose) {
                viewer.dispose();
            }
            delete window['comparison-viewer-pair'];
        } catch (e) {
            console.warn('Error disposing pair viewer:', e);
        }
    }
    
    // Clean up any other comparison viewer keys
    Object.keys(window).forEach(key => {
        if (key.startsWith('comparison-viewer-') && window[key] && typeof window[key] === 'object' && window[key].dispose) {
            try {
                window[key].dispose();
                delete window[key];
            } catch (e) {
                console.warn(`Error disposing viewer ${key}:`, e);
            }
        }
    });
}

// Show classifier results — carousel version with user-guess comparison
function showClassifierResultsInComparisonWindow(candidateBuildingId, pairs, userGuess) {
    const pairsToShow = pairs.slice(0, 3);
    const decisionThreshold = 0.5;
    const thresholdPct = `${(decisionThreshold * 100).toFixed(0)}%`;
    const compWin = document.getElementById('bkafi-comparison-window');
    if (compWin) compWin.setAttribute('data-revealed', '1');

    // ── compute model & truth indices ─────────────────────────────────────────
    let modelPickIdx = null;
    let modelPickConfidence = -Infinity;
    let truMatchIdx  = null;
    pairsToShow.forEach((pair, i) => {
        const pred = pair.prediction !== undefined ? pair.prediction : (pair.confidence > 0.5 ? 1 : 0);
        const tl   = pair.true_label !== undefined && pair.true_label !== null ? pair.true_label : null;
        const conf = typeof pair.confidence === 'number' ? pair.confidence : -Infinity;
        if (pred === 1 && (modelPickIdx === null || conf > modelPickConfidence)) {
            modelPickIdx = i;
            modelPickConfidence = conf;
        }
        if (tl === 1 && truMatchIdx === null)  truMatchIdx = i;
    });

    // Disable the pick button now
    const pickBtn = document.getElementById('carousel-pick-btn');
    if (pickBtn) { pickBtn.disabled = true; pickBtn.textContent = 'Pick This Match'; }

    // Apply result styling to the carousel card (for whatever is currently shown)
    if (compWin && compWin._applyRevealToCurrent) compWin._applyRevealToCurrent();
    // Re-render the current slide's label so the NN ⭐ + pool chip appear now
    // that data-revealed='1' is set.
    if (compWin && compWin._refreshCarouselLabels) compWin._refreshCarouselLabels();

    // ── score banner + details below both viewers ─────────────────────────────
    const classifierSection = document.getElementById('comparison-classifier-section');
    if (!classifierSection) return;
    classifierSection.style.display = 'flex';
    classifierSection.innerHTML = '';

    // Score banner
    if (userGuess !== null) {
        let bannerClass, bannerHtml;
        if (truMatchIdx !== null) {
            if (userGuess === truMatchIdx) {
                bannerClass = 'correct';
                if (modelPickIdx !== null && modelPickIdx !== truMatchIdx) {
                    const modelConfText = Number.isFinite(modelPickConfidence)
                        ? ` (${(modelPickConfidence * 100).toFixed(0)}% confidence)`
                        : '';
                    bannerHtml = `Correct. Option ${userGuess + 1} is the true match. The AI's top-confidence match was Option ${modelPickIdx + 1}${modelConfText}.`;
                } else if (modelPickIdx === null) {
                    bannerHtml = `Correct. Option ${userGuess + 1} is the true match, and the AI did not identify a match.`;
                } else {
                    bannerHtml = `Correct! Option ${userGuess + 1} is the true match.`;
                }
            } else {
                bannerClass = 'incorrect';
                bannerHtml = `The true match is Option ${truMatchIdx + 1}. You picked Option ${userGuess + 1}.`;
            }
        } else if (modelPickIdx !== null && userGuess === modelPickIdx) {
            // No true match exists — both user and model picked the same option, but it's a false positive
            bannerClass = 'incorrect';
            bannerHtml = `You agreed with the model (both picked Option ${userGuess + 1}), but none of the pairs is the true match, this is a <strong>False Positive</strong>.`;
        } else if (modelPickIdx !== null) {
            // No true match exists — model picked one option, user picked a different one; both are wrong
            bannerClass = 'incorrect';
            bannerHtml = `The model predicted Option ${modelPickIdx + 1} (a False Positive). You picked Option ${userGuess + 1}. Neither is the true match.`;
        } else {
            bannerClass = 'no-pick';
            bannerHtml = `The model found no match. You picked Option ${userGuess + 1}.`;
        }
        const banner = document.createElement('div');
        banner.className = `game-score-banner ${bannerClass}`;
        banner.innerHTML = bannerHtml;
        classifierSection.appendChild(banner);
    }

    // Per-pair summary table
    const table = document.createElement('div');
    table.style.cssText = 'font-size:12px;width:100%;';

    // Find the spatial-NN (final pipeline) pick — it's the slide tagged
    // _nnInjected by the View-Pairs onclick. After Step 4 this IS the
    // pipeline's final match for this cand. Before Step 4 (hybrid mode) the
    // tag is absent and we fall back to the model-pick column only.
    const finalIdx = pairsToShow.findIndex(p => p && p._nnInjected);
    const finalDistance = (finalIdx >= 0 && pairsToShow[finalIdx].distance_m != null)
        ? Number(pairsToShow[finalIdx].distance_m).toFixed(2) + ' m' : null;

    // Banner naming the pipeline's final pick. Decision is purely distance-based:
    // the 1-NN is the final pick; if it's beyond the cutoff distance the
    // spatial step considers it unreliable, otherwise it's confirmed.
    if (finalIdx >= 0) {
        const finalPair = pairsToShow[finalIdx];
        const ai        = window._lastAlignmentInfo || {};
        const cutoff    = (ai.cutoff_m != null) ? Number(ai.cutoff_m) : 7.0;
        const d         = (finalPair.distance_m != null) ? Number(finalPair.distance_m) : null;
        const dTxt      = (d != null) ? `${d.toFixed(2)} m` : 'n/a';
        const within    = (d != null) && (d <= cutoff);
        const banner    = document.createElement('div');
        banner.className = `final-match-banner ${within ? 'accepted' : 'rejected'}`;
        const detail = within
            ? `Distance <strong>${dTxt}</strong> — within the ${cutoff} m cutoff distance, match is confirmed.`
            : `Distance <strong>${dTxt}</strong> — beyond the ${cutoff} m cutoff distance, the nearest building is too far to be a reliable match.`;
        banner.innerHTML = `⭐ <strong>Pipeline's final pick:</strong> Option ${finalIdx + 1} — Index <code>${finalPair.index_id}</code>. ${detail}`;
        classifierSection.appendChild(banner);
    }

    // Header row — adds Distance + Final Pick columns when post-align data
    // is present. The pipeline's final pick is decided purely by distance
    // (the 1-NN within the cutoff), so we surface the distance directly
    // rather than the derived final_score.
    const cutoffForLabel = (window._lastAlignmentInfo && window._lastAlignmentInfo.cutoff_m) || 7.0;
    const gridCols = 'display:grid;grid-template-columns:0.9fr 1.5fr 0.8fr 0.9fr 1.0fr 0.9fr;gap:6px;';
    const header = document.createElement('div');
    header.style.cssText = `${gridCols}padding:3px 2px 5px;border-bottom:2px solid #e2e8f0;font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;`;
    header.innerHTML = `<span>Option</span><span>Model Prediction (threshold: ${thresholdPct})</span><span>Confidence</span><span>True Label</span><span title="Post-alignment distance between this option and the candidate, in metres.">Distance</span><span>Final Pick</span>`;
    table.appendChild(header);

    pairsToShow.forEach((pair, i) => {
        const pred = pair.prediction !== undefined ? pair.prediction : (pair.confidence > 0.5 ? 1 : 0);
        const tl   = pair.true_label !== undefined && pair.true_label !== null ? pair.true_label : null;
        const predColor = pred === 1 ? '#22c55e' : '#94a3b8';
        const tlColor   = tl === 1 ? '#22c55e' : (tl === 0 ? '#f97316' : '#94a3b8');
        const confPct   = pair.confidence !== undefined ? `${(pair.confidence * 100).toFixed(0)}%` : '—';
        const isUserPick = userGuess === i ? ' (Your Pick)' : '';
        const isFinal = (i === finalIdx);
        const rowBg = isFinal ? 'background:#fffbe6;' : '';
        const row = document.createElement('div');
        row.style.cssText = `${gridCols}align-items:center;padding:5px 4px;border-bottom:1px solid #f0f0f0;cursor:pointer;${rowBg}`;
        const predictionLabel = pred === 1
            ? `Match (>= ${thresholdPct})`
            : `No (< ${thresholdPct})`;
        // Distance cell — the underlying number the spatial step ranks on.
        let distanceCell;
        if (pair.distance_m == null) {
            distanceCell = '<span style="color:#cbd5e1">—</span>';
        } else {
            const dNum = Number(pair.distance_m);
            const within = dNum <= cutoffForLabel;
            const colour = within ? '#15803d' : '#b45309';
            distanceCell = `<span style="color:${colour}" title="${within ? 'Within' : 'Beyond'} the ${cutoffForLabel} m cutoff distance"><strong>${dNum.toFixed(2)} m</strong></span>`;
        }
        // Final Pick decision = distance against cutoff (same rule the banner
        // uses). Don't read predicted_label here: it's null for the NN-only
        // pick when it wasn't classified by XGBoost, which would wrongly
        // render as "too far" even when the distance is well within cutoff.
        let finalCell;
        if (!isFinal) {
            finalCell = '<span style="color:#cbd5e1">—</span>';
        } else if (pair.distance_m != null && Number(pair.distance_m) <= cutoffForLabel) {
            finalCell = `<span title="Pipeline's final pick — within ${cutoffForLabel} m cutoff distance">⭐ confirmed</span>`;
        } else {
            finalCell = `<span title="Pipeline's final pick — beyond ${cutoffForLabel} m cutoff distance">⭐ too far</span>`;
        }
        row.innerHTML = `
            <span style="font-weight:600;color:#555;">Opt ${i + 1}${isUserPick}</span>
            <span>Model: <strong style="color:${predColor}">${predictionLabel}</strong></span>
            <span>${confPct}</span>
            <span>True: <strong style="color:${tlColor}">${tl === 1 ? 'Match' : tl === 0 ? 'No' : '—'}</strong></span>
            <span>${distanceCell}</span>
            <span>${finalCell}</span>`;
        // clicking a row navigates the carousel to that option
        row.addEventListener('click', () => { if (compWin && compWin._goTo) compWin._goTo(i); });
        row.addEventListener('mouseenter', () => row.style.background = '#f8fafc');
        row.addEventListener('mouseleave', () => row.style.background = '');
        table.appendChild(row);
    });
    classifierSection.appendChild(table);

    // ── "Show Matches on Map" button ──────────────────────────────────────────
    const backBtn = document.createElement('button');
    backBtn.className = 'back-to-map-btn';
    backBtn.innerHTML = 'Show Matches on Map';
    backBtn.addEventListener('click', () => {
        hideBeacon();
        // Place markers FIRST (before closing, so viewer is still accessible)
        if (window.viewer && window.viewer.addBuildingMarkers) {
            removeTutorialMarker();   // pair markers replace the "Example building" label
            const pcd = compWin ? (compWin._pairCityData || []) : [];
            window.viewer.addBuildingMarkers(candidateBuildingId, pairsToShow, pcd);
        }
        // Close comparison window and building properties without clearing the markers we just added
        window._keepBuildingMarkers = true;
        closeBkafiComparisonWindow();
        window._keepBuildingMarkers = false;
        closeBuildingProperties();
        // Fly to candidate building so markers are visible
        if (window.viewer) {
            const candidateEntity = window.viewer.findEntityByBuildingId(candidateBuildingId);
            if (candidateEntity) {
                window.viewer.viewer.flyTo(candidateEntity, {
                    offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), 300)
                });
            }
        }
    });
    classifierSection.appendChild(backBtn);
}

// Close BKAFI comparison window
function closeBkafiComparisonWindow() {
    const comparisonWindow = document.getElementById('bkafi-comparison-window');
    const overlay = document.getElementById('comparison-overlay');
    
    if (comparisonWindow) {
        comparisonWindow.style.display = 'none';
    }
    
    if (overlay) {
        overlay.classList.remove('active');
    }

    // Remove map markers (skip if "Show Matches on Map" just placed them)
    if (!window._keepBuildingMarkers && window.viewer && window.viewer.clearBuildingMarkers) {
        window.viewer.clearBuildingMarkers();
    }
    
    // Clean up viewers when closing
    cleanupComparisonViewers();
    
    // Clear viewer elements
    const candidateViewerEl = document.getElementById('comparison-viewer-candidate');
    const pairsViewersEl = document.getElementById('comparison-pairs-viewers');
    
    if (candidateViewerEl) {
        candidateViewerEl.innerHTML = '';
    }
    if (pairsViewersEl) {
        pairsViewersEl.innerHTML = '';
    }
    
    // Reset classifier section
    const classifierSection = document.getElementById('comparison-classifier-section');
    const classifierResults = document.getElementById('classifier-results');
    const showClassifierBtn = document.getElementById('show-classifier-results-btn');
    
    if (classifierSection) {
        classifierSection.style.display = 'none';
    }
    if (classifierResults) {
        classifierResults.innerHTML = '';
    }
    if (showClassifierBtn) {
        showClassifierBtn.disabled = false;
        showClassifierBtn.textContent = 'Show Classifier Results';
    }
}

// View results (Step 3) - Show summary instead of individual matches
function viewResults() {
    if (!pipelineState.step2Completed) {
        alert('Please complete Geometric Blocking first.');
        return;
    }
    
    console.log('Loading classifier results summary');
    
    const stepBtn = document.getElementById('step-btn-3');
    stepBtn.textContent = 'Loading...';
    stepBtn.disabled = true;
    
    // Show loading overlay
    showLoading('Loading classifier results and updating colors...');
    
    const targetFiles = getSelectedSummaryFiles();
    if (targetFiles.length === 0) {
        hideLoading();
        alert('Please select at least one file.');
        stepBtn.textContent = 'Run Classifier';
        stepBtn.disabled = false;
        return;
    }

    const handleClassifierComplete = () => {
        // After the live pipeline finishes the matching stage, fetch per-file
        // summaries the same way the legacy code did. The Celery task has
        // populated the metrics file in results_demo/demo_inference/.
        Promise.all(
            targetFiles.map((filePath) =>
                fetch(`/api/classifier/summary?file=${encodeURIComponent(filePath)}`)
                    .then(response => response.json())
                    .then(data => {
                        if (data.error) throw new Error(data.error);
                        return { filePath, data };
                    })
            )
        )
            .then(results => {
                showLoading('Updating building colors...');
                pipelineState.step3Completed = true;
                updatePipelineUI();
                updateViewerLegend();
                advanceTutorialForPipelineAction('viewResults');

                // Enable Step 4 (Spatial Alignment) — the headline summary now
                // lives there, since Step 4 is the last stage.
                const step4Btn = document.getElementById('step-btn-4');
                if (step4Btn) step4Btn.disabled = false;

                updateBuildingColorsForStage3(true, () => {
                    hideLoading();
                });
                stepBtn.textContent = 'Completed';
                stepBtn.style.background = '#28a745';
            })
            .catch(error => {
                console.error('Error loading summary:', error);
                hideLoading();
                alert('Error loading classifier results summary: ' + error.message);
                stepBtn.textContent = 'Run Classifier';
                stepBtn.disabled = false;
            });
    };

    const handleClassifierError = (errorMessage) => {
        console.error('Classifier pipeline error:', errorMessage);
        hideLoading();
        alert('Error running classifier: ' + errorMessage);
        stepBtn.textContent = 'Run Classifier';
        stepBtn.disabled = false;
    };

    window.PipelineRunner.start('matching', {
        onProgress: ({ sub_stage, message, elapsed_s }) => {
            const label = sub_stage ? `[${sub_stage}] ${message || ''}` : (message || 'Running…');
            showLoading(`${label} (${elapsed_s || 0}s)`);
        },
        onComplete: handleClassifierComplete,
        onError:    ({ error }) => handleClassifierError(error),
    }).catch(err => handleClassifierError(err.message));
}

// Update pipeline UI with status indicators and step accent colors (orange = step 1, yellow = step 2 when relevant)
function updatePipelineUI() {
    // Step 1: orange when current (not completed), green when completed
    const step1El = document.getElementById('step-1');
    const step1Status = step1El ? step1El.querySelector('.step-status') : null;
    if (step1El) {
        step1El.classList.remove('step-current-orange', 'step-completed');
        if (pipelineState.step1Completed) {
            step1El.classList.add('step-completed');
            if (step1Status) { step1Status.innerHTML = '✓'; step1Status.className = 'step-status completed'; }
        } else {
            step1El.classList.add('step-current-orange');
            if (step1Status) { step1Status.innerHTML = ''; step1Status.className = 'step-status'; }
        }
    }

    // Step 2: yellow when current (step 1 done, step 2 not done), green when completed
    const step2El = document.getElementById('step-2');
    const step2Status = step2El ? step2El.querySelector('.step-status') : null;
    if (step2El) {
        step2El.classList.remove('step-current-yellow', 'step-completed');
        if (pipelineState.step2Completed) {
            step2El.classList.add('step-completed');
            if (step2Status) { step2Status.innerHTML = '✓'; step2Status.className = 'step-status completed'; }
        } else if (pipelineState.step1Completed) {
            step2El.classList.add('step-current-yellow');
            if (step2Status) { step2Status.innerHTML = ''; step2Status.className = 'step-status'; }
        } else if (step2Status) {
            step2Status.innerHTML = ''; step2Status.className = 'step-status';
        }
    }

    // Step 3: default blue when current, green when completed
    const step3El = document.getElementById('step-3');
    const step3Status = step3El ? step3El.querySelector('.step-status') : null;
    if (step3El) {
        step3El.classList.remove('step-completed');
        if (pipelineState.step3Completed) {
            step3El.classList.add('step-completed');
            if (step3Status) { step3Status.innerHTML = '✓'; step3Status.className = 'step-status completed'; }
        } else if (step3Status) {
            step3Status.innerHTML = ''; step3Status.className = 'step-status';
        }
    }

    // Step 4: green when completed.
    const step4El = document.getElementById('step-4');
    const step4Status = step4El ? step4El.querySelector('.step-status') : null;
    if (step4El) {
        step4El.classList.remove('step-completed');
        if (pipelineState.step4Completed) {
            step4El.classList.add('step-completed');
            if (step4Status) { step4Status.innerHTML = '✓'; step4Status.className = 'step-status completed'; }
        } else if (step4Status) {
            step4Status.innerHTML = ''; step4Status.className = 'step-status';
        }
    }
}

// Helper function to extract numeric ID from building ID
function extractNumericId(buildingId) {
    const match = buildingId.match(/(\d{10,})/);
    return match ? match[1] : buildingId;
}

// Show loading overlay with message
function showLoading(message = 'Processing...') {
    const overlay = document.getElementById('loading-overlay');
    const messageEl = document.getElementById('loading-message');
    if (overlay && messageEl) {
        messageEl.textContent = message;
        overlay.style.display = 'flex';
    }
}

// Hide loading overlay
function hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

// Poll job status until completion
function pollJobStatus(jobId, onSuccess, onError, options = {}) {
    const intervalMs = options.intervalMs || 2000;
    const maxAttempts = options.maxAttempts || 180;
    let attempts = 0;

    const poll = () => {
        attempts += 1;
        fetch(`/api/jobs/${jobId}`)
            .then(response => response.json())
            .then(data => {
                if (data.status === 'SUCCESS') {
                    try { onSuccess(data.result); } catch (e) {
                        console.error('pollJobStatus: onSuccess threw:', e);
                        onError(e.message || 'Error processing job result');
                    }
                    return;
                }
                if (data.status === 'FAILURE') {
                    onError(data.error || 'Job failed');
                    return;
                }
                if (attempts >= maxAttempts) {
                    onError('Job timed out');
                    return;
                }
                setTimeout(poll, intervalMs);
            })
            .catch(err => {
                if (attempts >= maxAttempts) {
                    onError(err.message || 'Job timed out');
                    return;
                }
                setTimeout(poll, intervalMs);
            });
    };

    poll();
}

// Update the selected building's color based on current pipeline state
function updateSelectedBuildingColor() {
    if (!selectedBuildingId || !selectedFile || !window.viewer) {
        return;
    }
    
    // Use cached status if available
    getBuildingStatus(false)
        .then(data => {
            const { statusMap } = buildOptimizedStatusMap(data);
            const status = findBuildingStatus(selectedBuildingId, statusMap);
            
            if (status) {
                let colorName = 'blue'; // Default
                
                // Determine color based on pipeline stage
                if (pipelineState.step3Completed && status.match_status) {
                    if (status.match_status === 'true_match') {
                        colorName = 'green';
                    } else if (status.match_status === 'false_positive') {
                        colorName = 'red';
                    } else if (status.match_status === 'no_match') {
                        colorName = 'darkgray';
                    }
                } else if (pipelineState.step2Completed && status.has_pairs) {
                    colorName = 'yellow';
                } else if (pipelineState.step1Completed && status.has_features) {
                    colorName = 'orange';
                }
                
                // Update the building color
                window.viewer.updateBuildingColor(selectedBuildingId, colorName);
                console.log(`Updated selected building ${selectedBuildingId} to color: ${colorName}`);
            } else {
                console.warn(`No status found for selected building: ${selectedBuildingId}`);
            }
        })
        .catch(error => {
            console.error('Error updating selected building color:', error);
        });
}

// Timeout for building status fetch (ms) — prevents spinner hanging if server is slow
var BUILDING_STATUS_FETCH_TIMEOUT_MS = 30000;

// Helper function to get building status (with caching)
function getBuildingStatus(forceRefresh = false) {
    return new Promise((resolve, reject) => {
        // Use cache if available and not forcing refresh
        if (buildingStatusCache && !forceRefresh) {
            resolve(buildingStatusCache);
            return;
        }
        
        if (!selectedFile) {
            reject(new Error('No file selected'));
            return;
        }
        
        const url = `/api/buildings/status?file=${encodeURIComponent(selectedFile)}&_t=${Date.now()}`;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), BUILDING_STATUS_FETCH_TIMEOUT_MS);
        
        fetch(url, { signal: controller.signal, cache: 'no-store' })
            .then(response => response.json())
            .then(data => {
                clearTimeout(timeoutId);
                if (data.error) {
                    reject(new Error(data.error));
                    return;
                }
                buildingStatusCache = data;
                _cachedOptimizedStatusMap = null;
                resolve(data);
            })
            .catch(err => {
                clearTimeout(timeoutId);
                if (err.name === 'AbortError') {
                    reject(new Error('Building status request timed out. Try again.'));
                } else {
                    reject(err);
                }
            });
    });
}

// Pre-compute status map with all ID variations for fast lookups (cached)
function buildOptimizedStatusMap(data) {
    if (_cachedOptimizedStatusMap) return _cachedOptimizedStatusMap;
    
    const statusMap = {};
    const numericMap = {};
    
    Object.entries(data.buildings).forEach(([buildingId, status]) => {
        const numericId = extractNumericId(buildingId);
        
        // Store by original ID
        statusMap[buildingId] = status;
        
        // Store by numeric ID
        if (numericId !== buildingId) {
            statusMap[numericId] = status;
            numericMap[numericId] = status;
        }
        
        // Store variations with bag_ prefix
        if (buildingId.startsWith('bag_')) {
            const withoutPrefix = buildingId.replace('bag_', '');
            statusMap[withoutPrefix] = status;
        } else {
            statusMap[`bag_${buildingId}`] = status;
        }
    });
    
    _cachedOptimizedStatusMap = { statusMap, numericMap };
    return _cachedOptimizedStatusMap;
}

// Helper function to match building IDs (optimized with pre-computed map)
function findBuildingStatus(viewerBuildingId, statusMap) {
    // Fast direct lookup
    let status = statusMap[viewerBuildingId];
    if (status) return status;
    
    // Try numeric ID
    const numericId = extractNumericId(viewerBuildingId);
    if (numericId !== viewerBuildingId) {
        status = statusMap[numericId];
        if (status) return status;
    }
    
    // Try bag_ prefix variations
    if (viewerBuildingId.startsWith('bag_')) {
        status = statusMap[viewerBuildingId.replace('bag_', '')];
        if (status) return status;
    } else {
        status = statusMap[`bag_${viewerBuildingId}`];
        if (status) return status;
    }
    
    return null;
}

// Max time to wait for Stage 1 color update before still calling onComplete (ms)
var STAGE1_COLOR_UPDATE_TIMEOUT_MS = 40000;

// Update building colors based on pipeline stages
function updateBuildingColorsForStage1(forceRefresh = false, onComplete = null) {
    // Stage 1: Geometric Featurization
    // Buildings with features -> orange, without -> blue
    if (!selectedFile || !window.viewer) {
        console.warn('Cannot update colors: no file selected or viewer not available');
        if (onComplete) onComplete();
        return Promise.resolve();
    }
    
    if (_colorUpdateTicket) _colorUpdateTicket.cancelled = true;
    var ticket = { cancelled: false };
    _colorUpdateTicket = ticket;
    
    var startTime = performance.now();
    var completed = false;
    var done = function () {
        if (completed) return;
        completed = true;
        if (onComplete) onComplete();
    };
    
    var chain = getBuildingStatus(forceRefresh)
        .then(function (data) {
            if (ticket.cancelled) { done(); return Promise.resolve(); }
            var buildingColors = {};
            var statusMap = buildOptimizedStatusMap(data).statusMap;
            if (window.viewer && window.viewer.buildingEntities) {
                window.viewer.buildingEntities.forEach(function (entities, viewerBuildingId) {
                    var status = findBuildingStatus(viewerBuildingId, statusMap);
                    buildingColors[viewerBuildingId] = (status && status.has_features) ? 'orange' : 'blue';
                });
            }
            if (ticket.cancelled) { done(); return Promise.resolve(); }
            if (Object.keys(buildingColors).length > 0) {
                return window.viewer.updateBuildingColors(buildingColors, selectedFile).then(function () {
                    if (ticket.cancelled) { done(); return; }
                    console.log('Updated colors for ' + Object.keys(buildingColors).length + ' buildings (Stage 1) in ' + (performance.now() - startTime).toFixed(2) + 'ms');
                    done();
                });
            }
            done();
            return Promise.resolve();
        })
        .catch(function (error) {
            console.error('Error updating building colors for Stage 1:', error);
            done();
        });
    
    // Ensure onComplete is called even if getBuildingStatus or updateBuildingColors hangs
    Promise.race([
        chain,
        new Promise(function (resolve) {
            setTimeout(function () {
                if (!completed) console.warn('Stage 1 color update: timeout after ' + STAGE1_COLOR_UPDATE_TIMEOUT_MS + 'ms');
                done();
                resolve();
            }, STAGE1_COLOR_UPDATE_TIMEOUT_MS);
        })
    ]);
    
    return chain;
}

function updateBuildingColorsForStage2(forceRefresh = false, onComplete = null) {
    // Stage 2: BKAFI Blocking
    // Buildings with pairs -> yellow
    if (!selectedFile || !window.viewer) {
        console.warn('Cannot update colors: no file selected or viewer not available');
        if (onComplete) onComplete();
        return;
    }
    
    if (_colorUpdateTicket) _colorUpdateTicket.cancelled = true;
    const ticket = { cancelled: false };
    _colorUpdateTicket = ticket;
    
    const startTime = performance.now();
    
    return getBuildingStatus(forceRefresh)
        .then(data => {
            if (ticket.cancelled) { if (onComplete) onComplete(); return Promise.resolve(); }
            const buildingColors = {};
            const { statusMap } = buildOptimizedStatusMap(data);
            
            // Update colors for ALL buildings in the viewer (including selected building)
            if (window.viewer && window.viewer.buildingEntities) {
                window.viewer.buildingEntities.forEach((entities, viewerBuildingId) => {
                    const status = findBuildingStatus(viewerBuildingId, statusMap);
                    if (status) {
                        if (status.has_pairs) {
                            buildingColors[viewerBuildingId] = 'yellow';
                        } else {
                            // Keep previous color (orange if has features, blue if not)
                            buildingColors[viewerBuildingId] = status.has_features ? 'orange' : 'blue';
                        }
                    } else {
                        buildingColors[viewerBuildingId] = 'blue'; // Default
                    }
                });
            }
            
            if (ticket.cancelled) { if (onComplete) onComplete(); return Promise.resolve(); }
            if (Object.keys(buildingColors).length > 0) {
                return window.viewer.updateBuildingColors(buildingColors, selectedFile).then(() => {
                    if (ticket.cancelled) { if (onComplete) onComplete(); return; }
                    const endTime = performance.now();
                    console.log(`Updated colors for ${Object.keys(buildingColors).length} buildings (Stage 2) in ${(endTime - startTime).toFixed(2)}ms`);
                    if (onComplete) onComplete();
                });
            } else {
                if (onComplete) onComplete();
                return Promise.resolve();
            }
        })
        .catch(error => {
            console.error('Error updating building colors for Stage 2:', error);
            if (onComplete) onComplete();
        });
}

function updateBuildingColorsForStage3(forceRefresh = false, onComplete = null) {
    // Stage 3: Matching Classifier
    // True match -> green, false positive -> red, no match -> dark gray
    if (!selectedFile || !window.viewer) {
        console.warn('Cannot update colors: no file selected or viewer not available');
        if (onComplete) onComplete();
        return;
    }
    
    if (_colorUpdateTicket) _colorUpdateTicket.cancelled = true;
    const ticket = { cancelled: false };
    _colorUpdateTicket = ticket;
    
    const startTime = performance.now();
    
    return getBuildingStatus(forceRefresh)
        .then(data => {
            if (ticket.cancelled) { if (onComplete) onComplete(); return Promise.resolve(); }
            const buildingColors = {};
            const { statusMap } = buildOptimizedStatusMap(data);
            
            if (window.viewer && window.viewer.buildingEntities) {
                window.viewer.buildingEntities.forEach((entities, viewerBuildingId) => {
                    const status = findBuildingStatus(viewerBuildingId, statusMap);
                    if (status) {
                        if (status.match_status === 'true_match') {
                            buildingColors[viewerBuildingId] = 'green';
                        } else if (status.match_status === 'false_positive') {
                            buildingColors[viewerBuildingId] = 'red';
                        } else if (status.match_status === 'no_match') {
                            buildingColors[viewerBuildingId] = 'darkgray';
                        } else {
                            buildingColors[viewerBuildingId] = 'darkgray';
                        }
                    } else {
                        buildingColors[viewerBuildingId] = 'blue';
                    }
                });
            }
            
            if (ticket.cancelled) { if (onComplete) onComplete(); return Promise.resolve(); }
            if (Object.keys(buildingColors).length > 0) {
                return window.viewer.updateBuildingColors(buildingColors, selectedFile).then(() => {
                    if (ticket.cancelled) { if (onComplete) onComplete(); return; }
                    const endTime = performance.now();
                    console.log(`Updated colors for ${Object.keys(buildingColors).length} buildings (Stage 3) in ${(endTime - startTime).toFixed(2)}ms`);
                    if (onComplete) onComplete();
                });
            } else {
                if (onComplete) onComplete();
                return Promise.resolve();
            }
        })
        .catch(error => {
            console.error('Error updating building colors for Stage 3:', error);
            if (onComplete) onComplete();
        });
}

// Viewer controls
function resetCamera() {
    console.log('Resetting camera');
    if (window.viewer && window.viewer.resetCamera) {
        window.viewer.resetCamera();
    }
}

function toggleFullscreen() {
    console.log('Toggling fullscreen');
    if (window.viewer && window.viewer.toggleFullscreen) {
        window.viewer.toggleFullscreen();
    }
}

// Reset viewer camera to initial position after first load
function resetViewerCamera() {
    if (window.viewer && window.viewer.resetCamera) {
        window.viewer.resetCamera();
    }
}

function zoomInViewer() {
    if (window.viewer && window.viewer.zoomIn) {
        window.viewer.zoomIn();
    }
}

function zoomOutViewer() {
    if (window.viewer && window.viewer.zoomOut) {
        window.viewer.zoomOut();
    }
}

function rotateViewerLeft() {
    if (window.viewer && window.viewer.rotateLeft) {
        window.viewer.rotateLeft();
    }
}

function rotateViewerRight() {
    if (window.viewer && window.viewer.rotateRight) {
        window.viewer.rotateRight();
    }
}

function tiltViewerUp() {
    if (window.viewer && window.viewer.tiltUp) {
        window.viewer.tiltUp();
    }
}

function tiltViewerDown() {
    if (window.viewer && window.viewer.tiltDown) {
        window.viewer.tiltDown();
    }
}

function setBasemap(mode) {
    if (window.viewer && window.viewer.setBasemap) {
        window.viewer.setBasemap(mode);
    }
}

// Show building matches window
function showBuildingMatches(buildingId, buildingName, matches) {
    const matchesWindow = document.getElementById('matches-window');
    const buildingNameEl = document.getElementById('building-name');
    const buildingIdEl = document.getElementById('building-id');
    const matchesList = document.getElementById('matches-list');
    
    if (!matchesWindow || !buildingNameEl || !buildingIdEl || !matchesList) {
        console.error('Matches window elements not found');
        return;
    }
    
    // Update building info
    buildingNameEl.textContent = buildingName || 'Building';
    buildingIdEl.textContent = `ID: ${buildingId}`;
    
    // Clear and populate matches
    matchesList.innerHTML = '';
    
    if (matches && matches.length > 0) {
        matches.forEach((match, index) => {
            const matchItem = document.createElement('div');
            matchItem.className = 'match-item';
            matchItem.innerHTML = `
                <div class="match-header">
                    <span class="match-source">${match.source || 'Source'}</span>
                    <span class="match-confidence">${((match.confidence || match.similarity || 0) * 100).toFixed(1)}%</span>
                </div>
                <div class="match-details">
                    <p><strong>ID:</strong> ${match.id || match.building_id || 'N/A'}</p>
                    ${match.similarity ? `<p><strong>Similarity:</strong> ${(match.similarity * 100).toFixed(1)}%</p>` : ''}
                    ${match.features ? `<p><strong>Features:</strong> ${match.features}</p>` : ''}
                    <button onclick="viewMatch('${match.id || match.building_id || ''}')">View in 3D</button>
                </div>
            `;
            matchesList.appendChild(matchItem);
        });
    } else {
        matchesList.innerHTML = '<p style="text-align: center; color: #666; padding: 20px;">No matches found for this building</p>';
    }
    
    // Show the window
    matchesWindow.style.display = 'block';
    
    // Add overlay (optional, for better UX)
    let overlay = document.getElementById('matches-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'matches-overlay';
        overlay.className = 'matches-overlay';
        overlay.onclick = closeMatchesWindow;
        document.body.appendChild(overlay);
    }
    overlay.classList.add('active');
}

function initSummaryHelpHandlers() {
    const badges = document.querySelectorAll('.info-badge');
    if (!badges.length) {
        return;
    }
    badges.forEach((badge) => {
        badge.addEventListener('click', (event) => {
            event.stopPropagation();
            badge.classList.toggle('active');
        });
    });
    document.addEventListener('click', () => {
        badges.forEach((badge) => badge.classList.remove('active'));
    }, { once: true });
}

// Close matches window
function closeMatchesWindow() {
    const matchesWindow = document.getElementById('matches-window');
    const overlay = document.getElementById('matches-overlay');
    
    if (matchesWindow) {
        matchesWindow.style.display = 'none';
    }
    
    if (overlay) {
        overlay.classList.remove('active');
    }
}

// View a specific match in 3D
function viewMatch(matchId) {
    console.log('Viewing match:', matchId);
    // Close matches window
    closeMatchesWindow();
    
    // TODO: Implement logic to highlight/zoom to the matched building
    // This would require loading the matched building's data and highlighting it
    if (window.viewer) {
        // You can add logic here to highlight the matched building
        alert(`Viewing match: ${matchId}\n(This feature can be extended to highlight the matched building in the 3D viewer)`);
    }
}

// Make functions globally available
window.showBuildingMatches = showBuildingMatches;
window.closeMatchesWindow = closeMatchesWindow;
window.viewMatch = viewMatch;
window.showBuildingProperties = showBuildingProperties;
window.closeBuildingProperties = closeBuildingProperties;
window.updateViewerLegend = updateViewerLegend;
window.updatePipelineUI    = updatePipelineUI;
window.setActiveFileFromViewer = setActiveFileFromViewer;
window.zoomToLayer = zoomToLayer;
// pipelineState is declared with `let` at the top of this file, which under
// strict module-script semantics does NOT auto-attach to window. alignment.js
// (separate <script>) needs to mutate `step4Completed` to drive the legend
// and the post-run colour-reapply chain — expose the actual object.
window.pipelineState = pipelineState;

// Called by the viewer after all queued layers have finished loading
window.applyViewerLayerStyles = function () {
    if (window.viewer && window.viewer.applyLayerVisualStyles) {
        window.viewer.applyLayerVisualStyles(selectedFile);
    }
    updateViewerLegend();
};