/**
 * alignment.js — Owns Step 4 ("Spatial alignment") of the demo pipeline.
 *
 * Public surface (window.AlignmentStep):
 *   run()              kick off the alignment pipeline stage via PipelineRunner.
 *                      On completion, reveal the four sub-stage controls and
 *                      auto-display 4a.
 *   setSubStage(s)     show one of '4a' | '4b' | '4c' | '4d'.
 *   prev() / next()    step through the four sub-stages.
 *   toggleAutoAdvance() flip the auto-advance timer.
 *   reset()            tear down sub-stage state (call from resetPipelineState).
 *   getActiveSubStage() → '4a'..'4d' | null
 *   reapplyCurrentSubStage() — used by setPipelineFile to re-colour after a
 *                              dataset switch.
 *
 * The stubs below set up the lifecycle and PipelineRunner integration;
 * per-sub-stage rendering is filled in by the next four commits.
 */
(function () {
  'use strict';

  const SUBSTAGES = ['4a', '4b', '4c', '4d'];
  const AUTO_ADVANCE_MS = 4500;
  const MISALIGNED_LAYER = '__alignment_misaligned__';   // virtual layer key for 4a
  const ALIGNED_LAYER    = '__alignment_aligned__';      // virtual layer key for 4c
  const MISALIGNED_URL = '/api/alignment/cityjson?stage=misaligned';
  const ALIGNED_URL    = '/api/alignment/cityjson?stage=aligned';

  let activeSubStage = null;
  let autoAdvanceTimer = null;
  let infoCardEl = null;
  let cachedAlignmentInfo = null;   // populated by run() from /api/alignment/status

  // Layers we own (we'll tear these down on reset()).
  const _ownedLayers = new Set();

  function _btn() { return document.getElementById('step-btn-4'); }
  function _wrap() { return document.getElementById('step-4-substages'); }
  function _info() { return infoCardEl || (infoCardEl = document.getElementById('step-4-info-card')); }

  function _markSubStageButton(stage) {
    const wrap = _wrap();
    if (!wrap) return;
    wrap.querySelectorAll('.substage-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.substage === stage);
    });
  }

  function _setInfoCard(html) {
    const el = _info();
    if (el) el.innerHTML = html;
  }

  function _waitForViewer() {
    return new Promise((resolve, reject) => {
      const start = Date.now();
      (function tick() {
        if (window.viewer && window.viewer.isInitialized) return resolve(window.viewer);
        if (Date.now() - start > 5000) return reject(new Error('Cesium viewer never initialised'));
        setTimeout(tick, 50);
      })();
    });
  }

  async function _loadLayerOnce(layerKey, url, source) {
    const viewer = await _waitForViewer();
    if (_ownedLayers.has(layerKey)) return viewer;   // already loaded
    return new Promise((resolve, reject) => {
      viewer.loadCityJSON(layerKey, { append: true, source, url });
      _ownedLayers.add(layerKey);
      const start = Date.now();
      // The viewer doesn't expose a load-complete event yet, so poll isLoading.
      (function tick() {
        if (!viewer.isLoading) return resolve(viewer);
        if (Date.now() - start > 60000) {
          _ownedLayers.delete(layerKey);
          return reject(new Error('CityJSON load timed out'));
        }
        setTimeout(tick, 80);
      })();
    });
  }

  async function _fetchColors(stage) {
    const res = await fetch(`/api/alignment/buildings/colors?stage=${encodeURIComponent(stage)}`);
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).error || res.statusText;
      throw new Error(`buildings/colors failed: ${detail}`);
    }
    return res.json();
  }

  // Sub-stage renderers
  async function _show4a() {
    const viewer = await _loadLayerOnce(MISALIGNED_LAYER, MISALIGNED_URL, 'A');
    const { cand_colors } = await _fetchColors('4a');
    if (typeof viewer.updateBuildingColors === 'function') {
      // The viewer's idMapping resolves bag_<id> ↔ <id>, so raw IDs work.
      await viewer.updateBuildingColors(cand_colors || {}, MISALIGNED_LAYER);
    }
    if (typeof viewer.fitCameraToBuildings === 'function') viewer.fitCameraToBuildings();
    _setInfoCard(
      `<strong>4a · Misaligned candidates</strong><br>` +
      `Candidate buildings drawn at their post-disaster positions; the rigid ` +
      `transform that RANSAC will recover has not been applied yet.`
    );
  }
  async function _show4b() {
    _setInfoCard('Sub-stage 4b (Anchors) — implementation pending.');
  }
  async function _show4c() {
    _setInfoCard('Sub-stage 4c (Transform applied) — implementation pending.');
  }
  async function _show4d() {
    _setInfoCard('Sub-stage 4d (Final matches) — implementation pending.');
  }

  const RENDERERS = { '4a': _show4a, '4b': _show4b, '4c': _show4c, '4d': _show4d };

  async function setSubStage(stage) {
    if (!SUBSTAGES.includes(stage)) return;
    activeSubStage = stage;
    _markSubStageButton(stage);
    try {
      await RENDERERS[stage]();
    } catch (e) {
      console.error(`[AlignmentStep] sub-stage ${stage} failed:`, e);
      _setInfoCard(`<span style="color:#c00;">Error in sub-stage ${stage}: ${e.message || e}</span>`);
    }
  }

  function next() {
    const i = SUBSTAGES.indexOf(activeSubStage);
    if (i < 0 || i === SUBSTAGES.length - 1) return;
    setSubStage(SUBSTAGES[i + 1]);
  }

  function prev() {
    const i = SUBSTAGES.indexOf(activeSubStage);
    if (i <= 0) return;
    setSubStage(SUBSTAGES[i - 1]);
  }

  function toggleAutoAdvance() {
    const playBtn = document.getElementById('substage-play-btn');
    if (autoAdvanceTimer) {
      clearInterval(autoAdvanceTimer);
      autoAdvanceTimer = null;
      if (playBtn) playBtn.textContent = '▶ Auto';
      return;
    }
    autoAdvanceTimer = setInterval(() => {
      const i = SUBSTAGES.indexOf(activeSubStage);
      const nextIdx = (i + 1) % SUBSTAGES.length;
      setSubStage(SUBSTAGES[nextIdx]);
    }, AUTO_ADVANCE_MS);
    if (playBtn) playBtn.textContent = '⏸ Pause';
  }

  function reset() {
    activeSubStage = null;
    if (autoAdvanceTimer) { clearInterval(autoAdvanceTimer); autoAdvanceTimer = null; }
    const wrap = _wrap();
    if (wrap) wrap.style.display = 'none';
    _markSubStageButton(null);
    _setInfoCard('');
    // Remove the alignment-owned virtual layers from the viewer.
    if (window.viewer && typeof window.viewer.removeLayer === 'function') {
      _ownedLayers.forEach(k => window.viewer.removeLayer(k));
    }
    _ownedLayers.clear();
    cachedAlignmentInfo = null;
    const btn = _btn();
    if (btn) {
      btn.textContent = 'Run Alignment';
      btn.disabled = !(window.pipelineState && window.pipelineState.step3Completed);
      btn.style.background = '';
    }
    if (window.pipelineState) window.pipelineState.step4Completed = false;
  }

  function getActiveSubStage() { return activeSubStage; }

  function reapplyCurrentSubStage() {
    if (activeSubStage) setSubStage(activeSubStage);
  }

  function _onAlignmentDone() {
    const btn = _btn();
    if (btn) {
      btn.textContent = 'Completed';
      btn.style.background = '#28a745';
      btn.disabled = true;
    }
    const wrap = _wrap();
    if (wrap) wrap.style.display = 'block';
    if (window.pipelineState) window.pipelineState.step4Completed = true;
    if (typeof window.updateViewerLegend === 'function') window.updateViewerLegend();
    if (typeof window.updatePipelineUI === 'function') window.updatePipelineUI();
    // Default to 4a after a fresh run.
    setSubStage('4a');
  }

  function _onAlignmentProgress({ sub_stage, message, elapsed_s }) {
    if (typeof window.showLoading === 'function') {
      const label = sub_stage ? `[${sub_stage}] ${message || ''}` : (message || 'Running…');
      window.showLoading(`${label} (${elapsed_s || 0}s)`);
    }
  }

  function _onAlignmentError({ error }) {
    if (typeof window.hideLoading === 'function') window.hideLoading();
    alert('Alignment failed: ' + error);
    const btn = _btn();
    if (btn) { btn.disabled = false; btn.textContent = 'Run Alignment'; }
  }

  async function run() {
    if (!window.PipelineRunner) {
      alert('PipelineRunner not available — page may not have finished loading.');
      return;
    }
    if (window.pipelineState && !window.pipelineState.step3Completed) {
      alert('Please complete Step 3 (Run Classifier) before running alignment.');
      return;
    }
    const btn = _btn();
    if (btn) { btn.textContent = 'Running…'; btn.disabled = true; }
    if (typeof window.showLoading === 'function') window.showLoading('Running spatial alignment…');

    try {
      await window.PipelineRunner.start('alignment', {
        onProgress: _onAlignmentProgress,
        onComplete: () => {
          if (typeof window.hideLoading === 'function') window.hideLoading();
          _onAlignmentDone();
        },
        onError: _onAlignmentError,
      });
    } catch (e) {
      _onAlignmentError({ error: e.message || String(e) });
    }
  }

  window.AlignmentStep = {
    run, setSubStage, prev, next, toggleAutoAdvance, reset,
    getActiveSubStage, reapplyCurrentSubStage,
  };
})();
