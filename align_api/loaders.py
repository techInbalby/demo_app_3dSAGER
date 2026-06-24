"""
Cached file readers for alignment artifacts living under
results_demo/cache/<input_hash>/.

Each load is keyed by (path, mtime) so a fresh pipeline run automatically
invalidates the in-memory entry. Files are small (each <5 MB) so we keep them
entirely in memory.
"""

import json
import os
import threading
from pathlib import Path
from typing import Optional

# Sister package - reuse its cache helpers to find the current cache dir.
from pipeline.cache import CACHE_ROOT, get_current_hash

_LOCK = threading.Lock()
_CACHE: dict = {}   # path_str → (mtime_ns, payload)


def _read_json_cached(path: Path) -> Optional[dict]:
    p = Path(path)
    if not p.exists():
        return None
    st = p.stat()
    key = str(p)
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is not None and entry[0] == st.st_mtime_ns:
            return entry[1]
    with open(p, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    with _LOCK:
        _CACHE[key] = (st.st_mtime_ns, payload)
    return payload


def current_cache_dir() -> Path:
    """Cache dir for the current locked inputs."""
    return CACHE_ROOT / get_current_hash()


def alignment_info(cache_dir: Optional[Path] = None) -> Optional[dict]:
    cache_dir = cache_dir or current_cache_dir()
    return _read_json_cached(cache_dir / 'alignment_info.json')


def anchor_pairs(cache_dir: Optional[Path] = None) -> Optional[dict]:
    cache_dir = cache_dir or current_cache_dir()
    return _read_json_cached(cache_dir / 'anchor_pairs.json')


def matches_by_cand(cache_dir: Optional[Path] = None) -> Optional[dict]:
    cache_dir = cache_dir or current_cache_dir()
    return _read_json_cached(cache_dir / 'matches_by_cand.json')


def metrics_summary(cache_dir: Optional[Path] = None) -> Optional[dict]:
    cache_dir = cache_dir or current_cache_dir()
    return _read_json_cached(cache_dir / 'metrics_summary.json')


def slice_cand_from_post_disaster(cache_dir: Optional[Path], cand_id) -> Optional[dict]:
    """Extract a single-cand CityJSON 1.1 from the run's
    `post_disaster_cands.json`. Walks the cand's boundaries to collect only
    the vertices it uses and re-indexes them, so the returned payload is
    a self-contained minimal CityJSON the three.js viewer can render.
    Returns None when the cache file is missing or the cand isn't found.
    Tries both `bag_<id>` and raw `<id>` forms."""
    cache_dir = cache_dir or current_cache_dir()
    full = _read_json_cached(Path(cache_dir) / 'post_disaster_cands.json')
    if not full or 'CityObjects' not in full:
        return None
    objs = full['CityObjects']
    key = None
    for cand in (f'bag_{cand_id}', str(cand_id)):
        if cand in objs:
            key = cand
            break
    if key is None:
        return None
    cand_obj = objs[key]
    all_verts = full.get('vertices', [])

    # Walk arbitrarily-nested int boundaries; collect referenced vertex indices.
    referenced = set()
    def _walk(node):
        if isinstance(node, int):
            referenced.add(node)
        elif isinstance(node, list):
            for x in node:
                _walk(x)
    for g in cand_obj.get('geometry', []):
        _walk(g.get('boundaries'))

    # Build the index remap + the trimmed vertices array.
    old_to_new = {old: new for new, old in enumerate(sorted(referenced))}
    new_verts = [all_verts[old] for old in sorted(referenced)]
    # Shift z so the building base sits at z=0. post_disaster_cands.json
    # stores absolute EPSG:7415 coords (z ~40–50 m NAP). The pristine +
    # index paths feed CityJSON with a transform block the three.js viewer
    # applies, ending up near z=0; without this shift the post-disaster
    # cand floats above the scene's z=0 grid plane while everything else
    # rests on it. Relative heights inside the building (the damage shape)
    # are preserved.
    if new_verts:
        min_z = min(v[2] for v in new_verts)
        new_verts = [[v[0], v[1], v[2] - min_z] for v in new_verts]

    # Rewrite the geometry with remapped indices.
    def _remap(node):
        if isinstance(node, int):
            return old_to_new[node]
        if isinstance(node, list):
            return [_remap(x) for x in node]
        return node
    geom_remapped = []
    for g in cand_obj.get('geometry', []):
        g2 = dict(g)
        g2['boundaries'] = _remap(g.get('boundaries'))
        geom_remapped.append(g2)

    return {
        'type':        full.get('type', 'CityJSON'),
        'version':     full.get('version', '1.1'),
        'metadata':    full.get('metadata', {}),
        'CityObjects': {key: {**cand_obj, 'geometry': geom_remapped}},
        'vertices':    new_verts,
    }


def damage_factor_for_cand(cache_dir: Optional[Path], cand_id) -> Optional[float]:
    """Look up the per-building height-damage factor (~0.3-0.95, 1.0 = undamaged)
    from disaster_log.json. Returns None if the log or the entry is missing."""
    cache_dir = cache_dir or current_cache_dir()
    log = _read_json_cached(Path(cache_dir) / 'disaster_log.json')
    if not log:
        return None
    damage = log.get('damage_log') or log.get('damage') or {}
    for key in (str(cand_id), f'bag_{cand_id}'):
        if key in damage:
            try:
                return float(damage[key])
            except (TypeError, ValueError):
                return None
    return None


def bkafi_pool_for_cand(cand_id) -> list:
    """Return the cand's BKAFI blocking pool from the Redis-backed bkafi:flat
    dict (populated by tasks.py:_bridge_to_legacy). Each entry has at least
    {index_id, confidence, predicted_label, true_label}. Empty list if Redis
    isn't reachable or the cand isn't in the pool."""
    try:
        # Lazy import so this module stays importable in environments without
        # the full Flask app (e.g. CI smoke tests of pipeline_stages alone).
        from app import cache_get_json
    except Exception:
        return []
    flat = cache_get_json('bkafi:flat')
    if not flat or not isinstance(flat, dict):
        return []
    entry = flat.get(str(cand_id))
    if not entry:
        return []
    return list(entry.get('possible_matches', []))


def cityjson_path(stage: str, seed: int = 1,
                  cache_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Resolve the prebaked CityJSON path for sub-stage 4a (misaligned) or 4c (aligned).
    Falls back to the raw .json if the prebaked variant is missing.
    """
    cache_dir = cache_dir or current_cache_dir()
    stems = {
        'misaligned':      'post_disaster_cands',
        'aligned':         f'aligned_candidates_seed{seed}',
        'damaged_heights': 'damaged_heights_only_cands',
    }
    if stage not in stems:
        return None
    stem = stems[stage]
    prebaked = cache_dir / f'{stem}.prebaked.json'
    if prebaked.exists():
        return prebaked
    raw = cache_dir / f'{stem}.json'
    return raw if raw.exists() else None


def status(cache_dir: Optional[Path] = None) -> dict:
    """Cheap dict of which artifacts exist, plus alignment_info if available."""
    cache_dir = cache_dir or current_cache_dir()
    files = {
        'post_disaster_cands': cache_dir / 'post_disaster_cands.json',
        'aligned_candidates':  cache_dir / 'aligned_candidates_seed1.json',
        'anchor_pairs':        cache_dir / 'anchor_pairs.json',
        'matches_by_cand':     cache_dir / 'matches_by_cand.json',
        'metrics_summary':     cache_dir / 'metrics_summary.json',
        'alignment_info':      cache_dir / 'alignment_info.json',
    }
    out = {f'has_{k}': v.exists() for k, v in files.items()}
    out['cache_dir'] = str(cache_dir)
    info = alignment_info(cache_dir)
    if info is not None:
        out['alignment_info'] = info
    return out


def build_sub_stage_colors(stage: str, seed: int = 1,
                           cache_dir: Optional[Path] = None,
                           match_threshold: float = 0.65) -> dict:
    """
    Return {cand_colors: {raw_id: color_name}, index_colors: {raw_id: color_name}}
    for one of the four Step-4 sub-stages.

    Color names match BUILDING_COLOR_MAP in cesium-cityjson-viewer.js. The viewer's
    idMapping resolves bag_<id> ↔ <id>, so we key by raw IDs throughout.
    """
    cache_dir = cache_dir or current_cache_dir()
    cand_colors, index_colors = {}, {}

    if stage == '4a':
        # All known cand IDs misaligned. We don't enumerate them here (the viewer
        # will color any unmapped cand with the default fallback); 4a only needs
        # the explicit `cand_misaligned` rule applied via the matches set.
        # To keep this stateless and avoid loading the CityJSON, we use the cand
        # IDs present in matches_by_cand.json (every cand that survived blocking).
        mbc = matches_by_cand(cache_dir) or {}
        for _file, by_id in mbc.items():
            for cand_id in by_id.keys():
                cand_colors[str(cand_id)] = 'cand_misaligned'

    elif stage == '4b':
        # 4a colors + recolour anchors.
        cand_colors = build_sub_stage_colors('4a', seed=seed,
                                             cache_dir=cache_dir,
                                             match_threshold=match_threshold)['cand_colors']
        anchors_payload = anchor_pairs(cache_dir) or {}
        for entry in anchors_payload.get('anchors', []):
            cand_colors[str(entry['cand_id'])] = 'anchor_cand'
            index_colors[str(entry['index_id'])] = 'anchor_index'

    elif stage == '4c':
        # All cands snap to aligned position; reuse the default-A blue.
        mbc = matches_by_cand(cache_dir) or {}
        for _file, by_id in mbc.items():
            for cand_id in by_id.keys():
                cand_colors[str(cand_id)] = 'blue'

    elif stage == '4d':
        mbc = matches_by_cand(cache_dir) or {}
        for _file, by_id in mbc.items():
            for cand_id, payload in by_id.items():
                matches = payload.get('possible_matches', [])
                if not matches:
                    cand_colors[str(cand_id)] = 'darkgray'
                    continue
                # matches are sorted by final_score desc in stage_align.
                best = matches[0]
                predicted = int(best.get('predicted_label', 0))
                # True label across ALL pairs for this cand: any same-ID pair?
                true_match_exists = any(m.get('true_label') == 1 for m in matches)
                if predicted == 1 and true_match_exists and int(best.get('true_label', 0)) == 1:
                    cand_colors[str(cand_id)] = 'green'        # TP
                elif predicted == 1:
                    cand_colors[str(cand_id)] = 'red'          # FP
                elif true_match_exists:
                    cand_colors[str(cand_id)] = 'false_negative'  # FN
                else:
                    cand_colors[str(cand_id)] = 'darkgray'     # TN / no match

    else:
        raise ValueError(f"Unknown stage '{stage}'. Expected 4a / 4b / 4c / 4d.")

    return {'cand_colors': cand_colors, 'index_colors': index_colors}
