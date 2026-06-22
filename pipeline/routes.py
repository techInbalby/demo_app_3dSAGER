"""
Flask routes for the online inference pipeline.

Endpoints (registered under /api/pipeline):

    POST   /start             body {"stage": features|blocking|matching|alignment}
    GET    /status/<task_id>
    GET    /manifest
    DELETE /cache
"""

from flask import Blueprint, jsonify, request
from celery.result import AsyncResult

# .cache must come first — it adds the standalone pipeline bundle to sys.path
# before we try to `import pipeline_stages`.
from .cache import (
    CACHE_ROOT,
    get_cache_dir,
    get_current_hash,
    get_locked_input_paths,
    wipe_cache,
)

import pipeline_stages   # noqa: E402  (must follow .cache import)
import tasks            # noqa: E402  the app's own tasks.py

pipeline_bp = Blueprint('pipeline', __name__)


# UI step name → pipeline_stages target. The UI step's prerequisites are run
# transitively by run_through(), so callers only need to name the step they want.
STAGE_MAP = {
    'features':  'properties',
    'blocking':  'blocking',
    'matching':  'classify',
    'alignment': 'align',
}


@pipeline_bp.route('/start', methods=['POST'])
def pipeline_start():
    body = request.get_json(silent=True) or {}
    ui_stage = body.get('stage') or request.args.get('stage')
    if ui_stage not in STAGE_MAP:
        return jsonify(error=f"unknown stage '{ui_stage}'. "
                             f"Valid: {list(STAGE_MAP)}"), 400
    target_stage = STAGE_MAP[ui_stage]

    try:
        cands_path, index_path = get_locked_input_paths()
    except (FileNotFoundError, RuntimeError) as e:
        return jsonify(error=str(e)), 500

    input_hash = pipeline_stages.compute_input_hash(cands_path, index_path)
    cache_dir = get_cache_dir(input_hash)

    # Optional user-tunable knobs. Per-stage cache-hit gates compare against
    # the recorded values; mismatch → recompute that stage (and downstream
    # outputs for blocking).
    nn_count = body.get('nn_count')
    cutoff_m = body.get('post_align_knn_cutoff')
    try:
        nn_count = int(nn_count) if nn_count is not None else None
    except (TypeError, ValueError):
        nn_count = None
    try:
        cutoff_m = float(cutoff_m) if cutoff_m is not None else None
    except (TypeError, ValueError):
        cutoff_m = None

    task_kwargs = {
        'target_stage': target_stage,
        'cands_path': cands_path,
        'index_path': index_path,
        'input_hash': input_hash,
    }
    if nn_count is not None: task_kwargs['nn_count'] = nn_count
    if cutoff_m is not None: task_kwargs['post_align_knn_cutoff'] = cutoff_m

    task = tasks.pipeline_run.delay(**task_kwargs)
    return jsonify({
        'task_id': task.id,
        'ui_stage': ui_stage,
        'target_stage': target_stage,
        'input_hash': input_hash,
        'cache_dir': str(cache_dir),
    })


@pipeline_bp.route('/status/<task_id>', methods=['GET'])
def pipeline_status(task_id):
    res = AsyncResult(task_id, app=tasks.celery)
    out = {'task_id': task_id, 'state': res.state}
    if res.state in ('PENDING', 'RECEIVED'):
        return jsonify(out)
    if res.state in ('STARTED', 'PROGRESS'):
        info = res.info if isinstance(res.info, dict) else {}
        out.update({
            'stage': info.get('stage'),
            'message': info.get('message'),
            'elapsed_s': info.get('elapsed_s'),
        })
    elif res.state == 'SUCCESS':
        out['result'] = res.result
    elif res.state == 'FAILURE':
        # res.info is the exception
        out['error'] = repr(res.info) if res.info is not None else 'unknown error'
    return jsonify(out)


@pipeline_bp.route('/manifest', methods=['GET'])
def pipeline_manifest():
    try:
        input_hash = get_current_hash()
    except (FileNotFoundError, RuntimeError) as e:
        return jsonify(error=str(e)), 500
    cache_dir = CACHE_ROOT / input_hash
    manifest = pipeline_stages.read_manifest(cache_dir)
    return jsonify({
        'input_hash': input_hash,
        'cache_dir': str(cache_dir),
        'cache_exists': cache_dir.exists(),
        **manifest,
    })


@pipeline_bp.route('/cache', methods=['DELETE'])
def pipeline_cache_clear():
    try:
        cache_dir = wipe_cache()
    except (FileNotFoundError, RuntimeError) as e:
        return jsonify(error=str(e)), 500
    return jsonify({'cleared': str(cache_dir)})
