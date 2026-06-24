"""Loaders for the legacy feature stores (parquet + joblib).

`features:<file>` Redis keys + the in-memory `features_cache` dict are the
hot path — these loaders are only hit on a cold cache. Once Step 1
(/api/pipeline/start?stage=features) has run for the locked Source A
file, both are populated and these loaders aren't called again until the
cache is wiped.
"""
from pathlib import Path
from typing import Dict, Optional

import pandas as pd


def build_features_from_parquet(parquet_path: Path) -> Dict[str, dict]:
    """Reshape the feature-major parquet into a building-major dict.

    Parquet schema: rows of (building_id, feature_name, value).
    Returns: {building_id: {feature_name: value, ...}, ...}.
    """
    df = pd.read_parquet(parquet_path)
    building_features: Dict[str, dict] = {}
    for row in df.itertuples(index=False):
        building_id = str(row.building_id)
        feature_name = str(row.feature_name)
        building_features.setdefault(building_id, {})[feature_name] = row.value
    return building_features


def build_features_from_joblib(joblib_path: Path) -> Optional[Dict[str, dict]]:
    """Reshape the legacy joblib property dict into a building-major dict.

    The joblib file has the inverse layout — feature-major: `{feature_name:
    {'cands': {building_id: value, ...}, 'index': {...}, ...}}`. We pivot to
    `{building_id: {feature_name: value, ...}}` and only retain the 'cands'
    side. Returns None if the file doesn't exist; raises on read errors so
    the caller can surface a 500."""
    import joblib
    import numpy as np

    if not joblib_path.exists():
        return None

    with open(joblib_path, 'rb') as f:
        property_dicts = joblib.load(f)

    # Discover the universe of cand building ids from the first feature's 'cands' key.
    building_ids = set()
    if isinstance(property_dicts, dict) and property_dicts:
        first_feature = next(iter(property_dicts.values()))
        if isinstance(first_feature, dict) and 'cands' in first_feature:
            building_ids = {str(bid) for bid in first_feature['cands'].keys()}

    building_features: Dict[str, dict] = {bid: {} for bid in building_ids}

    for feature_name, feature_data in property_dicts.items():
        if not (isinstance(feature_data, dict) and 'cands' in feature_data):
            continue
        cands_dict = feature_data['cands']
        for bid in building_ids:
            # Look up bid by string match; the joblib keys are sometimes
            # numpy scalars which compare as strings via str().
            key = bid if bid in cands_dict else next(
                (k for k in cands_dict.keys() if str(k) == bid), None
            )
            if key is None:
                continue
            value = cands_dict[key]
            if isinstance(value, (np.integer, np.floating)):
                value = float(value)
            elif isinstance(value, np.ndarray):
                value = value.tolist()
            building_features[bid][feature_name] = value

    return building_features


def find_features_for_building(building_features: Dict[str, dict], building_id, numeric_id: str):
    """Search the building_features dict for an entry matching the requested
    id, using six fallback strategies (most-specific to most-permissive):

      1. exact: building_id (the raw string from the URL)
      2. exact: numeric_id  (10+ digit BAG extracted from building_id)
      3. id variants: numeric_id, numeric_id.lstrip('0'), numeric_id.zfill(16)
      4. variant ⊂/⊃ cached_id (substring containment, bidirectional)
      5. variant endswith / cached_id endswith (suffix match)
      6. global pass: numeric_id ⊂ cached_id OR cached_id ⊂ numeric_id

    Returns (cached_id_that_matched, features_dict) on success, or (None, None)
    if nothing matches. The legacy demo's per-building viewer depends on
    finding a match for every cand the user clicks, even when IDs come in as
    `NL.IMBAG.Pand.<n>-0` from CityJSON 1.1 or `bag_<n>` from CityJSON 2.0.

    Caller is expected to have already done the `if isinstance(...)` check.
    """
    # 1. Exact match on the raw URL building_id (rarely hits — usually the
    #    URL form has a `bag_` or `NL.IMBAG.Pand.` prefix the dict lacks).
    if building_id in building_features:
        return building_id, building_features[building_id]

    # 2. Exact match on the extracted numeric.
    if numeric_id in building_features:
        return numeric_id, building_features[numeric_id]

    # 3-5. Variants × cached ids.
    variants = [numeric_id, numeric_id.lstrip('0'), numeric_id.zfill(16)]
    for variant in variants:
        if variant in building_features:
            return variant, building_features[variant]
        for cached_id, cached_features in building_features.items():
            cached_id_str = str(cached_id)
            if cached_id_str == variant:
                return cached_id, cached_features
            # Substring containment, bidirectional.
            if variant in cached_id_str or cached_id_str in variant:
                return cached_id, cached_features
            # Suffix match.
            if variant.endswith(cached_id_str) or cached_id_str.endswith(variant):
                return cached_id, cached_features

    # 6. Final permissive pass on numeric_id (catches edge cases where the
    #    cached id has surprise embedded digits).
    for cached_id, cached_features in building_features.items():
        cached_id_str = str(cached_id)
        if numeric_id in cached_id_str or cached_id_str in numeric_id:
            return cached_id, cached_features

    return None, None
