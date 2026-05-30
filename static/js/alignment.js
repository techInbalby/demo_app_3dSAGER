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
    // If we came from 4c the aligned layer is visible; hide it.
    if (_ownedLayers.has(ALIGNED_LAYER)) _setLayerVisible(ALIGNED_LAYER, false);
    _setLayerVisible(MISALIGNED_LAYER, true);

    const { cand_colors } = await _fetchColors('4a');
    if (typeof viewer.updateBuildingColors === 'function') {
      await viewer.updateBuildingColors(cand_colors || {}, MISALIGNED_LAYER);
      // Reset any anchor_index highlight on the index layer.
      const indexLayer = _findIndexLayer();
      if (indexLayer) await viewer.updateBuildingColors({}, indexLayer);
    }
    if (typeof viewer.fitCameraToBuildings === 'function') viewer.fitCameraToBuildings();
    _setInfoCard(
      `<strong>4a · Misaligned candidates</strong><br>` +
      `Candidate buildings drawn at their post-disaster positions; the rigid ` +
      `transform that RANSAC will recover has not been applied yet.`
    );
  }
  function _findIndexLayer() {
    // Returns the file_path of the currently-loaded Source B (index) layer, if any.
    if (!window.layerState) return null;
    const entries = Object.entries(window.layerState)
      .filter(([, s]) => s && s.visible && s.source === 'B');
    return entries.length ? entries[0][0] : null;
  }

  async function _show4b() {
    // 4a must be loaded so the misaligned cand layer exists.
    await _loadLayerOnce(MISALIGNED_LAYER, MISALIGNED_URL, 'A');
    if (_ownedLayers.has(ALIGNED_LAYER)) _setLayerVisible(ALIGNED_LAYER, false);
    _setLayerVisible(MISALIGNED_LAYER, true);
    const viewer = await _waitForViewer();
    const colors = await _fetchColors('4b');

    if (typeof viewer.updateBuildingColors === 'function') {
      await viewer.updateBuildingColors(colors.cand_colors || {}, MISALIGNED_LAYER);
      const indexLayer = _findIndexLayer();
      if (indexLayer && Object.keys(colors.index_colors || {}).length) {
        await viewer.updateBuildingColors(colors.index_colors, indexLayer);
      }
    }

    // Anchor count + threshold for the info card.
    let anchorMeta = '';
    try {
      const res = await fetch('/api/alignment/anchors?limit=1');
      if (res.ok) {
        const j = await res.json();
        anchorMeta =
          `<br>Anchor pool: <strong>${j.total}</strong> pairs ` +
          `with geometric score ≥ <strong>${j.confidence_threshold}</strong>.`;
      }
    } catch (_) {}

    _setInfoCard(
      `<strong>4b · Anchor selection</strong><br>` +
      `High-confidence classifier pairs become the anchor pool fed into RANSAC ` +
      `for rigid-transform estimation.${anchorMeta}`
    );
  }
  async function _fetchAlignmentInfo() {
    if (cachedAlignmentInfo) return cachedAlignmentInfo;
    const res = await fetch('/api/alignment/status');
    if (!res.ok) throw new Error('alignment status not available');
    const j = await res.json();
    cachedAlignmentInfo = j.alignment_info || null;
    return cachedAlignmentInfo;
  }

  function _setLayerVisible(layerKey, visible) {
    if (!window.viewer || typeof window.viewer.setLayerEntityShow !== 'function') return;
    window.viewer.setLayerEntityShow(layerKey, visible);
  }

  async function _show4c() {
    // Hide misaligned layer; load (or show) the aligned layer in its place.
    _setLayerVisible(MISALIGNED_LAYER, false);
    const viewer = await _waitForViewer();
    if (viewer && 'skipAutoFit' in viewer) viewer.skipAutoFit = true;
    await _loadLayerOnce(ALIGNED_LAYER, ALIGNED_URL, 'A');
    _setLayerVisible(ALIGNED_LAYER, true);

    // Reset index colors (clear the anchor_index highlight from 4b).
    const indexLayer = _findIndexLayer();
    if (indexLayer && viewer && typeof viewer.updateBuildingColors === 'function') {
      // Empty mapping reverts to per-source default colors.
      await viewer.updateBuildingColors({}, indexLayer);
    }

    // Color all aligned cands with the default A blue.
    const colors = await _fetchColors('4c');
    if (viewer && typeof viewer.updateBuildingColors === 'function') {
      await viewer.updateBuildingColors(colors.cand_colors || {}, ALIGNED_LAYER);
    }

    let info;
    try { info = await _fetchAlignmentInfo(); } catch (_) { info = null; }
    const residual  = info && info.mean_residual_m != null ? `${info.mean_residual_m.toFixed(2)} m` : 'n/a';
    const anchorN   = info && info.n_anchor_pairs   != null ? info.n_anchor_pairs : 'n/a';
    const alpha     = info && info.alpha            != null ? info.alpha : 'n/a';
    _setInfoCard(
      `<strong>4c · Rigid transform applied</strong><br>` +
      `Candidates have been moved by the RANSAC-recovered (R, t).<br>` +
      `Mean residual: <strong>${residual}</strong> · ` +
      `anchor pairs used: <strong>${anchorN}</strong> · ` +
      `α (geometric weight): <strong>${alpha}</strong>`
    );
  }
  function _renderPrSweepSvg(sweep) {
    // Tiny inline SVG. x = recall, y = precision, with the current threshold
    // point highlighted. Sweep is [{threshold, precision, recall, f1, ...}, ...].
    if (!sweep || !sweep.length) return '';
    const W = 220, H = 110, pad = 22;
    const xs = sweep.map(p => Math.max(0, Math.min(1, p.recall)));
    const ys = sweep.map(p => Math.max(0, Math.min(1, p.precision)));
    const x = (r) => pad + r * (W - 2 * pad);
    const y = (p) => (H - pad) - p * (H - 2 * pad);

    // Sort by recall so the polyline traces left → right cleanly.
    const idxSorted = sweep.map((_, i) => i).sort((a, b) => xs[a] - xs[b]);
    const pts = idxSorted.map(i => `${x(xs[i]).toFixed(1)},${y(ys[i]).toFixed(1)}`).join(' ');

    const dots = sweep.map((p, i) =>
      `<circle cx="${x(xs[i]).toFixed(1)}" cy="${y(ys[i]).toFixed(1)}" r="2.5" fill="#667eea"/>` +
      `<title>thr=${p.threshold}  P=${p.precision}  R=${p.recall}  F1=${p.f1}</title>`
    ).join('');

    return (
      `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="margin-top:6px; background:#fafafa; border:1px solid #ddd; border-radius:3px;">` +
      `  <line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="#aaa"/>` +
      `  <line x1="${pad}" y1="${pad}"    x2="${pad}"  y2="${H-pad}" stroke="#aaa"/>` +
      `  <text x="${pad}" y="${H-6}" font-size="9" fill="#666">recall →</text>` +
      `  <text x="2" y="${pad+8}" font-size="9" fill="#666" transform="rotate(-90 ${pad-12} ${pad+8})">precision</text>` +
      `  <polyline points="${pts}" fill="none" stroke="#667eea" stroke-width="1.2"/>` +
      `  ${dots}` +
      `</svg>`
    );
  }

  async function _show4d() {
    // Aligned layer must be loaded and visible (same scene as 4c).
    await _loadLayerOnce(ALIGNED_LAYER, ALIGNED_URL, 'A');
    if (_ownedLayers.has(MISALIGNED_LAYER)) _setLayerVisible(MISALIGNED_LAYER, false);
    _setLayerVisible(ALIGNED_LAYER, true);

    const viewer = await _waitForViewer();
    const colors = await _fetchColors('4d');
    if (typeof viewer.updateBuildingColors === 'function') {
      await viewer.updateBuildingColors(colors.cand_colors || {}, ALIGNED_LAYER);
      const indexLayer = _findIndexLayer();
      if (indexLayer) await viewer.updateBuildingColors({}, indexLayer);
    }

    // Header card with P/R/F1 + PR sweep mini-chart.
    let summary = null;
    try {
      const res = await fetch('/api/alignment/matches/summary');
      if (res.ok) summary = await res.json();
    } catch (_) {}

    let body;
    if (summary && summary.at_match_threshold) {
      const m = summary.at_match_threshold;
      const sweepSvg = _renderPrSweepSvg(summary.pr_sweep);
      body =
        `<strong>4d · Final matches (post-alignment)</strong><br>` +
        `Best <code>final_score</code> per cand → TP / FP / FN / no-match colouring.<br>` +
        `At threshold <strong>${m.threshold}</strong>: ` +
        `TP=<strong>${m.tp}</strong> · FP=<strong>${m.fp}</strong> · FN=<strong>${m.fn}</strong> · ` +
        `P=<strong>${m.precision}</strong> · R=<strong>${m.recall}</strong> · F1=<strong>${m.f1}</strong>` +
        sweepSvg;
    } else {
      body =
        `<strong>4d · Final matches</strong><br>` +
        `Coloured by per-cand best final_score (no ground-truth metrics ` +
        `available — no same-ID pairs in the blocking output).`;
    }
    _setInfoCard(body);
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
