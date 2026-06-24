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


@alignment_bp.route('/cand/<cand_id>', methods=['GET'])
def alignment_cand(cand_id):
    """Per-cand inspection payload: the spatial NN match plus whether it was
    in the BKAFI blocking pool. Drives the building-properties "Spatial
    alignment" callout and the comparison-window NN-slide decoration."""
    (cache_info, err) = _ensure_aligned()
    if err is not None:
        return err
    cache_dir, info = cache_info
    mbc = loaders.matches_by_cand(cache_dir) or {}

    # Find the cand entry (the file-keyed wrapper has one entry: the aligned
    # candidates filename) and pick its best (first) match — possible_matches
    # are sorted by final_score desc in stage_align.
    cand_entry = None
    for _file, by_id in mbc.items():
        if str(cand_id) in by_id:
            cand_entry = by_id[str(cand_id)]
            break
    if cand_entry is None:
        return jsonify(error=f"cand {cand_id} not found in matches_by_cand"), 404
    matches = cand_entry.get('possible_matches', [])
    if not matches:
        return jsonify(error=f"cand {cand_id} has no possible matches"), 404
    nn = matches[0]

    # Was the NN's index_id present in the original BKAFI pool? bkafi:flat
    # is the bridged per-cand dict {cid: {possible_matches: [{index_id,…},…]}}.
    pool = loaders.bkafi_pool_for_cand(cand_id)
    in_pool = bool(pool) and any(str(p.get('index_id')) == str(nn.get('index_id')) for p in pool)

    # Top-N pool by post-align final_score (BKAFI pairs ∪ NN pick, sorted).
    # The UI carousel uses this directly as its 3-pair list.
    try:
        limit = max(1, min(int(request.args.get('pool_limit', 3)), 20))
    except (TypeError, ValueError):
        limit = 3
    pool = cand_entry.get('pool', [])
    pool_top = pool[:limit]

    return jsonify({
        'cand_id': str(cand_id),
        'nn_match': {
            'index_id':        nn.get('index_id'),
            'distance_m':      nn.get('distance_m'),
            'final_score':     nn.get('final_score'),
            'predicted_label': nn.get('predicted_label'),
            'true_label':      nn.get('true_label'),
        },
        'pool':                pool_top,
        'pool_total':          len(pool),
        'in_blocking_pool':    in_pool,
        'alignment_succeeded': bool(info.get('alignment_succeeded')),
        'match_threshold':     info.get('match_threshold'),
        # Read the cutoff that was actually used (persisted to alignment_info.json
        # by stage_align). Falls back to the upstream default only if the field
        # is missing on an old cache.
        'cutoff_m':            info.get('cutoff_m', 7.0),
    })


@alignment_bp.route('/cand/<cand_id>/cityjson', methods=['GET'])
def alignment_cand_cityjson(cand_id):
    """Per-cand single-building CityJSON sliced out of post_disaster_cands.json.
    Drives the "Post-disaster" toggle in the three.js comparison window so the
    user can see the geometry the model actually saw."""
    stage = request.args.get('stage', 'post_disaster').lower()
    if stage != 'post_disaster':
        return jsonify(error="stage must be 'post_disaster' (only one supported today)"), 400
    (cache_info, err) = _ensure_aligned()
    if err is not None:
        return err
    cache_dir, _ = cache_info
    payload = loaders.slice_cand_from_post_disaster(cache_dir, cand_id)
    if payload is None:
        return jsonify(error=f"cand {cand_id} not found in post_disaster_cands.json"), 404
    damage = loaders.damage_factor_for_cand(cache_dir, cand_id)
    if damage is not None:
        payload.setdefault('metadata', {})['damage_factor'] = damage
    return jsonify(payload)


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
