"""
Flask routes for per-building status + classifier metrics summary.

Endpoints:

    GET /api/buildings/status?file=<path>     — has_features / has_pairs / match_status per building
    GET /api/classifier/summary?file=<path>   — metrics blob (P/R/F1, counts, recall@threshold)

The status route drives every legend colour in the demo. The summary route
populates the metrics card in the sidebar after Step 3.
"""
import json
import traceback
from pathlib import Path
from typing import Dict, Set, Tuple

from flask import Blueprint, jsonify, request

from lib.cache import (
    cache_get_json,
    cache_set_json,
    get_bkafi_by_file_cache,
    get_bkafi_cache,
    get_features_cache,
)
from lib.config import CONFIDENCE_THRESHOLD, DEMO_METRICS_JSON, DEMO_RESULTS_JSON
from lib.id_utils import extract_numeric_id

status_api_bp = Blueprint('status_api', __name__)


# ---------------------------------------------------------------------------
# /api/buildings/status helpers
# ---------------------------------------------------------------------------

def _has_features_for_file(file_path: str) -> Set[str]:
    """Return the set of building ids that have features cached for `file_path`.
    Uses the compact id-list Redis key when available (~1 KB) to avoid pulling
    the full feature payload (6+ MB) just to count keys."""
    features_ids = cache_get_json(f'features_ids:{file_path}')
    if features_ids is not None:
        return set(features_ids)
    features_data = get_features_cache(file_path)
    if isinstance(features_data, dict):
        return set(features_data.keys())
    return set()


def _has_pairs_from_bkafi(bkafi_data: Dict[str, dict]) -> Set[str]:
    """Set of building ids that have any BKAFI pair. Each id is stored in two
    forms (raw key + numeric extract) so the legend can render with either."""
    out: Set[str] = set()
    for candidate_id in bkafi_data.keys():
        bid_str = str(candidate_id)
        out.add(bid_str)
        numeric = extract_numeric_id(bid_str)
        if numeric:
            out.add(numeric)
    return out


def _predicted_label(match: dict) -> int:
    """Use explicit predicted_label if present, otherwise fall back to a
    synthetic 0/1 from confidence > CONFIDENCE_THRESHOLD."""
    label = match.get('predicted_label')
    if label is not None:
        return int(label)
    return 1 if float(match.get('confidence', 0)) > CONFIDENCE_THRESHOLD else 0


def _classify_building_status(possible_matches) -> str:
    """Roll up a cand's per-pair predictions into a single colour bucket.

    Priority order: true_match > false_positive > no_match > (no status).
      - true_match     — at least one predicted=1 with true_label=1
      - false_positive — at least one predicted=1 with true_label=0
      - no_match       — has pairs but no predicted=1 hits a known true
      - None           — caller's responsibility (no pairs at all)
    """
    has_true_match = False
    has_false_positive = False
    for match in possible_matches:
        if _predicted_label(match) != 1:
            continue
        true_label = match.get('true_label')
        if true_label is None:
            continue
        if int(true_label) == 1:
            has_true_match = True
        elif int(true_label) == 0:
            has_false_positive = True
    if has_true_match:
        return 'true_match'
    if has_false_positive:
        return 'false_positive'
    return 'no_match'


def _match_status_by_building(bkafi_data: Dict[str, dict]) -> Dict[str, str]:
    """Build {id_variant: status} for every cand in `bkafi_data`. Stores each
    status under both the raw cand_id and the numeric extract, so the legend
    can render keyed by either spelling."""
    out: Dict[str, str] = {}
    for candidate_id, building_data in bkafi_data.items():
        possible_matches = building_data.get('possible_matches', [])
        if not possible_matches:
            continue
        status = _classify_building_status(possible_matches)
        source_id_str = str(candidate_id)
        out[source_id_str] = status
        numeric = extract_numeric_id(source_id_str)
        if numeric:
            out[numeric] = status
    return out


# ---------------------------------------------------------------------------
# /api/buildings/status route
# ---------------------------------------------------------------------------

@status_api_bp.route('/buildings/status', methods=['GET'])
def get_all_buildings_status():
    """Return per-building flags driving the viewer legend colours:
    has_features (orange), has_pairs (yellow), match_status (green/red/grey).

    Recomputes from Redis on every request — the in-process cache that used
    to live here raced under K-change timing and served stale stub state
    (see plan addendum 9). Redis reads are sub-ms and the per-cand classify
    loop is O(N_cands × N_pairs_per_cand), ~14k ops at K=30, negligible.
    """
    try:
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400

        has_features = _has_features_for_file(file_path)

        bkafi_data = get_bkafi_cache() or {}
        has_pairs = _has_pairs_from_bkafi(bkafi_data) if bkafi_data else set()
        match_status = _match_status_by_building(bkafi_data) if bkafi_data else {}

        all_building_ids = has_features | has_pairs | set(match_status.keys())
        result = {
            str(bid): {
                'has_features': str(bid) in has_features,
                'has_pairs':    str(bid) in has_pairs,
                'match_status': match_status.get(str(bid)),
            }
            for bid in all_building_ids
        }

        resp = jsonify({'success': True, 'buildings': result, 'total': len(result)})
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    except Exception as e:
        print(f"Error getting building status: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# /api/classifier/summary helpers
# ---------------------------------------------------------------------------

# Maps the response keys the frontend reads ←→ the file_metrics keys in
# demo_metrics_summary_seed1.json. Keeps the metric translation in data
# rather than in 20+ lines of `value = file_metric_data.get('field', 0)`.
_THRESHOLD_FIELDS: Tuple[Tuple[str, str, type], ...] = (
    ('threshold_precision',                        'threshold_precision',                        float),
    ('threshold_recall_overall',                   'threshold_recall_overall',                   float),
    ('threshold_recall_blocking',                  'threshold_recall_blocking',                  float),
    ('threshold_recall_matching',                  'threshold_recall_matching',                  float),
    ('threshold_f1_score',                         'threshold_f1_score',                         float),
    ('threshold_true_positives',                   'threshold_true_positives',                   int),
    ('threshold_false_positives',                  'threshold_false_positives',                  int),
    ('threshold_total_false_negatives',            'threshold_false_negatives',                  int),
    ('threshold_false_negatives_in_blocking',      'threshold_false_negatives_in_blocking',      int),
    ('threshold_false_negatives_not_in_blocking',  'threshold_false_negatives_not_in_blocking',  int),
)

_BEST_MATCH_FIELDS: Tuple[Tuple[str, str, type], ...] = (
    ('best_match_precision',                       'best_match_precision',                       float),
    ('best_match_recall_overall',                  'best_match_recall_overall',                  float),
    ('best_match_recall_blocking',                 'best_match_recall_blocking',                 float),
    ('best_match_recall_matching',                 'best_match_recall_matching',                 float),
    ('best_match_f1_score',                        'best_match_f1_score',                        float),
    ('best_match_true_positives',                  'best_match_true_positives',                  int),
    ('best_match_false_positives',                 'best_match_false_positives',                 int),
    ('best_match_total_false_negatives',           'best_match_total_false_negatives',           int),
    ('best_match_false_negatives_in_blocking',     'best_match_false_negative_in_blocking',      int),
    ('best_match_false_negatives_not_in_blocking', 'best_match_false_negative_not_in_blocking',  int),
)


def _find_file_metrics(file_metrics: dict, file_name: str) -> dict:
    """Look up the per-file metrics block by exact match, then substring.
    Returns {} if not found so caller can decide whether to 404."""
    for key, value in file_metrics.items():
        if key == file_name or file_name in key or key in file_name:
            return value
    return {}


def _count_total_pairs(file_name: str) -> int:
    """Sum of possible_matches across every cand in `file_name`. Reads
    bkafi:by_file (cached) or falls back to the JSON file."""
    bkafi_by_file = get_bkafi_by_file_cache()
    if bkafi_by_file is None and DEMO_RESULTS_JSON.exists():
        with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
            bkafi_by_file = json.load(f)
        cache_set_json('bkafi:by_file', bkafi_by_file)
    if not bkafi_by_file or file_name not in bkafi_by_file:
        return 0
    return sum(
        len(building_data.get('possible_matches', []))
        for building_data in bkafi_by_file[file_name].values()
    )


# ---------------------------------------------------------------------------
# /api/classifier/summary route
# ---------------------------------------------------------------------------

@status_api_bp.route('/classifier/summary', methods=['GET'])
def get_classifier_summary():
    """Return the metrics blob the sidebar metrics card reads. Sources from
    `demo_metrics_summary_seed1.json` (bridged by the Celery worker after
    Step 3) plus a derived `total_pairs` count from `bkafi:by_file`."""
    try:
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400

        if not DEMO_METRICS_JSON.exists():
            return jsonify({'error': f'Metrics summary file not found at {DEMO_METRICS_JSON}'}), 404

        with open(DEMO_METRICS_JSON, 'r', encoding='utf-8') as f:
            metrics_data = json.load(f)

        model_name = 'XGBClassifier'
        if model_name not in metrics_data:
            return jsonify({'error': f'Model {model_name} not found in metrics file'}), 404

        file_metrics = metrics_data[model_name].get('file_metrics', {})
        file_name = Path(file_path).name
        file_metric_data = _find_file_metrics(file_metrics, file_name)
        if not file_metric_data:
            return jsonify({'error': f'No metrics found for file: {file_name}'}), 404

        def _get(field: str, cast: type, default=0):
            value = file_metric_data.get(field, default)
            try:
                return cast(value)
            except (TypeError, ValueError):
                return cast(default)

        potential_matches_in_index = _get('potential_matches_in_index', int)
        potential_matches_in_blocking = _get('potential_matches_in_blocking', int)
        candidates_in_file = _get('candidates_in_file', int)

        summary = {
            'total_buildings': candidates_in_file,
            'total_buildings_in_file': candidates_in_file,
            'potential_true_matches': potential_matches_in_blocking,
            'potential_true_matches_not_in_bkafi': potential_matches_in_index - potential_matches_in_blocking,
            'buildings_with_true_match_in_bkafi': potential_matches_in_blocking,
            'total_pairs': _count_total_pairs(file_name),
        }
        for src_key, dst_key, cast in _THRESHOLD_FIELDS + _BEST_MATCH_FIELDS:
            summary[dst_key] = _get(src_key, cast, default=0)

        # Frontend-friendly aliases on top of the field-by-field copy above.
        summary.update({
            'found_true_matches':            summary['threshold_true_positives'],
            'recall':                        summary['threshold_recall_matching'],
            'precision':                     summary['threshold_precision'],
            'precision_conf_threshold':      summary['threshold_precision'],
            'precision_highest_conf':        summary['best_match_precision'],
            'predicted_with_conf_threshold': summary['threshold_true_positives'] + summary['threshold_false_positives'],
            'predicted_highest_conf':        summary['best_match_true_positives'] + summary['best_match_false_positives'],
            'true_positive':                 summary['threshold_true_positives'],
            'false_positive':                summary['threshold_false_positives'],
            'false_negative':                summary['threshold_false_negatives'],
            'false_negative_in_blocking':    summary['threshold_false_negatives_in_blocking'],
            'false_negative_not_in_blocking': summary['threshold_false_negatives_not_in_blocking'],
            'true_matches_not_in_blocking':  summary['threshold_false_negatives_not_in_blocking'],
            'overall_recall':                summary['threshold_recall_overall'],
            'blocking_recall':               summary['threshold_recall_blocking'],
            'matching_recall':               summary['threshold_recall_matching'],
            'f1_score':                      summary['best_match_f1_score'],
        })

        return jsonify({'success': True, 'summary': summary})

    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 500
    except Exception as e:
        print(f"Error getting classifier summary: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
