"""
Flask routes serving Step-4 ("Spatial alignment") artifacts.

All endpoints read from the current input_hash's cache dir, populated by the
inference pipeline. If the pipeline hasn't completed alignment yet, the routes
return 409 Conflict so the frontend can prompt the user to run Step 4 first.
"""

from flask import Blueprint, jsonify, request, send_file

from . import loaders

alignment_bp = Blueprint('alignment', __name__)


def _ensure_aligned() -> tuple:
    """Return (cache_dir, alignment_info) or (None, error_response) tuple."""
    cache_dir = loaders.current_cache_dir()
    info = loaders.alignment_info(cache_dir)
    if info is None:
        return None, (jsonify(error='Alignment has not run yet. POST /api/pipeline/start '
                                    'with stage=alignment first.'), 409)
    return (cache_dir, info), None


@alignment_bp.route('/status', methods=['GET'])
def alignment_status():
    return jsonify(loaders.status())


@alignment_bp.route('/anchors', methods=['GET'])
def alignment_anchors():
    (cache_info, err) = _ensure_aligned()
    if err is not None:
        return err
    cache_dir, _ = cache_info
    payload = loaders.anchor_pairs(cache_dir)
    if payload is None:
        return jsonify(error='anchor_pairs.json missing'), 404

    try:
        limit = int(request.args.get('limit', 0))
    except (TypeError, ValueError):
        limit = 0

    anchors = payload.get('anchors', [])
    return jsonify({
        'confidence_threshold': payload.get('confidence_threshold'),
        'total': len(anchors),
        'returned': min(limit, len(anchors)) if limit > 0 else len(anchors),
        'anchors': anchors[:limit] if limit > 0 else anchors,
    })


@alignment_bp.route('/matches/by_cand', methods=['GET'])
def alignment_matches_by_cand():
    (cache_info, err) = _ensure_aligned()
    if err is not None:
        return err
    cache_dir, _ = cache_info
    payload = loaders.matches_by_cand(cache_dir)
    if payload is None:
        return jsonify(error='matches_by_cand.json missing'), 404
    return jsonify(payload)


@alignment_bp.route('/matches/summary', methods=['GET'])
def alignment_matches_summary():
    (cache_info, err) = _ensure_aligned()
    if err is not None:
        return err
    cache_dir, _ = cache_info
    payload = loaders.metrics_summary(cache_dir)
    if payload is None:
        return jsonify(error='metrics_summary.json missing (no same-ID pairs in blocking)'), 404
    return jsonify(payload)


@alignment_bp.route('/cityjson', methods=['GET'])
def alignment_cityjson():
    stage = request.args.get('stage', '').lower()
    if stage not in ('misaligned', 'aligned'):
        return jsonify(error="stage must be 'misaligned' or 'aligned'"), 400
    cache_dir = loaders.current_cache_dir()
    path = loaders.cityjson_path(stage, cache_dir=cache_dir)
    if path is None:
        return jsonify(error=f"CityJSON for stage '{stage}' missing — run alignment first"), 409
    return send_file(str(path), mimetype='application/json',
                     as_attachment=False, conditional=True)


@alignment_bp.route('/buildings/colors', methods=['GET'])
def alignment_buildings_colors():
    stage = request.args.get('stage', '').lower()
    if stage not in ('4a', '4b', '4c', '4d'):
        return jsonify(error="stage must be one of 4a, 4b, 4c, 4d"), 400
    (cache_info, err) = _ensure_aligned()
    if err is not None:
        return err
    cache_dir, info = cache_info
    payload = loaders.build_sub_stage_colors(
        stage,
        seed=info.get('seed', 1),
        cache_dir=cache_dir,
        match_threshold=info.get('match_threshold', 0.65),
    )
    payload['stage'] = stage
    return jsonify(payload)
