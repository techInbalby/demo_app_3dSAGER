/**
 * building-colors.js — Cesium viewer colouring + per-building status.
 *
 * Extracted from demo.js bucket I (Phase 2.1 of plan addendum 15). This module
 * owns:
 *   - the in-flight cache of `/api/buildings/status` responses
 *   - the pre-computed id-variant lookup map
 *   - the four per-stage colour update entry points
 *   - the concurrency guard that lets a later update cancel an earlier one
 *
 * External reads (state owned by demo.js or other modules):
 *   - window.viewer                       (Cesium viewer instance)
 *   - window.selectedFile                 (current pipeline file path)
 *   - window.selectedBuildingId           (currently inspected cand)
 *   - window.pipelineState                (step1/2/3/4 completion flags)
 *   - window.extractNumericId             (id normalisation helper)
 *
 * Public API (window.BuildingColors):
 *   getStatus(forceRefresh)               -> Promise<statusResponse>
 *   buildStatusMap(statusResponse)        -> { statusMap, numericMap }
 *   findStatus(viewerBuildingId, map)     -> statusObj|null
 *   updateSelected()                      — recolour the one inspected building
 *   updateForStage1/2/3(forceRefresh, onComplete?) -> Promise<void>
 *
 * Backward-compat shims on window: getBuildingStatus,
 * buildOptimizedStatusMap, findBuildingStatus, updateSelectedBuildingColor,
 * updateBuildingColorsForStage1/2/3.
 */
(function () {
    'use strict';

    // ── Closure-scoped state ────────────────────────────────────────────────
    let buildingStatusCache = null;          // /api/buildings/status response
    let _cachedOptimizedStatusMap = null;    // pre-computed id-variant lookup
    let _colorUpdateTicket = null;           // cancellation token for in-flight updates

    const BUILDING_STATUS_FETCH_TIMEOUT_MS = 30000;
    const STAGE1_COLOR_UPDATE_TIMEOUT_MS = 40000;

    // ── Helpers ─────────────────────────────────────────────────────────────

    function _extractNumericId(buildingId) {
        if (typeof window.extractNumericId === 'function') {
            return window.extractNumericId(buildingId);
        }
        // Fallback: the demo.js helper exists everywhere it matters, but if
        // this file ever loads before demo.js, don't crash.
        const m = String(buildingId || '').match(/(\d{10,})/);
        return m ? m[1] : buildingId;
    }

    function getStatus(forceRefresh = false) {
        return new Promise((resolve, reject) => {
            if (buildingStatusCache && !forceRefresh) {
                resolve(buildingStatusCache);
                return;
            }
            const selectedFile = window.selectedFile;
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

    function buildStatusMap(data) {
        if (_cachedOptimizedStatusMap) return _cachedOptimizedStatusMap;

        const statusMap = {};
        const numericMap = {};

        Object.entries(data.buildings).forEach(([buildingId, status]) => {
            const numericId = _extractNumericId(buildingId);
            statusMap[buildingId] = status;
            if (numericId !== buildingId) {
                statusMap[numericId] = status;
                numericMap[numericId] = status;
            }
            if (buildingId.startsWith('bag_')) {
                statusMap[buildingId.replace('bag_', '')] = status;
            } else {
                statusMap[`bag_${buildingId}`] = status;
            }
        });

        _cachedOptimizedStatusMap = { statusMap, numericMap };
        return _cachedOptimizedStatusMap;
    }

    function findStatus(viewerBuildingId, statusMap) {
        let status = statusMap[viewerBuildingId];
        if (status) return status;

        const numericId = _extractNumericId(viewerBuildingId);
        if (numericId !== viewerBuildingId) {
            status = statusMap[numericId];
            if (status) return status;
        }

        if (viewerBuildingId.startsWith('bag_')) {
            status = statusMap[viewerBuildingId.replace('bag_', '')];
            if (status) return status;
        } else {
            status = statusMap[`bag_${viewerBuildingId}`];
            if (status) return status;
        }

        return null;
    }

    // ── Selected-building recolour ──────────────────────────────────────────

    function updateSelected() {
        const selectedBuildingId = window.selectedBuildingId;
        const selectedFile = window.selectedFile;
        if (!selectedBuildingId || !selectedFile || !window.viewer) return;

        getStatus(false)
            .then(data => {
                const { statusMap } = buildStatusMap(data);
                const status = findStatus(selectedBuildingId, statusMap);
                if (!status) {
                    console.warn(`No status found for selected building: ${selectedBuildingId}`);
                    return;
                }

                const ps = window.pipelineState || {};
                let colorName = 'blue';
                if (ps.step3Completed && status.match_status) {
                    if (status.match_status === 'true_match') colorName = 'green';
                    else if (status.match_status === 'false_positive') colorName = 'red';
                    else if (status.match_status === 'no_match') colorName = 'darkgray';
                } else if (ps.step2Completed && status.has_pairs) {
                    colorName = 'yellow';
                } else if (ps.step1Completed && status.has_features) {
                    colorName = 'orange';
                }

                window.viewer.updateBuildingColor(selectedBuildingId, colorName);
                console.log(`Updated selected building ${selectedBuildingId} to color: ${colorName}`);
            })
            .catch(error => {
                console.error('Error updating selected building color:', error);
            });
    }

    // ── Per-stage bulk recolour ─────────────────────────────────────────────
    //
    // The three stage helpers share the structure: take cancellation ticket
    // → fetch status → walk viewer entities and build a colour map → call
    // viewer.updateBuildingColors(). The colour-selection rule per entity is
    // the only thing that differs (`colorFor(status)` callback).

    function _runStageUpdate(stageName, colorFor, forceRefresh, onComplete, withTimeout) {
        const selectedFile = window.selectedFile;
        if (!selectedFile || !window.viewer) {
            console.warn('Cannot update colors: no file selected or viewer not available');
            if (onComplete) onComplete();
            return Promise.resolve();
        }

        if (_colorUpdateTicket) _colorUpdateTicket.cancelled = true;
        const ticket = { cancelled: false };
        _colorUpdateTicket = ticket;

        const startTime = performance.now();
        let completed = false;
        const done = () => {
            if (completed) return;
            completed = true;
            if (onComplete) onComplete();
        };

        const chain = getStatus(forceRefresh)
            .then(data => {
                if (ticket.cancelled) { done(); return Promise.resolve(); }
                const { statusMap } = buildStatusMap(data);
                const buildingColors = {};
                if (window.viewer && window.viewer.buildingEntities) {
                    window.viewer.buildingEntities.forEach((entities, viewerBuildingId) => {
                        const status = findStatus(viewerBuildingId, statusMap);
                        buildingColors[viewerBuildingId] = colorFor(status);
                    });
                }
                if (ticket.cancelled) { done(); return Promise.resolve(); }
                if (Object.keys(buildingColors).length > 0) {
                    return window.viewer.updateBuildingColors(buildingColors, selectedFile).then(() => {
                        if (ticket.cancelled) { done(); return; }
                        const elapsed = (performance.now() - startTime).toFixed(2);
                        console.log(`Updated colors for ${Object.keys(buildingColors).length} buildings (${stageName}) in ${elapsed}ms`);
                        done();
                    });
                }
                done();
                return Promise.resolve();
            })
            .catch(error => {
                console.error(`Error updating building colors for ${stageName}:`, error);
                done();
            });

        if (withTimeout) {
            // Stage 1 has a hard cap because it's the first user-visible
            // colour update; we'd rather call onComplete early than hang the
            // spinner if the server is slow.
            Promise.race([
                chain,
                new Promise(resolve => {
                    setTimeout(() => {
                        if (!completed) console.warn(`${stageName} color update: timeout after ${STAGE1_COLOR_UPDATE_TIMEOUT_MS}ms`);
                        done();
                        resolve();
                    }, STAGE1_COLOR_UPDATE_TIMEOUT_MS);
                }),
            ]);
        }

        return chain;
    }

    function updateForStage1(forceRefresh = false, onComplete = null) {
        return _runStageUpdate(
            'Stage 1',
            status => (status && status.has_features) ? 'orange' : 'blue',
            forceRefresh,
            onComplete,
            /* withTimeout */ true,
        );
    }

    function updateForStage2(forceRefresh = false, onComplete = null) {
        return _runStageUpdate(
            'Stage 2',
            status => {
                if (!status) return 'blue';
                if (status.has_pairs) return 'yellow';
                return status.has_features ? 'orange' : 'blue';
            },
            forceRefresh,
            onComplete,
            /* withTimeout */ false,
        );
    }

    function updateForStage3(forceRefresh = false, onComplete = null) {
        return _runStageUpdate(
            'Stage 3',
            status => {
                if (!status) return 'blue';
                if (status.match_status === 'true_match') return 'green';
                if (status.match_status === 'false_positive') return 'red';
                return 'darkgray';   // covers 'no_match' + null
            },
            forceRefresh,
            onComplete,
            /* withTimeout */ false,
        );
    }

    // ── Public API + back-compat aliases ────────────────────────────────────

    window.BuildingColors = {
        getStatus,
        buildStatusMap,
        findStatus,
        updateSelected,
        updateForStage1,
        updateForStage2,
        updateForStage3,
        // Test/debug hook: clear the cache to force a refetch.
        _clearCache() {
            buildingStatusCache = null;
            _cachedOptimizedStatusMap = null;
        },
    };

    // Bare-name aliases — existing call sites in demo.js use these without
    // a namespace. Keep them working until each caller is migrated.
    window.getBuildingStatus = getStatus;
    window.buildOptimizedStatusMap = buildStatusMap;
    window.findBuildingStatus = findStatus;
    window.updateSelectedBuildingColor = updateSelected;
    window.updateBuildingColorsForStage1 = updateForStage1;
    window.updateBuildingColorsForStage2 = updateForStage2;
    window.updateBuildingColorsForStage3 = updateForStage3;
})();
