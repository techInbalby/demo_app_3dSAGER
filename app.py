"""
3dSAGER Demo Flask Application
Provides web interface and API endpoints for 3D geospatial entity resolution
"""

import os
import json
import re
import pandas as pd
from flask import Flask, render_template, jsonify, request, make_response
from flask_compress import Compress
from pathlib import Path
import hashlib
import redis

from tasks import celery as celery_app
from tasks import load_bkafi_results as load_bkafi_task

from pipeline import pipeline_bp
# Note: the demo's blueprint package is named align_api/ (not alignment/) to
# avoid colliding with demo_infrance_pipeline/modules/alignment.py, which the
# stage code puts on sys.path[0] via config_demo. URL prefix remains
# /api/alignment so the frontend contract is unchanged.
from align_api import alignment_bp
from data_api import data_api_bp
from features_api import features_api_bp
from bkafi_api import bkafi_api_bp
from building_api import building_api_bp

app = Flask(__name__)
# Enable compression for all responses (gzip)
Compress(app)
app.register_blueprint(pipeline_bp, url_prefix='/api/pipeline')
app.register_blueprint(alignment_bp, url_prefix='/api/alignment')
app.register_blueprint(data_api_bp, url_prefix='/api/data')
# features_api and bkafi_api each have two top-level URL spaces
# (/api/features/* + /api/building/features/<id>, and /api/bkafi/* +
# /api/building/{bkafi,matches}/<id>), so they're mounted at /api with
# routes that include the second-level path.
app.register_blueprint(features_api_bp, url_prefix='/api')
app.register_blueprint(bkafi_api_bp, url_prefix='/api')
app.register_blueprint(building_api_bp, url_prefix='/api')

# Configuration — single source of truth lives in lib.config; re-exported here
# so existing module-level references keep working until each consumer is
# migrated to import from lib.config directly.
from lib.config import (
    BASE_DIR,
    DATA_DIR,
    RESULTS_DIR,
    PIPELINE_CACHE_ROOT,
    SAVED_MODEL_DIR,
    LOGS_DIR,
    DEMO_RESULTS_JSON,
    DEMO_METRICS_JSON,
    FEATURES_PARQUET,
    CONFIDENCE_THRESHOLD,
    REDIS_URL,
    CACHE_TTL_SECONDS,
    ensure_directories_exist,
)

# Redis client + JSON cache helpers + in-memory cache mirrors live in lib.cache.
# Imported at module top-level so existing route code (`cache_get_json(...)`,
# `features_cache[fp] = ...`) keeps working unchanged.
from lib.cache import (
    get_redis_client,
    cache_get_json,
    cache_set_json,
    features_cache,
    get_features_cache,
    get_bkafi_cache,
    set_bkafi_cache,
    get_bkafi_by_file_cache,
    set_bkafi_by_file_cache,
)
from lib.id_utils import extract_numeric_id, numeric_ids_match


ensure_directories_exist()


@app.route('/')
def index():
    """Home page"""
    return render_template('index.html')


@app.route('/demo')
def demo():
    """Demo page with 3D viewer"""
    cesium_ion_token = os.getenv('CESIUM_ION_TOKEN', '')
    return render_template('demo.html', cesium_ion_token=cesium_ion_token)


# /api/data/* moved to the data_api blueprint (registered at the top of this
# file). See data_api/routes.py for files, select, file/<path>, file?path=.


# Note: features_cache + bkafi_cache + bkafi_by_file_cache live in lib.cache
# now (imported at the top of this file). The mutations below still write
# to the shared dict / via setters, so existing call sites stay unchanged.


def invalidate_buildings_status_cache(file_path: str = None):
    """No-op shim. The per-file in-process buildings-status cache was removed
    (it caused intermittent stale-colour bugs across the K-change boundary).
    Every legacy route in this module still calls this function on data
    mutations; rather than chase down each call site, keep the function as a
    no-op so the modules continue to import cleanly."""
    return


# /api/features/calculate + /api/building/features/<id> moved to features_api
# blueprint (registered at the top of this file). See features_api/routes.py
# for the route bodies and features_api/loaders.py for the parquet/joblib
# helpers + the 6-strategy ID matcher.



# /api/bkafi/load + /api/bkafi/result + /api/building/{bkafi,matches}/<id>
# moved to bkafi_api blueprint (registered at the top of this file). See
# bkafi_api/routes.py and bkafi_api/lookups.py.


@app.route('/api/jobs/<task_id>', methods=['GET'])
def get_job_status(task_id):
    result = celery_app.AsyncResult(task_id)
    payload = {
        'task_id': task_id,
        'status': result.status
    }
    if result.failed():
        payload['error'] = str(result.result)
    if result.successful():
        payload['result'] = result.result
    return jsonify(payload)


@app.route('/api/features/result', methods=['GET'])
def get_features_result():
    file_path = request.args.get('file_path', '')
    if not file_path:
        return jsonify({'error': 'No file path provided'}), 400
    cached = cache_get_json(f'features:{file_path}')
    if cached is None:
        return jsonify({'error': 'Features not found in cache'}), 404
    return jsonify({'file_path': file_path, 'features': cached})



@app.route('/api/buildings/status', methods=['GET'])
def get_all_buildings_status():
    """
    Get status for all buildings in the selected file
    Returns: building_id -> {has_features, has_pairs, match_status}
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400

        # No in-process cache here on purpose — every request recomputes from
        # Redis. The previous per-file cache + cross-process stamp invalidation
        # raced under K-change timing and served stale stub state. Redis reads
        # are sub-ms and the per-cand computation is O(N_cands × N_neighbours)
        # ≈ 14k ops at K=30, negligible.
        print(f"Getting status for all buildings in file: {file_path}")
        
        result = {}
        
        # 1. Check which buildings have features
        # Use compact ID list (written by Celery task) to avoid loading 6+ MB of feature values
        has_features = set()
        features_ids = cache_get_json(f'features_ids:{file_path}')
        if features_ids is not None:
            has_features = set(features_ids)
        else:
            features_data = get_features_cache(file_path)
            if isinstance(features_data, dict):
                has_features = set(features_data.keys())
        
        # 2. Check which buildings have BKAFI pairs
        has_pairs = set()
        bkafi_data = get_bkafi_cache()
        if bkafi_data is not None:
            # Get all unique candidate building IDs from dictionary keys
            for candidate_id in bkafi_data.keys():
                bid_str = str(candidate_id)
                has_pairs.add(bid_str)
                # Also add numeric version for matching
                bid_numeric = extract_numeric_id(bid_str)
                if bid_numeric:
                    has_pairs.add(bid_numeric)
        
        # 3. Check match status (true match, false positive, no match)
        # For each building, check all its pairs to determine overall status
        match_status = {}  # building_id -> 'true_match', 'false_positive', 'no_match'
        if bkafi_data is not None:
            # Iterate over dictionary keys (candidate building IDs)
            for candidate_id, building_data in bkafi_data.items():
                source_id_str = str(candidate_id)
                
                # Get possible_matches array
                possible_matches = building_data.get('possible_matches', [])
                building_has_pairs = len(possible_matches) > 0
                
                # Check all pairs for this building
                has_true_match = False
                has_false_positive = False
                
                for match in possible_matches:
                    # Get predicted_label or calculate from confidence
                    predicted_label = match.get('predicted_label')
                    if predicted_label is None:
                        predicted_label = 1 if match.get('confidence', 0) > CONFIDENCE_THRESHOLD else 0
                    else:
                        predicted_label = int(predicted_label)
                    
                    # Get true_label (do not use is_match as it's redundant)
                    true_label = match.get('true_label')
                    if true_label is not None:
                        true_label = int(true_label)
                    
                    if predicted_label == 1:
                        if true_label == 1:
                            has_true_match = True
                        elif true_label == 0:
                            has_false_positive = True
                
                # Determine overall status for this building based on ALL pairs
                # Priority: true_match > false_positive > no_match
                if has_true_match:
                    status = 'true_match'  # At least one pair with predicted_label=1 and true_label=1
                elif has_false_positive:
                    status = 'false_positive'  # At least one pair with predicted_label=1 and true_label=0
                elif building_has_pairs:
                    # Has pairs but all predictions are 0, or prediction=1 with unknown true_label
                    status = 'no_match'
                else:
                    status = None  # No pairs at all - keep previous stage color
                
                # Store for both full ID and numeric ID
                numeric_id = extract_numeric_id(source_id_str)

                if status:
                    match_status[source_id_str] = status
                    if numeric_id:
                        match_status[numeric_id] = status
        
        # Combine all building IDs
        all_building_ids = has_features.union(has_pairs).union(match_status.keys())
        
        # Build result
        for building_id in all_building_ids:
            building_id_str = str(building_id)
            result[building_id_str] = {
                'has_features': building_id_str in has_features,
                'has_pairs': building_id_str in has_pairs,
                'match_status': match_status.get(building_id_str, None)
            }
        
        resp = jsonify({
            'success': True,
            'buildings': result,
            'total': len(result)
        })
        resp.headers['Cache-Control'] = 'no-store'
        return resp
        
    except Exception as e:
        import traceback
        print(f"Error getting building status: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/classifier/summary', methods=['GET'])
def get_classifier_summary():
    """
    Get classifier results summary with success rates calculated per file
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        print(f"Getting classifier summary for file: {file_path}")
        
        # Load metrics summary JSON
        if not DEMO_METRICS_JSON.exists():
            return jsonify({'error': f'Metrics summary file not found at {DEMO_METRICS_JSON}'}), 404
        
        with open(DEMO_METRICS_JSON, 'r', encoding='utf-8') as f:
            metrics_data = json.load(f)
        
        # Extract model metrics (XGBClassifier)
        model_name = 'XGBClassifier'
        if model_name not in metrics_data:
            return jsonify({'error': f'Model {model_name} not found in metrics file'}), 404
        
        model_metrics = metrics_data[model_name]
        file_metrics = model_metrics.get('file_metrics', {})
        
        # Get file name to match against file_metrics keys
        file_name = Path(file_path).name
        
        # Find matching file in file_metrics (try exact match first, then partial)
        file_metric_data = None
        for key in file_metrics.keys():
            if key == file_name or file_name in key or key in file_name:
                file_metric_data = file_metrics[key]
                print(f"Found metrics for file: {key}")
                break
        
        if not file_metric_data:
            return jsonify({'error': f'No metrics found for file: {file_name}'}), 404
        
        # Use metrics from JSON file
        potential_matches_in_index = file_metric_data.get('potential_matches_in_index', 0)
        potential_matches_in_blocking = file_metric_data.get('potential_matches_in_blocking', 0)
        potential_true_matches = potential_matches_in_blocking  # In BKAFI sets
        potential_true_matches_not_in_bkafi = potential_matches_in_index - potential_matches_in_blocking  # NOT in BKAFI sets
        
        # Threshold metrics (confidence > 0.5)
        threshold_precision = file_metric_data.get('threshold_precision', 0.0)
        threshold_recall_overall = file_metric_data.get('threshold_recall_overall', 0.0)
        threshold_recall_blocking = file_metric_data.get('threshold_recall_blocking', 0.0)
        threshold_recall_matching = file_metric_data.get('threshold_recall_matching', 0.0)
        threshold_f1_score = file_metric_data.get('threshold_f1_score', 0.0)
        threshold_true_positives = file_metric_data.get('threshold_true_positives', 0)
        threshold_false_positives = file_metric_data.get('threshold_false_positives', 0)
        threshold_false_negatives = file_metric_data.get('threshold_total_false_negatives', 0)
        threshold_false_negatives_in_blocking = file_metric_data.get('threshold_false_negatives_in_blocking', 0)
        threshold_false_negatives_not_in_blocking = file_metric_data.get('threshold_false_negatives_not_in_blocking', 0)
        
        # Best match metrics (highest confidence)
        best_match_precision = file_metric_data.get('best_match_precision', 0.0)
        best_match_recall_overall = file_metric_data.get('best_match_recall_overall', 0.0)
        best_match_recall_blocking = file_metric_data.get('best_match_recall_blocking', 0.0)
        best_match_recall_matching = file_metric_data.get('best_match_recall_matching', 0.0)
        best_match_f1_score = file_metric_data.get('best_match_f1_score', 0.0)
        best_match_true_positives = file_metric_data.get('best_match_true_positives', 0)
        best_match_false_positives = file_metric_data.get('best_match_false_positives', 0)
        best_match_false_negatives = file_metric_data.get('best_match_total_false_negatives', 0)
        best_match_false_negatives_in_blocking = file_metric_data.get('best_match_false_negatives_in_blocking', 0)
        best_match_false_negatives_not_in_blocking = file_metric_data.get('best_match_false_negatives_not_in_blocking', 0)
        
        # Calculate found true matches (true positives for threshold)
        found_true_matches = threshold_true_positives
        
        # Calculate total pairs from detailed results (need to load BKAFI cache for this)
        bkafi_by_file = get_bkafi_by_file_cache()
        if bkafi_by_file is None and DEMO_RESULTS_JSON.exists():
            with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
                results_dict = json.load(f)
            bkafi_by_file = results_dict
            cache_set_json('bkafi:by_file', results_dict)
        
        # Count total pairs for this file
        total_pairs = 0
        if bkafi_by_file and file_name in bkafi_by_file:
            for building_data in bkafi_by_file[file_name].values():
                total_pairs += len(building_data.get('possible_matches', []))
        
        # Get total buildings in file
        candidates_in_file = file_metric_data.get('candidates_in_file', 0)
        
        # Recall metrics (from threshold metrics)
        recall = threshold_recall_matching  # Matching recall for backward compatibility
        overall_recall = threshold_recall_overall
        blocking_recall = threshold_recall_blocking
        matching_recall = threshold_recall_matching
        
        # Precision metrics
        precision = threshold_precision
        precision_conf_threshold = threshold_precision
        precision_highest_conf = best_match_precision
        
        # Predicted counts (approximate from true_positives + false_positives)
        predicted_with_conf_threshold = threshold_true_positives + threshold_false_positives
        predicted_highest_conf = best_match_true_positives + best_match_false_positives
        
        # True matches not in blocking
        true_matches_not_in_blocking = threshold_false_negatives_not_in_blocking
        
        summary = {
            'total_buildings': candidates_in_file,  # Buildings in file that are in BKAFI results
            'total_buildings_in_file': candidates_in_file,  # Total candidates in file
            'potential_true_matches': potential_true_matches,  # Potential true matches IN BKAFI sets
            'potential_true_matches_not_in_bkafi': potential_true_matches_not_in_bkafi,  # Potential true matches NOT in BKAFI sets
            'buildings_with_true_match_in_bkafi': potential_matches_in_blocking,  # Buildings with true match in BKAFI blocking
            'found_true_matches': found_true_matches,
            'recall': recall,
            'precision': precision,
            'precision_conf_threshold': precision_conf_threshold,
            'precision_highest_conf': precision_highest_conf,
            'predicted_with_conf_threshold': predicted_with_conf_threshold,
            'predicted_highest_conf': predicted_highest_conf,
            'true_positive': threshold_true_positives,
            'false_positive': threshold_false_positives,
            'false_negative': threshold_false_negatives,
            'false_negative_in_blocking': threshold_false_negatives_in_blocking,
            'false_negative_not_in_blocking': threshold_false_negatives_not_in_blocking,
            'best_match_false_negative_in_blocking': best_match_false_negatives_in_blocking,
            'best_match_false_negative_not_in_blocking': best_match_false_negatives_not_in_blocking,
            'best_match_total_false_negatives': best_match_false_negatives,
            'true_matches_not_in_blocking': true_matches_not_in_blocking,
            'total_pairs': total_pairs,
            'overall_recall': overall_recall,
            'blocking_recall': blocking_recall,
            'matching_recall': matching_recall,
            'f1_score': best_match_f1_score,  # Use best match F1 score
            'best_match_precision': best_match_precision,
            'best_match_recall_overall': best_match_recall_overall,
            'best_match_recall_blocking': best_match_recall_blocking,
            'best_match_recall_matching': best_match_recall_matching,
            'best_match_f1_score': best_match_f1_score,
            'best_match_true_positives': best_match_true_positives,
            'best_match_false_positives': best_match_false_positives
        }
        
        return jsonify({
            'success': True,
            'summary': summary
        })
        
    except json.JSONDecodeError as e:
        import traceback
        print(f"Error parsing JSON: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 500
    except Exception as e:
        import traceback
        print(f"Error getting classifier summary: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)