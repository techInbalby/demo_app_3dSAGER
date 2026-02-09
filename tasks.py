import os
import json
import re
from pathlib import Path

import numpy as np
import joblib
import pandas as pd
from celery import Celery
import redis

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
RESULTS_DIR = BASE_DIR / 'results_demo'

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
