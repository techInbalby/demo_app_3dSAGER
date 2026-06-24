"""
Flask routes for per-building BKAFI lookups.

Endpoints (registered under /api):

    GET  /api/building/bkafi/<id>         — per-cand list of blocking pairs (with confidence/label)
    GET  /api/building/matches/<id>       — per-cand list of matches (filter: predicted_label = 1)

The Step 2 button in the demo's UI runs the new pipeline via
`/api/pipeline/start?stage=blocking`. The legacy `/api/bkafi/load` +
`/api/bkafi/result` endpoints that used to live here were deleted in
refactor 1.9 — no client code referenced them.
"""
import traceback

from flask import Blueprint, jsonify, request

from lib.config import CONFIDENCE_THRESHOLD
from lib.id_utils import extract_numeric_id

from .lookups import ensure_bkafi_cache_loaded, find_building_in_bkafi

bkafi_api_bp = Blueprint('bkafi_api', __name__)


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
