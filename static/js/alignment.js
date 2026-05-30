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

  let activeSubStage = null;
  let autoAdvanceTimer = null;
  let infoCardEl = null;

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

  // Sub-stage renderers (stubbed; filled in by 9..12)
  async function _show4a() {
    _setInfoCard('Sub-stage 4a (Misaligned) — implementation pending.');
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
