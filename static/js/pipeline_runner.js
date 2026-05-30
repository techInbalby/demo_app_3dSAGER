/**
 * pipeline_runner.js — Generic Celery task poller for the live inference pipeline.
 *
 * Public API (window.PipelineRunner):
 *   start(stage, opts?)       → { task_id, cache_hit }   (returns Promise)
 *   onProgress(stage, cb)     register a stage/message/elapsed_s listener
 *   onComplete(stage, cb)     register a success-result listener
 *   onError(stage, cb)        register a failure listener
 *   cancel(stage)             stop polling (does NOT revoke the task on the server)
 *   getActive()               { stage, task_id, started_at } | null
 *   getManifest()             Promise<manifest dict>
 *
 * Stage values match the backend: 'features' | 'blocking' | 'matching' | 'alignment'.
 */
(function () {
  'use strict';

  // Endpoints. Keep them at module level so they're easy to change in one place.
  const API_START   = '/api/pipeline/start';
  const API_STATUS  = (taskId) => `/api/pipeline/status/${encodeURIComponent(taskId)}`;
  const API_MANIFEST = '/api/pipeline/manifest';

  // Polling timings.
  const POLL_FAST_MS   = 500;
  const POLL_SLOW_MS   = 2000;
  const POLL_SLOW_AFTER_MS = 30000;
  const POLL_MAX_MS    = 5000;

  // Per-stage listener registries.
  const listeners = {
    progress: {},   // { stage: [cb, ...] }
    complete: {},
    error:    {},
  };

  // Active polls keyed by stage. Only one poll per stage at a time.
  const active = {};   // { stage: { task_id, started_at, timer, polls } }

  function _fire(kind, stage, payload) {
    const arr = listeners[kind][stage] || [];
    for (const cb of arr) {
      try { cb(payload); } catch (e) { console.error(`[PipelineRunner] ${kind} listener:`, e); }
    }
  }

  function _registerListener(kind, stage, cb) {
    if (typeof cb !== 'function') throw new Error('callback must be a function');
    if (!listeners[kind][stage]) listeners[kind][stage] = [];
    listeners[kind][stage].push(cb);
  }

  async function _postStart(stage) {
    const res = await fetch(API_START, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ stage }),
    });
    if (!res.ok) {
      let detail;
      try { detail = (await res.json()).error || res.statusText; } catch { detail = res.statusText; }
      throw new Error(`/api/pipeline/start failed: ${detail}`);
    }
    return res.json();
  }

  async function _fetchStatus(taskId) {
    const res = await fetch(API_STATUS(taskId));
    if (!res.ok) {
      throw new Error(`status poll failed (HTTP ${res.status})`);
    }
    return res.json();
  }

  function _scheduleNextPoll(stage) {
    const ctx = active[stage];
    if (!ctx) return;
    const elapsed = Date.now() - ctx.started_at;
    const delay = elapsed > POLL_SLOW_AFTER_MS
      ? Math.min(POLL_MAX_MS, POLL_SLOW_MS + 100 * ctx.polls)
      : POLL_FAST_MS;
    ctx.timer = setTimeout(() => _poll(stage), delay);
  }

  async function _poll(stage) {
    const ctx = active[stage];
    if (!ctx) return;
    ctx.polls += 1;

    let payload;
    try {
      payload = await _fetchStatus(ctx.task_id);
    } catch (e) {
      _fire('error', stage, { stage, error: String(e) });
      cancel(stage);
      return;
    }

    if (payload.state === 'PROGRESS' || payload.state === 'STARTED') {
      _fire('progress', stage, {
        stage,
        sub_stage: payload.stage,
        message:   payload.message,
        elapsed_s: payload.elapsed_s,
      });
      _scheduleNextPoll(stage);
      return;
    }

    if (payload.state === 'SUCCESS') {
      cancel(stage);
      _fire('complete', stage, { stage, result: payload.result });
      return;
    }

    if (payload.state === 'FAILURE') {
      cancel(stage);
      _fire('error', stage, { stage, error: payload.error || 'task failed' });
      return;
    }

    // PENDING / RECEIVED / RETRY → keep polling.
    _scheduleNextPoll(stage);
  }

  function _clearListeners(stage) {
    delete listeners.progress[stage];
    delete listeners.complete[stage];
    delete listeners.error[stage];
  }

  async function start(stage, opts) {
    if (active[stage]) {
      throw new Error(`stage '${stage}' is already running`);
    }
    // Reset listeners for this stage so consecutive clicks don't pile up duplicates.
    // Then register opts.{onProgress,onComplete,onError} if supplied — these are
    // the most common usage pattern (one-shot per click).
    _clearListeners(stage);
    if (opts && typeof opts === 'object') {
      if (typeof opts.onProgress === 'function') _registerListener('progress', stage, opts.onProgress);
      if (typeof opts.onComplete === 'function') _registerListener('complete', stage, opts.onComplete);
      if (typeof opts.onError    === 'function') _registerListener('error',    stage, opts.onError);
    }
    const startPayload = await _postStart(stage);
    active[stage] = {
      task_id:    startPayload.task_id,
      started_at: Date.now(),
      input_hash: startPayload.input_hash,
      cache_dir:  startPayload.cache_dir,
      polls:      0,
      timer:      null,
    };
    // Kick off the first poll on next tick so the caller can register listeners.
    active[stage].timer = setTimeout(() => _poll(stage), POLL_FAST_MS);
    return startPayload;
  }

  function cancel(stage) {
    const ctx = active[stage];
    if (!ctx) return;
    if (ctx.timer) clearTimeout(ctx.timer);
    delete active[stage];
  }

  function getActive() {
    const keys = Object.keys(active);
    if (keys.length === 0) return null;
    const stage = keys[0];
    const ctx = active[stage];
    return { stage, task_id: ctx.task_id, started_at: ctx.started_at };
  }

  async function getManifest() {
    const res = await fetch(API_MANIFEST);
    if (!res.ok) {
      let detail; try { detail = (await res.json()).error || res.statusText; } catch { detail = res.statusText; }
      throw new Error(`/api/pipeline/manifest failed: ${detail}`);
    }
    return res.json();
  }

  window.PipelineRunner = {
    start,
    cancel,
    getActive,
    getManifest,
    clearListeners: _clearListeners,
    onProgress: (stage, cb) => _registerListener('progress', stage, cb),
    onComplete: (stage, cb) => _registerListener('complete', stage, cb),
    onError:    (stage, cb) => _registerListener('error',    stage, cb),
  };
})();
