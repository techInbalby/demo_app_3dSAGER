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
from status_api import status_api_bp

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
app.register_blueprint(status_api_bp, url_prefix='/api')

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


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)