"""
Flask routes for the legacy BKAFI surface.

Endpoints:

    POST /api/bkafi/load                  — bridge demo_detailed_results JSON → Redis cache
    GET  /api/bkafi/result                — DEPRECATED: return the bridged cache as a JSON blob
    GET  /api/building/bkafi/<id>         — per-cand list of blocking pairs (with confidence/label)
    GET  /api/building/matches/<id>       — per-cand list of matches (filter: predicted_label = 1)

The Step 2 button in the demo's UI calls `/api/pipeline/start?stage=blocking`
on the new pipeline. /api/bkafi/load is kept for compatibility with older
client code paths and explicit re-bridge requests.

The bkafi blueprint mounts at /api (not /api/bkafi) because two of its
routes are under /api/building, not /api/bkafi.
"""
import json
import traceback

from flask import Blueprint, jsonify, request

from lib.cache import (
    cache_get_json,
    cache_set_json,
    get_bkafi_by_file_cache,
    get_bkafi_cache,
    get_redis_client,
    invalidate_buildings_status_cache,
    set_bkafi_by_file_cache,
    set_bkafi_cache,
)
from lib.config import CONFIDENCE_THRESHOLD, DEMO_RESULTS_JSON
from lib.id_utils import extract_numeric_id
from tasks import load_bkafi_results as load_bkafi_task

from .lookups import ensure_bkafi_cache_loaded, find_building_in_bkafi

bkafi_api_bp = Blueprint('bkafi_api', __name__)


# ---------------------------------------------------------------------------
# POST /api/bkafi/load
# ---------------------------------------------------------------------------

@bkafi_api_bp.route('/bkafi/load', methods=['POST'])
def load_bkafi_results():
    """Bridge `demo_detailed_results_XGBClassifier_seed1.json` into Redis
    (or enqueue the Celery task that does the same). Hits a fast-path when
    the cache is already warm."""
    try:
        cached_bkafi = get_bkafi_cache()
        cached_by_file = get_bkafi_by_file_cache()
        if cached_bkafi is not None and cached_by_file is not None:
            return jsonify({
                'success': True,
                'message': 'BKAFI results already cached',
                'total_pairs': sum(len(v.get('possible_matches', [])) for v in cached_bkafi.values()),
                'unique_candidates': len(cached_bkafi),
            })

        if get_redis_client():
            job = load_bkafi_task.delay()
            return jsonify({
                'job_id': job.id,
                'status': 'queued',
                'message': 'BKAFI load queued',
            }), 202

        if not DEMO_RESULTS_JSON.exists():
            return jsonify({'error': f'BKAFI results file not found at {DEMO_RESULTS_JSON}'}), 404

        with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
            results_dict = json.load(f)

        flattened_cache: dict = {}
        unique_candidates = 0
        total_pairs = 0
        for _file_name, file_buildings in results_dict.items():
            for building_id, building_data in file_buildings.items():
                flattened_cache[building_id] = building_data
                unique_candidates += 1
                total_pairs += len(building_data.get('possible_matches', []))

        set_bkafi_cache(flattened_cache)
        set_bkafi_by_file_cache(results_dict)
        cache_set_json('bkafi:flat', flattened_cache)
        cache_set_json('bkafi:by_file', results_dict)
        invalidate_buildings_status_cache()

        return jsonify({
            'success': True,
            'message': f'BKAFI results loaded: {total_pairs} pairs for {unique_candidates} candidate buildings',
            'total_pairs': int(total_pairs),
            'unique_candidates': int(unique_candidates),
        })

    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 500
    except Exception as e:
        print(f"Error loading BKAFI results: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/bkafi/result   (DEPRECATED; deletion scheduled in refactor 1.9)
# ---------------------------------------------------------------------------

@bkafi_api_bp.route('/bkafi/result', methods=['GET'])
def get_bkafi_result():
    cached = cache_get_json('bkafi:flat')
    if cached is None:
        return jsonify({'error': 'BKAFI results not found in cache'}), 404
    return jsonify({'bkafi': cached})


# ---------------------------------------------------------------------------
# Per-building lookups
# ---------------------------------------------------------------------------

def _numeric_id_or_fallback(building_id) -> str:
    """Pull the BAG numeric out of `building_id`; fall back to split('_')[-1]
    for the rare non-BAG case. Always returns a str."""
    return str(
        extract_numeric_id(building_id)
        or (building_id.split('_')[-1] if '_' in str(building_id) else str(building_id))
    )


def _predicted_match_label(match: dict) -> int:
    """The match's predicted_label field, or a synthetic 0/1 from confidence."""
    label = match.get('predicted_label')
    if label is not None:
        return int(label)
    return 1 if float(match.get('confidence', 0)) > CONFIDENCE_THRESHOLD else 0


@bkafi_api_bp.route('/building/bkafi/<building_id>', methods=['GET'])
def get_building_bkafi(building_id):
    """Return up to N blocking pairs (cand→index) for `building_id`. Each
    pair carries predicted_label + confidence + true_label so the
    per-building inspection panel can colour each row."""
    try:
        bkafi_cache = ensure_bkafi_cache_loaded()
        if bkafi_cache is None:
            return jsonify({
                'error': 'BKAFI results not loaded. Please run Step 2 first.',
                'pairs': [],
            }), 404

        numeric_id = _numeric_id_or_fallback(building_id)
        _matched_id, building_data = find_building_in_bkafi(bkafi_cache, numeric_id)

        if building_data is None:
            return jsonify({
                'building_id': building_id,
                'pairs': [],
                'message': f'No BKAFI pairs found for building {building_id}',
            })

        possible_matches = building_data.get('possible_matches', [])
        pairs = []
        for match in possible_matches:
            pairs.append({
                'candidate_id': numeric_id,
                'index_id': str(match.get('index_id', '')),
                'prediction': _predicted_match_label(match),
                'true_label': int(match['true_label']) if match.get('true_label') is not None else None,
                'confidence': float(match.get('confidence', 0)),
            })
        pairs.sort(key=lambda p: p.get('confidence', 0), reverse=True)

        return jsonify({
            'building_id': building_id,
            'pairs': pairs,
            'total_pairs': len(pairs),
        })

    except Exception as e:
        print(f"Error getting BKAFI pairs: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e), 'pairs': []}), 500


@bkafi_api_bp.route('/building/matches/<building_id>', methods=['GET'])
def get_building_matches(building_id):
    """Same data as get_building_bkafi, but only the pairs with
    predicted_label = 1. Used by the legacy matches-window in the demo UI."""
    try:
        bkafi_cache = ensure_bkafi_cache_loaded()
        if bkafi_cache is None:
            return jsonify({
                'error': 'BKAFI results not loaded. Please run Step 2 first.',
                'matches': [],
            }), 404

        numeric_id = _numeric_id_or_fallback(building_id)
        _matched_id, building_data = find_building_in_bkafi(bkafi_cache, numeric_id)

        if building_data is None:
            return jsonify({
                'building_id': building_id,
                'matches': [],
                'message': f'No matches found for building {building_id}',
            })

        matches = []
        for match in building_data.get('possible_matches', []):
            if _predicted_match_label(match) != 1:
                continue
            matches.append({
                'id': match.get('index_id', ''),
                'building_id': str(match.get('index_id', '')),
                'source': 'Source B',
                'confidence': float(match.get('confidence', 0)),
                'true_label': int(match['true_label']) if match.get('true_label') is not None else None,
            })
        matches.sort(key=lambda m: m.get('confidence', 0), reverse=True)

        return jsonify({
            'building_id': building_id,
            'matches': matches,
        })

    except Exception as e:
        print(f"Error getting matches: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e), 'matches': []}), 500
