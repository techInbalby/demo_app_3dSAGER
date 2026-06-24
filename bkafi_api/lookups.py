"""Helpers shared by the BKAFI routes.

The per-building lookup (`/api/building/bkafi/<id>` and `/api/building/matches/<id>`)
both need to (a) make sure the cache is warm and (b) find the matching cand
entry by id-with-fallback. Centralised here so the two routes can't drift."""
import json
from typing import Optional, Tuple

from lib.cache import (
    cache_set_json,
    get_bkafi_cache,
    invalidate_buildings_status_cache,
    set_bkafi_by_file_cache,
    set_bkafi_cache,
)
from lib.config import DEMO_RESULTS_JSON


def ensure_bkafi_cache_loaded() -> Optional[dict]:
    """Return the flat bkafi cache. If Redis + in-memory are both empty,
    synchronously bridge the legacy `demo_detailed_results_*.json` to flat
    form (matching `tasks._bridge_to_legacy`'s output). Returns None when
    the JSON file is missing and the cache is empty."""
    cache = get_bkafi_cache()
    if cache is not None:
        return cache
    if not DEMO_RESULTS_JSON.exists():
        return None

    with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
        results_dict = json.load(f)

    flattened: dict = {}
    for _file_name, file_buildings in results_dict.items():
        flattened.update(file_buildings)

    set_bkafi_cache(flattened)
    set_bkafi_by_file_cache(results_dict)
    cache_set_json('bkafi:flat', flattened)
    cache_set_json('bkafi:by_file', results_dict)
    invalidate_buildings_status_cache()
    return flattened


def find_building_in_bkafi(bkafi_cache: dict, numeric_id: str) -> Tuple[Optional[str], Optional[dict]]:
    """Find a cand entry by numeric_id with two fallback strategies:
      1. exact key match: bkafi_cache[numeric_id]
      2. string-compare loop: substring containment in either direction
    Returns (matched_id, building_data) on success, or (None, None)."""
    if numeric_id in bkafi_cache:
        return numeric_id, bkafi_cache[numeric_id]
    for candidate_id, building_data in bkafi_cache.items():
        cid_str = str(candidate_id)
        if cid_str == numeric_id:
            return candidate_id, building_data
        if numeric_id in cid_str or cid_str in numeric_id:
            return candidate_id, building_data
    return None, None
