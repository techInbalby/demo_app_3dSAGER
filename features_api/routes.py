"""
Flask routes for the legacy feature-engineering surface.

Endpoints (registered under /api/):

    POST /api/features/calculate          — kick off feature calc for a file
    GET  /api/building/features/<id>      — get features for one building

The new pipeline (`/api/pipeline/start?stage=features`) supersedes the
POST /features/calculate path; the route is kept for back-compat with
the demo's older UI hooks. Both routes share the joblib/parquet
fallback chain in features_api.loaders.

Note: this blueprint registers two URL prefixes — /api/features for
the POST and /api/building/features for the GET. App.py picks them up
explicitly when registering the blueprint.
"""
import traceback

from flask import Blueprint, jsonify, request

from lib.cache import (
    cache_get_json,
    cache_set_json,
    features_cache,
    get_features_cache,
    get_redis_client,
    invalidate_buildings_status_cache,
)
from lib.config import DATA_DIR, FEATURES_PARQUET
from lib.id_utils import extract_numeric_id
from tasks import calculate_features as calculate_features_task

from .loaders import (
    build_features_from_joblib,
    build_features_from_parquet,
    find_features_for_building,
)

features_api_bp = Blueprint('features_api', __name__)

# Legacy joblib path used as fallback when the parquet isn't present.
LEGACY_JOBLIB_PATH = DATA_DIR / 'property_dicts' / 'Hague_demo_130425_demo_inference_vector_normalization=True_seed=1.joblib'


def _load_features_for_file(file_path: str):
    """Read features from parquet (preferred) or joblib (fallback). Persists
    to Redis + in-memory cache + the buildings-status invalidation stamp."""
    if FEATURES_PARQUET.exists():
        building_features = build_features_from_parquet(FEATURES_PARQUET)
    else:
        building_features = build_features_from_joblib(LEGACY_JOBLIB_PATH)
        if building_features is None:
            return None
    features_cache[file_path] = building_features
    cache_set_json(f'features:{file_path}', building_features)
    cache_set_json(f'features_ids:{file_path}', list(building_features.keys()))
    invalidate_buildings_status_cache(file_path)
    return building_features


# ---------------------------------------------------------------------------
# POST /api/features/calculate
# ---------------------------------------------------------------------------

@features_api_bp.route('/features/calculate', methods=['POST'])
def calculate_all_features():
    """Calculate (or load from cache) geometric features for all buildings
    in the selected file. Returns synchronously when cached; otherwise
    enqueues the legacy Celery task and returns 202 with the job id."""
    try:
        data = request.get_json() or {}
        file_path = data.get('file_path', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400

        # Already cached in-process?
        if file_path in features_cache:
            return jsonify({
                'success': True,
                'message': f'Features already cached for {file_path}',
                'building_count': len(features_cache[file_path]),
            })

        # In Redis already? (cheap ID list, not full feature values)
        features_ids = cache_get_json(f'features_ids:{file_path}')
        if features_ids is not None:
            return jsonify({
                'success': True,
                'message': f'Features already cached for {file_path}',
                'building_count': len(features_ids),
            })

        # Celery worker available → kick the legacy task. The frontend polls
        # /api/jobs/<id> for completion.
        if get_redis_client():
            job = calculate_features_task.delay(file_path)
            return jsonify({
                'job_id': job.id,
                'status': 'queued',
                'message': 'Feature calculation queued',
            }), 202

        # No Celery → fall back to synchronous load (slow, but at least works
        # in non-Docker dev where Redis may be down).
        building_features = _load_features_for_file(file_path)
        if building_features is None:
            return jsonify({'error': f'Joblib file not found at {LEGACY_JOBLIB_PATH}'}), 404
        return jsonify({
            'success': True,
            'message': f'Features loaded for {file_path}',
            'building_count': len(building_features),
        })

    except Exception as e:
        print(f"Error calculating features: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/building/features/<id>
# ---------------------------------------------------------------------------

@features_api_bp.route('/building/features/<building_id>', methods=['GET'])
def get_building_features(building_id):
    """Fetch the feature dict for a single building. Tries the in-memory
    cache first, then Redis (via get_features_cache), and finally a fresh
    parquet/joblib load. Six ID-match strategies are tried in
    find_features_for_building — the demo's per-building viewer relies on
    matching CityJSON 1.1 (`NL.IMBAG.Pand.<n>-0`), CityJSON 2.0 (`bag_<n>`),
    and raw numeric forms against whichever shape the cache happens to use."""
    try:
        file_path = request.args.get('file', '')

        numeric_id = extract_numeric_id(building_id) or (
            building_id.split('_')[-1] if '_' in str(building_id) else str(building_id)
        )
        numeric_id = str(numeric_id)

        # 1. Hot path: in-memory or Redis cache.
        building_features = get_features_cache(file_path)
        if isinstance(building_features, dict):
            matched_id, features = find_features_for_building(building_features, building_id, numeric_id)
            if features is not None:
                return jsonify({'building_id': building_id, 'features': features})

        # 2. Cold path: load from parquet or joblib and try again.
        building_features = _load_features_for_file(file_path)
        if building_features is None:
            return jsonify({'error': f'Joblib file not found at {LEGACY_JOBLIB_PATH}', 'features': {}}), 404

        matched_id, features = find_features_for_building(building_features, building_id, numeric_id)
        if features is not None:
            return jsonify({'building_id': building_id, 'features': features})

        # 3. Not found anywhere — return the legacy empty-with-message shape.
        return jsonify({
            'building_id': building_id,
            'features': {},
            'message': (f'Building {building_id} not found in feature dataset. '
                        'This building may not have geometric features calculated.'),
            'found': False,
        })

    except Exception as e:
        print(f"Error getting features: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e), 'features': {}}), 500
