import os
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import joblib
import pandas as pd
from celery import Celery
import redis

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
RESULTS_DIR = BASE_DIR / 'results_demo'
PIPELINE_CACHE_ROOT = RESULTS_DIR / 'cache'

# Make the bundled inference pipeline importable.
_PIPELINE_DIR = BASE_DIR / 'demo_infrance_pipeline'
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))
# Make scripts/ importable so we can call prebake_file() directly on stage_align outputs.
_SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

DEMO_RESULTS_JSON = RESULTS_DIR / 'demo_inference' / 'demo_detailed_results_XGBClassifier_seed1.json'
JOBLIB_PATH = DATA_DIR / 'property_dicts' / 'Hague_demo_130425_demo_inference_vector_normalization=True_seed=1.joblib'
PARQUET_PATH = DATA_DIR / 'property_dicts' / 'features.parquet'

REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
DEFAULT_CACHE_TTL = int(os.getenv('CACHE_TTL_SECONDS', '21600'))

celery = Celery('tasks', broker=REDIS_URL, backend=REDIS_URL)
celery.conf.update(task_track_started=True)


def _redis_client():
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _json_default(value):
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def _cache_set_json(key, payload, ttl=DEFAULT_CACHE_TTL):
    client = _redis_client()
    client.set(key, json.dumps(payload, default=_json_default), ex=ttl)


def _cache_get_json(key):
    client = _redis_client()
    raw = client.get(key)
    if not raw:
        return None
    return json.loads(raw)


def _build_features_from_parquet(parquet_path: Path):
    df = pd.read_parquet(parquet_path)
    building_features = {}
    for row in df.itertuples(index=False):
        building_id = str(row.building_id)
        feature_name = str(row.feature_name)
        value = row.value
        building_features.setdefault(building_id, {})[feature_name] = value
    return building_features


@celery.task(name='tasks.calculate_features')
def calculate_features(file_path):
    if PARQUET_PATH.exists():
        building_features = _build_features_from_parquet(PARQUET_PATH)
        cache_key = f'features:{file_path}'
        _cache_set_json(cache_key, building_features)
        _cache_set_json(f'features_ids:{file_path}', list(building_features.keys()))
        return {
            'cache_key': cache_key,
            'building_count': len(building_features)
        }

    if not JOBLIB_PATH.exists():
        raise FileNotFoundError(f'Joblib file not found at {JOBLIB_PATH}')

    with open(JOBLIB_PATH, 'rb') as f:
        property_dicts = joblib.load(f)

    building_ids = set()
    if isinstance(property_dicts, dict) and len(property_dicts) > 0:
        first_feature = list(property_dicts.values())[0]
        if isinstance(first_feature, dict) and 'cands' in first_feature:
            building_ids = set(str(bid) for bid in first_feature['cands'].keys())

    building_features = {}
    for building_id in building_ids:
        building_id_str = str(building_id)
        building_features[building_id_str] = {}
        for feature_name, feature_data in property_dicts.items():
            if isinstance(feature_data, dict) and 'cands' in feature_data:
                cands_dict = feature_data['cands']
                key_to_use = None
                if building_id_str in cands_dict:
                    key_to_use = building_id_str
                else:
                    for key in cands_dict.keys():
                        if str(key) == building_id_str:
                            key_to_use = key
                            break
                if key_to_use is not None:
                    value = cands_dict[key_to_use]
                    if isinstance(value, (np.integer, np.floating)):
                        value = float(value)
                    elif isinstance(value, np.ndarray):
                        value = value.tolist()
                    building_features[building_id_str][feature_name] = value

    cache_key = f'features:{file_path}'
    _cache_set_json(cache_key, building_features)
    # Store compact ID-only list so /api/buildings/status avoids loading 6.5 MB of features
    _cache_set_json(f'features_ids:{file_path}', list(building_features.keys()))

    return {
        'cache_key': cache_key,
        'building_count': len(building_features)
    }


@celery.task(name='tasks.load_bkafi_results')
def load_bkafi_results():
    if not DEMO_RESULTS_JSON.exists():
        raise FileNotFoundError(f'BKAFI results file not found at {DEMO_RESULTS_JSON}')

    with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
        results_dict = json.load(f)

    flattened_cache = {}
    total_pairs = 0
    unique_candidates = 0

    for file_name, file_buildings in results_dict.items():
        for building_id, building_data in file_buildings.items():
            flattened_cache[building_id] = building_data
            unique_candidates += 1
            total_pairs += len(building_data.get('possible_matches', []))

    _cache_set_json('bkafi:flat', flattened_cache)
    _cache_set_json('bkafi:by_file', results_dict)

    return {
        'cache_key_flat': 'bkafi:flat',
        'cache_key_by_file': 'bkafi:by_file',
        'total_pairs': int(total_pairs),
        'unique_candidates': int(unique_candidates)
    }


# ---------------------------------------------------------------------------- #
# Online inference pipeline — Step 1–4 driver                                  #
# ---------------------------------------------------------------------------- #

@celery.task(bind=True, name='tasks.pipeline.run')
def pipeline_run(self, target_stage, cands_path, index_path, input_hash,
                 seed=1, match_threshold=0.65, apply_disaster=True):
    """
    Run pipeline stages up to and including `target_stage`. Each stage is
    individually cache-aware, so unnecessary work is skipped on re-runs.

    After stage_align the two generated CityJSON files (post_disaster_cands
    and aligned_candidates_seed{N}) are pre-baked in place so the viewer can
    fetch the WGS84 variant directly.
    """
    # Import lazily so the celery module can be imported in environments that
    # don't have the full pipeline dependency tree (e.g. CI for the web layer
    # alone). When this task actually runs in the worker, all deps are present.
    import pipeline_stages
    import prebake_cityjson

    cache_dir = PIPELINE_CACHE_ROOT / input_hash
    cache_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()

    def progress_cb(stage, message):
        self.update_state(
            state='PROGRESS',
            meta={
                'stage': stage,
                'message': message,
                'elapsed_s': round(time.time() - started_at, 1),
            },
        )

    summary = pipeline_stages.run_through(
        target_stage, cache_dir,
        cands_path=cands_path,
        index_path=index_path,
        seed=seed,
        match_threshold=match_threshold,
        apply_disaster=apply_disaster,
        progress_cb=progress_cb,
    )

    # Pre-bake the alignment-stage CityJSON outputs so the viewer's WGS84
    # fast-path is ready when the frontend asks for them.
    if target_stage == 'align':
        progress_cb('align', 'pre-baking aligned CityJSON outputs')
        for stub in [
            'post_disaster_cands.json',
            f'aligned_candidates_seed{seed}.json',
        ]:
            p = cache_dir / stub
            if p.exists():
                try:
                    prebake_cityjson.prebake_file(p)
                except Exception as e:
                    # Don't fail the whole run if prebake fails — viewer can fall back to raw.
                    print(f"[pipeline_run] prebake of {p.name} failed: {e}")

    # Bridge new-pipeline outputs to the legacy demo's Redis + JSON consumers
    # so the existing /api/building/*, /api/buildings/status,
    # /api/classifier/summary routes keep working unchanged.
    progress_cb(target_stage, 'bridging to legacy demo routes')
    try:
        _bridge_to_legacy(cache_dir, cands_path, target_stage)
    except Exception as e:
        print(f"[pipeline_run] legacy bridging failed (non-fatal): {e}")

    summary['input_hash'] = input_hash
    summary['elapsed_s'] = round(time.time() - started_at, 1)
    return summary


# ---------------------------------------------------------------------------- #
# Legacy bridging — translate cache_dir outputs into the Redis keys + JSON     #
# files the existing /api/* routes expect.                                      #
# ---------------------------------------------------------------------------- #

_LEGACY_THRESHOLD = 0.5   # matches CONFIDENCE_THRESHOLD in app.py


def _bridge_to_legacy(cache_dir: Path, cands_path: str, target_stage: str) -> None:
    cands_basename = Path(cands_path).name

    # The frontend may pass either the basename or the relative path under
    # data/ for a given file. Mirror both so legacy lookups hit.
    keys = [
        cands_basename,
        f"RawCitiesData/The Hague/Source A/{cands_basename}",
    ]

    prop_path = cache_dir / "property_dict.joblib"
    if prop_path.exists():
        property_dict = joblib.load(prop_path)
        building_features = _transpose_property_dict(property_dict)
        ids = list(building_features.keys())
        for k in keys:
            _cache_set_json(f'features:{k}', building_features)
            _cache_set_json(f'features_ids:{k}', ids)

    scored_path = cache_dir / "scored_pairs.joblib"
    if scored_path.exists() and target_stage in ('classify', 'align'):
        scored_pairs = joblib.load(scored_path)
        bkafi_flat = _scored_to_bkafi_flat(scored_pairs)
        _cache_set_json('bkafi:flat', bkafi_flat)
        _cache_set_json('bkafi:by_file', {cands_basename: bkafi_flat})

        metrics_payload = _compute_legacy_metrics(scored_pairs, cands_basename)
        legacy_metrics_path = RESULTS_DIR / 'demo_inference' / 'demo_metrics_summary_seed1.json'
        legacy_metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with open(legacy_metrics_path, 'w', encoding='utf-8') as f:
            json.dump(metrics_payload, f, indent=2, default=_json_default)

        # The legacy /api/bkafi/load task also writes detailed_results JSON; keep
        # it in sync so anything else reading that file finds fresh data.
        detailed = {cands_basename: bkafi_flat}
        legacy_detailed_path = RESULTS_DIR / 'demo_inference' / 'demo_detailed_results_XGBClassifier_seed1.json'
        with open(legacy_detailed_path, 'w', encoding='utf-8') as f:
            json.dump(detailed, f, default=_json_default)


def _transpose_property_dict(property_dict: dict) -> dict:
    """{attr: {'cands': {bid: val}, 'index': {...}}} -> {bid: {attr: val}}."""
    if not property_dict:
        return {}
    first_attr = next(iter(property_dict.values()))
    cands_dict = first_attr.get('cands', {}) if isinstance(first_attr, dict) else {}
    out = {str(bid): {} for bid in cands_dict.keys()}
    for attr_name, sides in property_dict.items():
        if not isinstance(sides, dict):
            continue
        for raw_bid, value in sides.get('cands', {}).items():
            bid = str(raw_bid)
            if isinstance(value, (np.integer, np.floating)):
                value = float(value)
            elif isinstance(value, np.ndarray):
                value = value.tolist()
            out.setdefault(bid, {})[attr_name] = value
    return out


def _scored_to_bkafi_flat(scored_pairs) -> dict:
    """List of (cand_id, index_id, score) → {cand_id: {possible_matches: [...]}}."""
    by_cand = {}
    for cid, iid, score in scored_pairs:
        cand_key = str(cid)
        entry = by_cand.setdefault(cand_key, {'possible_matches': []})
        score_f = float(score)
        entry['possible_matches'].append({
            'index_id': str(iid),
            'confidence': score_f,
            'predicted_label': 1 if score_f >= _LEGACY_THRESHOLD else 0,
            'true_label': 1 if str(cid) == str(iid) else 0,
        })
    for cand_key in by_cand:
        by_cand[cand_key]['possible_matches'].sort(key=lambda m: -m['confidence'])
    return by_cand


def _compute_legacy_metrics(scored_pairs, cands_basename: str) -> dict:
    tp = fp = fn = 0
    cand_set = set()
    pos_pairs = 0
    for cid, iid, score in scored_pairs:
        cand_set.add(str(cid))
        same = str(cid) == str(iid)
        if same:
            pos_pairs += 1
        predicted = float(score) >= _LEGACY_THRESHOLD
        if predicted and same:
            tp += 1
        elif predicted and not same:
            fp += 1
        elif not predicted and same:
            fn += 1
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = (2 * precision * recall) / max(precision + recall, 1e-9)
    per_file = {
        'candidates_in_file': len(cand_set),
        'potential_matches_in_index': pos_pairs,
        'potential_matches_in_blocking': pos_pairs,
        'threshold_precision': round(precision, 4),
        'threshold_recall_overall': round(recall, 4),
        'threshold_recall_blocking': round(recall, 4),
        'threshold_recall_matching': round(recall, 4),
        'threshold_f1_score': round(f1, 4),
        'threshold_true_positives': tp,
        'threshold_false_positives': fp,
        'threshold_false_negatives': fn,
        'threshold_total_false_negatives': fn,
        'threshold_false_negatives_in_blocking': fn,
        'threshold_false_negatives_not_in_blocking': 0,
        'best_match_precision': round(precision, 4),
        'best_match_recall_overall': round(recall, 4),
        'best_match_recall_blocking': round(recall, 4),
        'best_match_recall_matching': round(recall, 4),
        'best_match_f1_score': round(f1, 4),
        'best_match_true_positives': tp,
        'best_match_false_positives': fp,
        'best_match_false_negatives': fn,
        'best_match_total_false_negatives': fn,
        'best_match_false_negatives_in_blocking': fn,
        'best_match_false_negatives_not_in_blocking': 0,
    }
    return {
        'XGBClassifier': {
            'model_name': 'XGBClassifier',
            **per_file,
            'file_metrics': {cands_basename: per_file},
        }
    }
