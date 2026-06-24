/**
 * alignment.js — Owns Step 4 ("Spatial alignment") of the demo pipeline.
 *
 * Single-click flow:
 *   1. Run the alignment pipeline stage via PipelineRunner.
 *   2. On success, fetch the per-cand color map for the final post-alignment
 *      predictions (TP / FP / false_negative / no-match) and apply it to the
 *      already-loaded Source A layer.
 *   3. Render a compact summary card (mean residual, anchor count, P/R/F1 at
 *      the match threshold). No PR sweep, no sub-stage controls.
 *
 * Public surface (window.AlignmentStep):
 *   run()                  kick off the alignment pipeline + render results
 *   reset()                clear state (called from resetPipelineState)
 *   reapplyColors()        re-paint after a dataset switch (called from setPipelineFile)
 */
(function () {
  'use strict';

  function _btn()  { return document.getElementById('step-btn-4'); }
  function _info() { return document.getElementById('step-4-info-card'); }

  function _setInfoCard(html) {
    const el = _info();
    if (!el) return;
    el.style.display = html ? 'block' : 'none';
    el.innerHTML = html || '';
  }

  function _findCandLayer() {
    // The Source A layer currently visible in the viewer. Set by file-picker
    // selection; falls back to demo.js's `selectedFile` global.
    if (window.layerState) {
      const entry = Object.entries(window.layerState)
        .find(([, s]) => s && s.visible && s.source === 'A');
      if (entry) return entry[0];
    }
    return window.selectedFile || null;
  }

  async function _fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) {
      let detail; try { detail = (await res.json()).error || res.statusText; } catch { detail = res.statusText; }
      throw new Error(`${url} → ${detail}`);
    }
    return res.json();
  }

  async function _applyColors() {
    const candLayer = _findCandLayer();
    if (!candLayer || !window.viewer) {
      console.warn('[AlignmentStep] No visible Source A layer to recolor.');
      return;
    }
    const resp = await _fetchJson('/api/alignment/buildings/colors?stage=4d');
    const byNumeric = resp.cand_colors || {};

    // The backend keys colors by raw numeric BAG id (e.g. "0518100000203425")
    // because matches_by_cand.json comes from scored_pairs.joblib. The viewer's
    // entities are keyed by the CityJSON 1.1 object id which is BAG-prefixed
    // (e.g. "NL.IMBAG.Pand.0518100000203425-0"). Re-key the colors map by the
    // exact viewer buildingId so updateBuildingColors's direct lookup hits.
    const remapped = {};
    if (window.viewer.buildingEntities) {
      window.viewer.buildingEntities.forEach((_entities, viewerBuildingId) => {
        const m = String(viewerBuildingId).match(/(\d{10,})/);
        const numeric = m ? m[1] : String(viewerBuildingId);
        const color = byNumeric[numeric];
        if (color) remapped[viewerBuildingId] = color;
      });
    }

    if (typeof window.viewer.updateBuildingColors === 'function') {
      await window.viewer.updateBuildingColors(remapped, candLayer);
    }
  }

  async function _renderSummary() {
    let info = null, metrics = null;
    try { info    = await _fetchJson('/api/alignment/status'); }            catch (e) { /* tolerate */ }
    try { metrics = await _fetchJson('/api/alignment/matches/summary'); }   catch (e) { /* tolerate */ }

    const ai = info && info.alignment_info ? info.alignment_info : null;
    const mr = ai && ai.mean_residual_m != null ? ai.mean_residual_m.toFixed(2) + ' m' : 'n/a';
    const np = ai && ai.n_anchor_pairs    != null ? ai.n_anchor_pairs : 'n/a';
    const co = ai && ai.cutoff_m          != null ? ai.cutoff_m + ' m' : 'n/a';

    let metricsHtml = '';
    if (metrics && metrics.at_match_threshold) {
      const m = metrics.at_match_threshold;
      metricsHtml =
        `<div style="margin-top:6px;">` +
        `At matcher threshold <strong>${m.threshold}</strong>: ` +
        `P=<strong>${m.precision}</strong> · R=<strong>${m.recall}</strong> · F1=<strong>${m.f1}</strong><br>` +
        `TP=<strong>${m.tp}</strong> · FP=<strong>${m.fp}</strong> · FN=<strong>${m.fn}</strong>` +
        `</div>`;
    } else {
      metricsHtml = `<div style="margin-top:6px;color:#888;">No same-ID ground truth in this run.</div>`;
    }

    _setInfoCard(
      `<strong>Geospatial alignment — results</strong><br>` +
      `Mean residual: <strong>${mr}</strong> · ` +
      `potential anchors: <strong>${np}</strong> · ` +
      `cutoff: <strong>${co}</strong>` +
      metricsHtml
    );
  }

  async function reapplyColors() {
    if (!window.pipelineState || !window.pipelineState.step4Completed) return;
    try { await _applyColors(); }
    catch (e) { console.error('[AlignmentStep] reapply failed:', e); }
  }

  function reset() {
    _setInfoCard('');
    const btn = _btn();
    if (btn) {
      btn.textContent = 'Run Alignment';
      btn.disabled = !(window.pipelineState && window.pipelineState.step3Completed);
      btn.style.background = '';
    }
    if (window.pipelineState) window.pipelineState.step4Completed = false;
  }

  async function _onAlignmentDone() {
    try {
      await _applyColors();
      await _renderSummary();
    } catch (e) {
      console.error('[AlignmentStep] post-run rendering failed:', e);
      _setInfoCard(`<span style="color:#c00;">Error rendering results: ${e.message || e}</span>`);
    }
    const btn = _btn();
    if (btn) {
      btn.textContent = 'Completed';
      btn.style.background = '#28a745';
      btn.disabled = true;
    }
    if (window.pipelineState) window.pipelineState.step4Completed = true;
    if (typeof window.updateViewerLegend === 'function') window.updateViewerLegend();
    if (typeof window.updatePipelineUI   === 'function') window.updatePipelineUI();
  }

  function _onAlignmentProgress({ sub_stage, message, elapsed_s }) {
    if (typeof window.showLoading !== 'function') return;
    const label = sub_stage ? `[${sub_stage}] ${message || ''}` : (message || 'Running…');
    window.showLoading(`${label} (${elapsed_s || 0}s)`);
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

    // Pick up the current cutoff input so the pipeline can recompute when the
    // user changes it. Falls back to backend default if the input is missing
    // or unparseable.
    const cutoffInput = document.getElementById('cfg-align-cutoff');
    const cutoffVal   = cutoffInput ? parseFloat(cutoffInput.value) : NaN;
    const body = (Number.isFinite(cutoffVal) && cutoffVal > 0)
      ? { post_align_knn_cutoff: cutoffVal } : undefined;
    try {
      await window.PipelineRunner.start('alignment', {
        body,
        onProgress: _onAlignmentProgress,
        onComplete: async () => {
          if (typeof window.hideLoading === 'function') window.hideLoading();
          await _onAlignmentDone();
        },
        onError: _onAlignmentError,
      });
    } catch (e) {
      _onAlignmentError({ error: e.message || String(e) });
    }
  }

  window.AlignmentStep = { run, reset, reapplyColors };
})();
