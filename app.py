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
from tasks import calculate_features as calculate_features_task
from tasks import load_bkafi_results as load_bkafi_task

app = Flask(__name__)
# Enable compression for all responses (gzip)
Compress(app)

# Configuration
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
RESULTS_DIR = BASE_DIR / 'results_demo'
SAVED_MODEL_DIR = BASE_DIR / 'saved_model_files'
LOGS_DIR = BASE_DIR / 'logs'

# Results JSON files
DEMO_RESULTS_JSON = RESULTS_DIR / 'demo_inference' / 'demo_detailed_results_XGBClassifier_seed1.json'
DEMO_METRICS_JSON = RESULTS_DIR / 'demo_inference' / 'demo_metrics_summary_seed1.json'
FEATURES_PARQUET = DATA_DIR / 'property_dicts' / 'features.parquet'

# Confidence threshold for predictions (hardcoded, but easy to make configurable)
CONFIDENCE_THRESHOLD = 0.5
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
CACHE_TTL_SECONDS = int(os.getenv('CACHE_TTL_SECONDS', '21600'))

_redis_client = None


def get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception:
        return None


def cache_get_json(key):
    client = get_redis_client()
    if not client:
        return None
    raw = client.get(key)
    if not raw:
        return None
    return json.loads(raw)


def cache_set_json(key, payload, ttl=CACHE_TTL_SECONDS):
    client = get_redis_client()
    if not client:
        return False
    client.set(key, json.dumps(payload), ex=ttl)
    return True


def build_features_from_parquet(parquet_path: Path):
    df = pd.read_parquet(parquet_path)
    building_features = {}
    for row in df.itertuples(index=False):
        building_id = str(row.building_id)
        feature_name = str(row.feature_name)
        value = row.value
        building_features.setdefault(building_id, {})[feature_name] = value
    return building_features


def get_features_cache(file_path):
    cached = cache_get_json(f'features:{file_path}')
    if cached is not None:
        return cached
    return features_cache.get(file_path)


def get_bkafi_cache():
    cached = cache_get_json('bkafi:flat')
    if cached is not None:
        return cached
    return bkafi_cache


def get_bkafi_by_file_cache():
    cached = cache_get_json('bkafi:by_file')
    if cached is not None:
        return cached
    return getattr(app, 'bkafi_cache_by_file', None)

# Ensure directories exist
for directory in [DATA_DIR, RESULTS_DIR, SAVED_MODEL_DIR, LOGS_DIR]:
    directory.mkdir(exist_ok=True)


@app.route('/')
def index():
    """Home page"""
    return render_template('index.html')


@app.route('/demo')
def demo():
    """Demo page with 3D viewer"""
    return render_template('demo.html')


@app.route('/api/data/files')
def get_files():
    """Get list of available CityJSON files from Source A and Source B"""
    try:
        # Try different directory name variations
        source_a_paths = [
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source A',  # With space
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceA',   # Without space
            DATA_DIR / 'Source A',
            DATA_DIR / 'SourceA',
            DATA_DIR
        ]
        
        source_b_paths = [
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source B',  # With space
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceB',   # Without space
            DATA_DIR / 'Source B',
            DATA_DIR / 'SourceB',
            DATA_DIR
        ]
        
        # Find first existing path
        source_a_path = None
        for path in source_a_paths:
            if path.exists():
                source_a_path = path
                break
        
        source_b_path = None
        for path in source_b_paths:
            if path.exists():
                source_b_path = path
                break
        
        def get_file_list(directory):
            files = []
            if directory.exists() and directory.is_dir():
                for file_path in directory.rglob('*.json'):
                    try:
                        rel_path = file_path.relative_to(DATA_DIR)
                        files.append({
                            'filename': file_path.name,
                            'path': str(rel_path),
                            'size': file_path.stat().st_size
                        })
                    except ValueError:
                        files.append({
                            'filename': file_path.name,
                            'path': str(file_path),
                            'size': file_path.stat().st_size
                        })
            return files
        
        return jsonify({
            'source_a': get_file_list(source_a_path),
            'source_b': get_file_list(source_b_path)
        })
    except Exception as e:
        return jsonify({'error': str(e), 'source_a': [], 'source_b': []}), 500


@app.route('/api/data/select', methods=['POST'])
def select_file():
    """Select a file for processing"""
    try:
        data = request.get_json()
        file_path = data.get('file_path')
        source = data.get('source', 'A')
        
        if not file_path:
            return jsonify({'success': False, 'error': 'No file path provided'}), 400
        
        full_path = DATA_DIR / file_path
        if not full_path.exists():
            alt_paths = [
                DATA_DIR / 'RawCitiesData' / 'The Hague' / file_path,
                DATA_DIR / file_path,
                Path(file_path) if os.path.isabs(file_path) else None
            ]
            for alt_path in alt_paths:
                if alt_path and alt_path.exists():
                    full_path = alt_path
                    break
            else:
                return jsonify({'success': False, 'error': f'File not found: {file_path}'}), 404
        
        import uuid
        return jsonify({
            'success': True,
            'session_id': str(uuid.uuid4()),
            'file_path': file_path,
            'source': source
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/data/file/<path:file_path>')
def get_file(file_path):
    """Get CityJSON file content"""
    try:
        from urllib.parse import unquote
        # URL decode the path manually to ensure it's decoded
        file_path = unquote(str(file_path))
        print(f"DEBUG: Requested file path: {file_path}")
        print(f"DEBUG: DATA_DIR: {DATA_DIR}")
        print(f"DEBUG: DATA_DIR exists: {DATA_DIR.exists()}")
        
        # Try multiple path combinations
        file_name = Path(file_path).name
        possible_paths = [
            DATA_DIR / file_path,  # Direct path from data directory (most common)
            # Try with "Source A" (with space)
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source A' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source B' / file_name,
            # Try with "SourceA" (without space)
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceA' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceB' / file_name,
            # Try if path doesn't include RawCitiesData prefix
            DATA_DIR / 'RawCitiesData' / 'The Hague' / file_path,
        ]
        
        # Also try if file_path already includes the full structure
        if 'RawCitiesData' in file_path or 'The Hague' in file_path:
            # Path already includes the structure, just use it directly
            possible_paths.insert(0, DATA_DIR / file_path)
        
        print(f"DEBUG: Trying {len(possible_paths)} possible paths...")
        found_path = None
        for i, path in enumerate(possible_paths):
            if path:
                exists = path.exists()
                is_file = path.is_file() if exists else False
                print(f"DEBUG: Path {i+1}: {path} - exists: {exists}, is_file: {is_file}")
                if exists and is_file:
                    found_path = path
                    print(f"DEBUG: Found file at: {found_path}")
                    break
        
        if not found_path:
            # Log available paths for debugging
            print(f"ERROR: File not found: {file_path}")
            print(f"ERROR: Tried paths: {[str(p) for p in possible_paths if p]}")
            # List what's actually in the data directory
            if DATA_DIR.exists():
                print(f"DEBUG: Contents of DATA_DIR: {list(DATA_DIR.iterdir())[:10]}")
            return jsonify({
                'error': f'File not found: {file_path}',
                'tried_paths': [str(p) for p in possible_paths if p],
                'data_dir': str(DATA_DIR),
                'data_dir_exists': DATA_DIR.exists()
            }), 404
        
        # Read and return the JSON file
        # Data is now in the image, so no OneDrive file locking issues
        print(f"DEBUG: Reading file: {found_path}")
        file_size = found_path.stat().st_size
        print(f"DEBUG: File size: {file_size} bytes")
        
        # Calculate ETag for caching (based on file path and modification time)
        mtime = found_path.stat().st_mtime
        etag = hashlib.md5(f"{found_path}_{mtime}".encode()).hexdigest()
        
        # Check if client has cached version (If-None-Match header)
        if_none_match = request.headers.get('If-None-Match')
        if if_none_match == etag:
            response = make_response('', 304)  # Not Modified
            response.headers['ETag'] = etag
            return response

        cache_key = f"cityjson:{file_path}:{etag}"
        cached_payload = cache_get_json(cache_key)
        if cached_payload is not None:
            response = jsonify(cached_payload)
            response.headers['ETag'] = etag
            response.headers['Cache-Control'] = 'public, max-age=3600'
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
            return response
        
        with open(found_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"DEBUG: Successfully loaded JSON, {len(data)} top-level keys")
            
            # Create response with caching headers
            response = jsonify(data)
            response.headers['ETag'] = etag
            response.headers['Cache-Control'] = 'public, max-age=3600'  # Cache for 1 hour
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
            cache_set_json(cache_key, data)
            return response
            
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON decode error: {e}")
        return jsonify({'error': f'Invalid JSON: {str(e)}'}), 400
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"ERROR: Exception in get_file: {e}")
        print(f"ERROR: Traceback:\n{error_trace}")
        return jsonify({
            'error': str(e),
            'traceback': error_trace
        }), 500


@app.route('/api/data/file')
def get_file_by_query():
    """Get CityJSON file content via query param to avoid encoded slashes."""
    file_path = request.args.get('path')
    if not file_path:
        return jsonify({'error': 'Missing path parameter'}), 400
    return get_file(file_path)


# Global cache for loaded features
features_cache = {}
# Global cache for BKAFI results
bkafi_cache = None

@app.route('/api/features/calculate', methods=['POST'])
def calculate_all_features():
    """
    Calculate geometric features for all buildings in the selected file
    Loads from joblib file: data/property_dicts/Hague_demo_130425_demo_inference_vector_normalization=True_seed=1.joblib
    """
    try:
        data = request.get_json()
        file_path = data.get('file_path', '')
        
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        print(f"Calculating features for all buildings in file: {file_path}")

        cache_key = f"features:{file_path}"
        cached_features = cache_get_json(cache_key)
        if cached_features is not None:
            features_cache[file_path] = cached_features
            return jsonify({
                'success': True,
                'message': f'Features already cached for {file_path}',
                'building_count': len(cached_features)
            })

        if get_redis_client():
            job = calculate_features_task.delay(file_path)
            return jsonify({
                'job_id': job.id,
                'status': 'queued',
                'message': 'Feature calculation queued'
            }), 202

        if FEATURES_PARQUET.exists():
            building_features = build_features_from_parquet(FEATURES_PARQUET)
            features_cache[file_path] = building_features
            cache_set_json(cache_key, building_features)
            return jsonify({
                'success': True,
                'message': f'Features loaded from parquet for {file_path}',
                'building_count': len(building_features)
            })
        
        # Load from joblib file
        joblib_path = DATA_DIR / 'property_dicts' / 'Hague_demo_130425_demo_inference_vector_normalization=True_seed=1.joblib'
        
        if not joblib_path.exists():
            return jsonify({'error': f'Joblib file not found at {joblib_path}'}), 404
        
        import joblib
        import numpy as np
        with open(joblib_path, 'rb') as f:
            property_dicts = joblib.load(f)
        
        print(f"Loaded property dicts from: {joblib_path}")
        print(f"Number of features: {len(property_dicts) if isinstance(property_dicts, dict) else 'unknown'}")
        
        # Extract all unique building IDs from the 'cands' dictionaries
        building_ids = set()
        if isinstance(property_dicts, dict) and len(property_dicts) > 0:
            first_feature = list(property_dicts.values())[0]
            if isinstance(first_feature, dict) and 'cands' in first_feature:
                # Convert all building IDs to strings (handle numpy string types)
                building_ids = set(str(bid) for bid in first_feature['cands'].keys())
        
        print(f"Number of unique buildings: {len(building_ids)}")
        print(f"Sample building IDs: {list(building_ids)[:5]}")
        
        # Reorganize data: convert from feature->building to building->features
        # This makes it easier to look up features for a specific building
        building_features = {}
        for building_id in building_ids:
            building_id_str = str(building_id)  # Ensure it's a string
            building_features[building_id_str] = {}
            for feature_name, feature_data in property_dicts.items():
                if isinstance(feature_data, dict) and 'cands' in feature_data:
                    # Try both string and original key format
                    cands_dict = feature_data['cands']
                    # Check if building_id exists (try as string and original format)
                    key_to_use = None
                    if building_id_str in cands_dict:
                        key_to_use = building_id_str
                    else:
                        # Try to find matching key (handle numpy string types)
                        for key in cands_dict.keys():
                            if str(key) == building_id_str:
                                key_to_use = key
                                break
                    
                    if key_to_use is not None:
                        value = cands_dict[key_to_use]
                        # Convert numpy types to Python native types
                        if isinstance(value, (np.integer, np.floating)):
                            value = float(value)
                        elif isinstance(value, np.ndarray):
                            value = value.tolist()
                        building_features[building_id_str][feature_name] = value
        
        # Store in cache (using the reorganized structure)
        features_cache[file_path] = building_features
        cache_set_json(cache_key, building_features)
        
        # Return success with count
        building_count = len(building_features)
        return jsonify({
            'success': True,
            'message': f'Features calculated for {building_count} buildings',
            'building_count': building_count
        })
            
    except Exception as e:
        import traceback
        print(f"Error calculating features: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/building/features/<building_id>')
def get_building_features(building_id):
    """
    Get geometric features for a building from cached property dicts
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        print(f"Getting features for building {building_id} from file {file_path}")
        
        # First check cache
        building_features = get_features_cache(file_path)
        if building_features:
            # Try exact match first
            if isinstance(building_features, dict) and building_id in building_features:
                features = building_features[building_id]
                print(f"Loaded features from cache for building {building_id}")
                return jsonify({'building_id': building_id, 'features': features})
            
            # Try to match by extracting numeric ID using regex (handle prefixes like "bag_", "NL.IMBAG.Pand.", etc.)
            # Extract numeric part from building_id using regex (e.g., "bag_0518100000271783" -> "0518100000271783")
            # Pattern: find a sequence of digits (10 or more digits for building IDs)
            numeric_match = re.search(r'(\d{10,})', str(building_id))
            if numeric_match:
                numeric_id = numeric_match.group(1)
            else:
                # Fallback: try splitting by underscore
                numeric_id = building_id.split('_')[-1] if '_' in building_id else str(building_id)
            numeric_id = str(numeric_id)  # Ensure it's a string
            
            print(f"Extracted numeric ID: {numeric_id} from building_id: {building_id}")
            print(f"Available building IDs in cache (first 5): {list(building_features.keys())[:5] if isinstance(building_features, dict) else 'N/A'}")
            print(f"Total buildings in cache: {len(building_features) if isinstance(building_features, dict) else 0}")
            
            if not isinstance(building_features, dict):
                print("Building features cache is not a dictionary, skipping cache lookup")
            else:
                # Try exact match
                if numeric_id in building_features:
                    features = building_features[numeric_id]
                    print(f"Loaded features from cache for building {building_id} (matched as {numeric_id}): {len(features)} features")
                    return jsonify({'building_id': building_id, 'features': features})
                
                # Try to find by string comparison and regex (handle any type mismatches)
                # Also try with/without leading zeros
                numeric_id_variants = [
                    numeric_id,  # Original
                    numeric_id.lstrip('0'),  # Without leading zeros
                    numeric_id.zfill(16),  # Padded to 16 digits
                ]
                
                for variant in numeric_id_variants:
                    # Try exact match with variant
                    if variant in building_features:
                        features = building_features[variant]
                        print(f"Loaded features from cache for building {building_id} (matched variant {variant}): {len(features)} features")
                        return jsonify({'building_id': building_id, 'features': features})
                    
                    # Try to find by string comparison and regex
                    for cached_id, cached_features in building_features.items():
                        cached_id_str = str(cached_id)
                        # Try exact match
                        if cached_id_str == variant:
                            print(f"Loaded features from cache for building {building_id} (matched {cached_id} as variant {variant}): {len(cached_features)} features")
                            return jsonify({'building_id': building_id, 'features': cached_features})
                        # Try regex match - check if variant is contained in cached_id or vice versa
                        if re.search(variant, cached_id_str) or re.search(cached_id_str, variant):
                            print(f"Loaded features from cache for building {building_id} (regex matched {cached_id} with variant {variant}): {len(cached_features)} features")
                            return jsonify({'building_id': building_id, 'features': cached_features})
                        # Try partial match - check if the variant ends with cached_id or vice versa
                        if variant.endswith(cached_id_str) or cached_id_str.endswith(variant):
                            print(f"Loaded features from cache for building {building_id} (partial match {cached_id} with variant {variant}): {len(cached_features)} features")
                            return jsonify({'building_id': building_id, 'features': cached_features})
                
                # Final check: search through all building IDs to see if any contain the numeric_id
                print(f"Searching through all {len(building_features)} building IDs in cache for {numeric_id}...")
                for cached_id, cached_features in building_features.items():
                    cached_id_str = str(cached_id)
                    # Check if numeric_id appears anywhere in cached_id
                    if numeric_id in cached_id_str or cached_id_str in numeric_id:
                        print(f"Found partial match in cache: {cached_id} contains {numeric_id} or vice versa")
                        print(f"Loaded features from cache for building {building_id} (found {cached_id}): {len(cached_features)} features")
                        return jsonify({'building_id': building_id, 'features': cached_features})
        
        # Try to load from parquet file if not in cache
        if FEATURES_PARQUET.exists():
            building_features = build_features_from_parquet(FEATURES_PARQUET)
        else:
            # Fall back to joblib
            joblib_path = DATA_DIR / 'property_dicts' / 'Hague_demo_130425_demo_inference_vector_normalization=True_seed=1.joblib'
            
            if not joblib_path.exists():
                return jsonify({'error': f'Joblib file not found at {joblib_path}', 'features': {}}), 404

            import joblib
            import numpy as np
            with open(joblib_path, 'rb') as f:
                property_dicts = joblib.load(f)
            
            # Extract all unique building IDs
            building_ids = set()
            if isinstance(property_dicts, dict) and len(property_dicts) > 0:
                first_feature = list(property_dicts.values())[0]
                if isinstance(first_feature, dict) and 'cands' in first_feature:
                    # Convert all building IDs to strings (handle numpy string types)
                    building_ids = set(str(bid) for bid in first_feature['cands'].keys())
            
            # Reorganize data: convert from feature->building to building->features
            building_features = {}
            for bid in building_ids:
                bid_str = str(bid)  # Ensure it's a string
                building_features[bid_str] = {}
                for feature_name, feature_data in property_dicts.items():
                    if isinstance(feature_data, dict) and 'cands' in feature_data:
                        cands_dict = feature_data['cands']
                        # Try both string and original key format
                        key_to_use = None
                        if bid_str in cands_dict:
                            key_to_use = bid_str
                        else:
                            # Try to find matching key (handle numpy string types)
                            for key in cands_dict.keys():
                                if str(key) == bid_str:
                                    key_to_use = key
                                    break
                        
                        if key_to_use is not None:
                            value = cands_dict[key_to_use]
                            # Convert numpy types to Python native types
                            if isinstance(value, (np.integer, np.floating)):
                                value = float(value)
                            elif isinstance(value, np.ndarray):
                                value = value.tolist()
                            building_features[bid_str][feature_name] = value
            
        # Store in cache
        features_cache[file_path] = building_features
        cache_set_json(f'features:{file_path}', building_features)
        
        # Try exact match first
        if building_id in building_features:
            features = building_features[building_id]
            print(f"Loaded features from joblib for building {building_id}: {len(features)} features")
            return jsonify({'building_id': building_id, 'features': features})
        
        # Try to match by extracting numeric ID using regex (handle prefixes like "bag_", "NL.IMBAG.Pand.", etc.)
        # Extract numeric part from building_id using regex (e.g., "bag_0518100000271783" -> "0518100000271783")
        # Pattern: find a sequence of digits (10 or more digits for building IDs)
        numeric_match = re.search(r'(\d{10,})', str(building_id))
        if numeric_match:
            numeric_id = numeric_match.group(1)
        else:
            # Fallback: try splitting by underscore
            numeric_id = building_id.split('_')[-1] if '_' in building_id else str(building_id)
        numeric_id = str(numeric_id)  # Ensure it's a string
        
        print(f"Extracted numeric ID: {numeric_id} from building_id: {building_id}")
        print(f"Available building IDs in joblib (first 5): {list(building_features.keys())[:5]}")
        print(f"Total buildings in joblib: {len(building_features)}")
        
        # Try exact match
        if numeric_id in building_features:
            features = building_features[numeric_id]
            print(f"Loaded features from joblib for building {building_id} (matched as {numeric_id}): {len(features)} features")
            return jsonify({'building_id': building_id, 'features': features})
        
        # Try to find by string comparison and regex (handle any type mismatches)
        # Also try with/without leading zeros
        numeric_id_variants = [
            numeric_id,  # Original
            numeric_id.lstrip('0'),  # Without leading zeros
            numeric_id.zfill(16),  # Padded to 16 digits
        ]
        
        for variant in numeric_id_variants:
            # Try exact match with variant
            if variant in building_features:
                features = building_features[variant]
                print(f"Loaded features from joblib for building {building_id} (matched variant {variant}): {len(features)} features")
                return jsonify({'building_id': building_id, 'features': features})
            
            # Try to find by string comparison and regex
            for cached_id, cached_features in building_features.items():
                cached_id_str = str(cached_id)
                # Try exact match
                if cached_id_str == variant:
                    print(f"Loaded features from joblib for building {building_id} (matched {cached_id} as variant {variant}): {len(cached_features)} features")
                    return jsonify({'building_id': building_id, 'features': cached_features})
                # Try regex match - check if variant is contained in cached_id or vice versa
                if re.search(variant, cached_id_str) or re.search(cached_id_str, variant):
                    print(f"Loaded features from joblib for building {building_id} (regex matched {cached_id} with variant {variant}): {len(cached_features)} features")
                    return jsonify({'building_id': building_id, 'features': cached_features})
                # Try partial match - check if the variant ends with cached_id or vice versa
                if variant.endswith(cached_id_str) or cached_id_str.endswith(variant):
                    print(f"Loaded features from joblib for building {building_id} (partial match {cached_id} with variant {variant}): {len(cached_features)} features")
                    return jsonify({'building_id': building_id, 'features': cached_features})
        
        # Final check: search through all building IDs to see if any contain the numeric_id
        print(f"Searching through all {len(building_features)} building IDs for {numeric_id}...")
        for cached_id, cached_features in building_features.items():
            cached_id_str = str(cached_id)
            # Check if numeric_id appears anywhere in cached_id
            if numeric_id in cached_id_str or cached_id_str in numeric_id:
                print(f"Found partial match: {cached_id} contains {numeric_id} or vice versa")
                print(f"Loaded features from joblib for building {building_id} (found {cached_id}): {len(cached_features)} features")
                return jsonify({'building_id': building_id, 'features': cached_features})
        
        # Building not found in joblib - return empty features with a message
        print(f"WARNING: Building {building_id} (numeric: {numeric_id if 'numeric_id' in locals() else building_id}) not found in joblib file")
        print("This building may not have features calculated, or it's not in the dataset used for feature calculation")
        # Return empty features instead of mock data
        return jsonify({
            'building_id': building_id,
            'features': {},
            'message': f'Building {building_id} not found in feature dataset. This building may not have geometric features calculated.',
            'found': False
        })
            
    except Exception as e:
        import traceback
        print(f"Error getting features: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e), 'features': {}}), 500


@app.route('/api/bkafi/load', methods=['POST'])
def load_bkafi_results():
    """
    Load BKAFI prediction results from JSON file
    Path: results_demo/demo_inference/demo_detailed_results_XGBClassifier_seed1.json
    """
    try:
        global bkafi_cache

        cached_bkafi = get_bkafi_cache()
        cached_by_file = get_bkafi_by_file_cache()
        if cached_bkafi is not None and cached_by_file is not None:
            bkafi_cache = cached_bkafi
            app.bkafi_cache_by_file = cached_by_file
            return jsonify({
                'success': True,
                'message': 'BKAFI results already cached',
                'total_pairs': sum(len(v.get('possible_matches', [])) for v in cached_bkafi.values()),
                'unique_candidates': len(cached_bkafi)
            })

        if get_redis_client():
            job = load_bkafi_task.delay()
            return jsonify({
                'job_id': job.id,
                'status': 'queued',
                'message': 'BKAFI load queued'
            }), 202
        
        # Load from JSON file
        if not DEMO_RESULTS_JSON.exists():
            return jsonify({'error': f'BKAFI results file not found at {DEMO_RESULTS_JSON}'}), 404
        
        with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
            results_dict = json.load(f)
        
        print(f"Loaded BKAFI results from: {DEMO_RESULTS_JSON}")
        
        # The new structure is file-based: {filename: {building_id: {possible_matches: [...]}}}
        # Flatten it for easier lookup: merge all buildings from all files
        flattened_cache = {}
        total_pairs = 0
        unique_candidates = 0
        
        for file_name, file_buildings in results_dict.items():
            for building_id, building_data in file_buildings.items():
                flattened_cache[building_id] = building_data
                unique_candidates += 1
                total_pairs += len(building_data.get('possible_matches', []))
        
        print(f"Number of candidate buildings: {unique_candidates} across {len(results_dict)} files")
        
        # Store in global cache (flattened dictionary structure for backward compatibility)
        bkafi_cache = flattened_cache
        # Also store the original file-based structure for file-specific lookups
        if not hasattr(app, 'bkafi_cache_by_file'):
            app.bkafi_cache_by_file = results_dict

        cache_set_json('bkafi:flat', flattened_cache)
        cache_set_json('bkafi:by_file', results_dict)
        
        return jsonify({
            'success': True,
            'message': f'BKAFI results loaded: {total_pairs} pairs for {unique_candidates} candidate buildings',
            'total_pairs': int(total_pairs),
            'unique_candidates': int(unique_candidates)
        })
            
    except json.JSONDecodeError as e:
        import traceback
        print(f"Error parsing JSON: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 500
    except Exception as e:
        import traceback
        print(f"Error loading BKAFI results: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


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


@app.route('/api/features/result', methods=['GET'])
def get_features_result():
    file_path = request.args.get('file_path', '')
    if not file_path:
        return jsonify({'error': 'No file path provided'}), 400
    cached = cache_get_json(f'features:{file_path}')
    if cached is None:
        return jsonify({'error': 'Features not found in cache'}), 404
    return jsonify({'file_path': file_path, 'features': cached})


@app.route('/api/bkafi/result', methods=['GET'])
def get_bkafi_result():
    cached = cache_get_json('bkafi:flat')
    if cached is None:
        return jsonify({'error': 'BKAFI results not found in cache'}), 404
    return jsonify({'bkafi': cached})


@app.route('/api/building/single/<building_id>')
def get_single_building(building_id):
    """
    Get a single building from a file as a minimal CityJSON
    Query params: file (the file path containing the building)
    """
    try:
        import re
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        print(f"Extracting single building {building_id} from file {file_path}")

        cache_key = f"building:{file_path}:{building_id}"
        cached_building = cache_get_json(cache_key)
        if cached_building is not None:
            return jsonify(cached_building)
        
        # Find the file
        file_name = Path(file_path).name
        possible_paths = [
            DATA_DIR / file_path,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source A' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source B' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceA' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceB' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / file_path,
        ]
        
        if 'RawCitiesData' in file_path or 'The Hague' in file_path:
            possible_paths.insert(0, DATA_DIR / file_path)
        
        found_path = None
        for path in possible_paths:
            if path and path.exists() and path.is_file():
                found_path = path
                break
        
        if not found_path:
            return jsonify({'error': f'File not found: {file_path}'}), 404
        
        # Load the CityJSON file
        with open(found_path, 'r', encoding='utf-8') as f:
            city_json = json.load(f)
        
        # Extract numeric ID for matching
        numeric_match = re.search(r'(\d{10,})', str(building_id))
        numeric_id = numeric_match.group(1) if numeric_match else building_id.split('_')[-1] if '_' in building_id else str(building_id)
        numeric_id = str(numeric_id)
        
        # Find the building in CityObjects
        target_building_id = None
        target_building = None
        
        for obj_id, obj_data in city_json.get('CityObjects', {}).items():
            # Try exact match
            if obj_id == building_id or obj_id == numeric_id:
                target_building_id = obj_id
                target_building = obj_data
                break
            
            # Try numeric match
            obj_numeric_match = re.search(r'(\d{10,})', str(obj_id))
            if obj_numeric_match:
                obj_numeric = obj_numeric_match.group(1)
                if obj_numeric == numeric_id:
                    target_building_id = obj_id
                    target_building = obj_data
                    break
        
        if not target_building:
            return jsonify({'error': f'Building {building_id} not found in file {file_path}'}), 404
        
        # Extract all vertex indices used by this building
        vertex_indices = set()
        
        def collect_vertex_indices(geometry):
            if geometry.get('type') == 'Solid' and geometry.get('boundaries'):
                for shell in geometry['boundaries']:
                    for face in shell:
                        for ring in face:
                            for vertex_idx in ring:
                                if isinstance(vertex_idx, int) and vertex_idx >= 0:
                                    vertex_indices.add(vertex_idx)
            elif geometry.get('type') == 'MultiSurface' and geometry.get('boundaries'):
                for surface in geometry['boundaries']:
                    for ring in surface:
                        for vertex_idx in ring:
                            if isinstance(vertex_idx, int) and vertex_idx >= 0:
                                vertex_indices.add(vertex_idx)
        
        geometries = target_building.get('geometry', [])
        for geometry in geometries:
            collect_vertex_indices(geometry)
        
        # Create a mapping from old indices to new indices
        sorted_indices = sorted(vertex_indices)
        index_mapping = {old_idx: new_idx for new_idx, old_idx in enumerate(sorted_indices)}
        
        # Extract only the vertices we need
        all_vertices = city_json.get('vertices', [])
        new_vertices = [all_vertices[i] for i in sorted_indices if i < len(all_vertices)]
        
        # Update geometry to use new vertex indices
        def remap_geometry(geometry):
            new_geometry = geometry.copy()
            if new_geometry.get('type') == 'Solid' and new_geometry.get('boundaries'):
                new_geometry['boundaries'] = [
                    [
                        [
                            [index_mapping.get(v_idx, v_idx) for v_idx in ring]
                            for ring in face
                        ]
                        for face in shell
                    ]
                    for shell in new_geometry['boundaries']
                ]
            elif new_geometry.get('type') == 'MultiSurface' and new_geometry.get('boundaries'):
                new_geometry['boundaries'] = [
                    [
                        [index_mapping.get(v_idx, v_idx) for v_idx in ring]
                        for ring in surface
                    ]
                    for surface in new_geometry['boundaries']
                ]
            return new_geometry
        
        new_geometries = [remap_geometry(geom) for geom in geometries]
        
        # Create minimal CityJSON with only this building
        minimal_cityjson = {
            'type': 'CityJSON',
            'version': city_json.get('version', '1.0'),
            'CityObjects': {
                target_building_id: {
                    **target_building,
                    'geometry': new_geometries
                }
            },
            'vertices': new_vertices
        }
        
        # Preserve metadata if available
        if 'metadata' in city_json:
            minimal_cityjson['metadata'] = city_json['metadata']
        
        # Preserve transform if available
        if 'transform' in city_json:
            minimal_cityjson['transform'] = city_json['transform']
        
        print(f"Created minimal CityJSON with 1 building and {len(new_vertices)} vertices")
        cache_set_json(cache_key, minimal_cityjson)
        return jsonify(minimal_cityjson)
        
    except Exception as e:
        import traceback
        print(f"Error extracting single building: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/building/find-file/<building_id>')
def find_building_file(building_id):
    """
    Find which file contains a specific building ID
    Searches through Source A and Source B files
    """
    try:
        import re
        # Extract numeric ID from building_id
        numeric_match = re.search(r'(\d{10,})', str(building_id))
        if numeric_match:
            numeric_id = numeric_match.group(1)
        else:
            numeric_id = building_id.split('_')[-1] if '_' in building_id else str(building_id)
        numeric_id = str(numeric_id)
        
        print(f"Searching for building {building_id} (numeric: {numeric_id}) in files...")
        
        # Get source paths (same logic as get_files)
        source_a_paths = [
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source A',
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceA',
            DATA_DIR / 'Source A',
            DATA_DIR / 'SourceA',
            DATA_DIR
        ]
        
        source_b_paths = [
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source B',
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceB',
            DATA_DIR / 'Source B',
            DATA_DIR / 'SourceB',
            DATA_DIR
        ]
        
        # Find first existing path
        source_a_path = None
        for path in source_a_paths:
            if path.exists():
                source_a_path = path
                break
        
        source_b_path = None
        for path in source_b_paths:
            if path.exists():
                source_b_path = path
                break
        
        # Search through files
        def search_in_directory(directory, source_type):
            if not directory or not directory.exists():
                return None
            
            for file_path in directory.rglob('*.json'):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        city_objects = data.get('CityObjects', {})
                        
                        # Check if building ID exists in this file
                        for obj_id, obj_data in city_objects.items():
                            # Try exact match
                            if obj_id == building_id or obj_id == numeric_id:
                                rel_path = file_path.relative_to(DATA_DIR)
                                print(f"Found building {building_id} in {rel_path} (exact match)")
                                return str(rel_path)
                            
                            # Try numeric match
                            obj_numeric_match = re.search(r'(\d{10,})', str(obj_id))
                            if obj_numeric_match:
                                obj_numeric = obj_numeric_match.group(1)
                                if obj_numeric == numeric_id:
                                    rel_path = file_path.relative_to(DATA_DIR)
                                    print(f"Found building {building_id} in {rel_path} (numeric match)")
                                    return str(rel_path)
                except Exception as e:
                    print(f"Error reading file {file_path}: {e}")
                    continue
            
            return None
        
        # Search in Source A first
        file_path = search_in_directory(source_a_path, 'A')
        if file_path:
            return jsonify({
                'building_id': building_id,
                'file_path': file_path,
                'source': 'A'
            })
        
        # Search in Source B
        file_path = search_in_directory(source_b_path, 'B')
        if file_path:
            return jsonify({
                'building_id': building_id,
                'file_path': file_path,
                'source': 'B'
            })
        
        # Not found
        return jsonify({
            'building_id': building_id,
            'file_path': None,
            'source': None,
            'message': f'Building {building_id} not found in any file'
        }), 404
        
    except Exception as e:
        import traceback
        print(f"Error finding building file: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/building/bkafi/<building_id>')
def get_building_bkafi(building_id):
    """
    Get BKAFI pairs for a specific candidate building
    Each building gets up to 3 pairs (candidate building ID -> index building IDs)
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        print(f"Getting BKAFI pairs for building {building_id} from file {file_path}")
        
        bkafi_cache_local = get_bkafi_cache()
        if bkafi_cache_local is None:
            # Try to load synchronously if not cached
            if DEMO_RESULTS_JSON.exists():
                with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
                    results_dict = json.load(f)
                flattened_cache = {}
                for file_name, file_buildings in results_dict.items():
                    for building_id, building_data in file_buildings.items():
                        flattened_cache[building_id] = building_data
                bkafi_cache_local = flattened_cache
                cache_set_json('bkafi:flat', flattened_cache)
                cache_set_json('bkafi:by_file', results_dict)
                print(f"Loaded BKAFI results from: {DEMO_RESULTS_JSON}")
            else:
                return jsonify({
                    'error': 'BKAFI results not loaded. Please run Step 2 first.',
                    'pairs': []
                }), 404
        
        # Extract numeric ID from building_id (handle prefixes like "bag_")
        import re
        numeric_match = re.search(r'(\d{10,})', str(building_id))
        if numeric_match:
            numeric_id = numeric_match.group(1)
        else:
            numeric_id = building_id.split('_')[-1] if '_' in building_id else str(building_id)
        numeric_id = str(numeric_id)
        
        print(f"Looking for pairs for candidate building: {numeric_id}")
        
        # Lookup candidate building in dictionary (try exact match first)
        building_data = bkafi_cache_local.get(numeric_id)
        
        # If no exact match, try to find by string comparison
        if building_data is None:
            for candidate_id in bkafi_cache_local.keys():
                if str(candidate_id) == numeric_id:
                    building_data = bkafi_cache_local[candidate_id]
                    break
                # Also try if numeric_id is contained in candidate_id or vice versa
                if numeric_id in str(candidate_id) or str(candidate_id) in numeric_id:
                    building_data = bkafi_cache_local[candidate_id]
                    break
        
        if building_data is None:
            print(f"No pairs found for building {building_id} (numeric: {numeric_id})")
            return jsonify({
                'building_id': building_id,
                'pairs': [],
                'message': f'No BKAFI pairs found for building {building_id}'
            })
        
        # Extract possible_matches array
        possible_matches = building_data.get('possible_matches', [])
        print(f"Found {len(possible_matches)} pairs for building {building_id} (numeric: {numeric_id})")
        
        if len(possible_matches) == 0:
            return jsonify({
                'building_id': building_id,
                'pairs': [],
                'message': f'No BKAFI pairs found for building {building_id}'
            })
        
        # Convert to list of dictionaries
        pairs = []
        for match in possible_matches:
            pair = {
                'candidate_id': numeric_id,
                'index_id': str(match.get('index_id', '')),
                'prediction': int(match.get('predicted_label', 0)) if match.get('predicted_label') is not None else (1 if match.get('confidence', 0) > CONFIDENCE_THRESHOLD else 0),
                'true_label': int(match.get('true_label', 0)) if match.get('true_label') is not None else None,
                'confidence': float(match.get('confidence', 0))
            }
            pairs.append(pair)
        
        # Sort by confidence (descending) instead of prediction
        pairs.sort(key=lambda x: x.get('confidence', 0), reverse=True)
        
        return jsonify({
            'building_id': building_id,
            'pairs': pairs,
            'total_pairs': len(pairs)
        })
            
    except Exception as e:
        import traceback
        print(f"Error getting BKAFI pairs: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e), 'pairs': []}), 500


@app.route('/api/building/matches/<building_id>')
def get_building_matches(building_id):
    """
    Get matches for a specific building from prediction results
    Uses the same data as get_building_bkafi() - returns matches with predicted_label=1
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        print(f"Getting matches for building {building_id} from file {file_path}")
        
        bkafi_cache_local = get_bkafi_cache()
        if bkafi_cache_local is None:
            # Try to load if not cached
            if DEMO_RESULTS_JSON.exists():
                with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
                    results_dict = json.load(f)
                # Flatten the file-based structure
                flattened_cache = {}
                for file_name, file_buildings in results_dict.items():
                    for building_id, building_data in file_buildings.items():
                        flattened_cache[building_id] = building_data
                bkafi_cache_local = flattened_cache
                cache_set_json('bkafi:flat', flattened_cache)
                cache_set_json('bkafi:by_file', results_dict)
                print(f"Loaded BKAFI results from: {DEMO_RESULTS_JSON}")
            else:
                return jsonify({
                    'error': 'BKAFI results not loaded. Please run Step 2 first.',
                    'matches': []
                }), 404
        
        # Extract numeric ID from building_id
        import re
        numeric_match = re.search(r'(\d{10,})', str(building_id))
        if numeric_match:
            numeric_id = numeric_match.group(1)
        else:
            numeric_id = building_id.split('_')[-1] if '_' in building_id else str(building_id)
        numeric_id = str(numeric_id)
        
        # Lookup candidate building in dictionary
        building_data = bkafi_cache_local.get(numeric_id)
        
        # If no exact match, try to find by string comparison
        if building_data is None:
            for candidate_id in bkafi_cache_local.keys():
                if str(candidate_id) == numeric_id:
                    building_data = bkafi_cache_local[candidate_id]
                    break
                if numeric_id in str(candidate_id) or str(candidate_id) in numeric_id:
                    building_data = bkafi_cache_local[candidate_id]
                    break
        
        if building_data is None:
            return jsonify({
                'building_id': building_id,
                'matches': [],
                'message': f'No matches found for building {building_id}'
            })
        
        # Extract possible_matches and filter for predicted matches (predicted_label=1 or confidence > threshold)
        possible_matches = building_data.get('possible_matches', [])
        matches = []
        
        for match in possible_matches:
            # Get predicted_label or calculate from confidence
            predicted_label = match.get('predicted_label')
            if predicted_label is None:
                predicted_label = 1 if match.get('confidence', 0) > CONFIDENCE_THRESHOLD else 0
            else:
                predicted_label = int(predicted_label)
            
            # Only include matches with predicted_label=1
            if predicted_label == 1:
                match_data = {
                    'id': match.get('index_id', ''),
                    'building_id': str(match.get('index_id', '')),
                    'source': 'Source B',  # Index buildings are from Source B
                    'confidence': float(match.get('confidence', 0)),
                    'true_label': int(match.get('true_label', 0)) if match.get('true_label') is not None else None
                }
                matches.append(match_data)
        
        # Sort by confidence (descending)
        matches.sort(key=lambda x: x.get('confidence', 0), reverse=True)
        
        return jsonify({
            'building_id': building_id,
            'matches': matches
        })
            
    except Exception as e:
        import traceback
        print(f"Error getting matches: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e), 'matches': []}), 500


@app.route('/api/buildings/status', methods=['GET'])
def get_all_buildings_status():
    """
    Get status for all buildings in the selected file
    Returns: building_id -> {has_features, has_pairs, match_status}
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        print(f"Getting status for all buildings in file: {file_path}")
        
        result = {}
        
        # 1. Check which buildings have features
        has_features = set()
        features_data = get_features_cache(file_path)
        if isinstance(features_data, dict):
            has_features = set(features_data.keys())
        
        # 2. Check which buildings have BKAFI pairs
        has_pairs = set()
        bkafi_data = get_bkafi_cache()
        if bkafi_data is not None:
            # Get all unique candidate building IDs from dictionary keys
            for candidate_id in bkafi_data.keys():
                bid_str = str(candidate_id)
                has_pairs.add(bid_str)
                # Also add numeric version for matching
                numeric_match = re.search(r'(\d{10,})', bid_str)
                if numeric_match:
                    has_pairs.add(numeric_match.group(1))
        
        # 3. Check match status (true match, false positive, no match)
        # For each building, check all its pairs to determine overall status
        match_status = {}  # building_id -> 'true_match', 'false_positive', 'no_match'
        if bkafi_data is not None:
            # Iterate over dictionary keys (candidate building IDs)
            for candidate_id, building_data in bkafi_data.items():
                source_id_str = str(candidate_id)
                
                # Get possible_matches array
                possible_matches = building_data.get('possible_matches', [])
                building_has_pairs = len(possible_matches) > 0
                
                # Check all pairs for this building
                has_true_match = False
                has_false_positive = False
                
                for match in possible_matches:
                    # Get predicted_label or calculate from confidence
                    predicted_label = match.get('predicted_label')
                    if predicted_label is None:
                        predicted_label = 1 if match.get('confidence', 0) > CONFIDENCE_THRESHOLD else 0
                    else:
                        predicted_label = int(predicted_label)
                    
                    # Get true_label (do not use is_match as it's redundant)
                    true_label = match.get('true_label')
                    if true_label is not None:
                        true_label = int(true_label)
                    
                    if predicted_label == 1:
                        if true_label == 1:
                            has_true_match = True
                        elif true_label == 0:
                            has_false_positive = True
                
                # Determine overall status for this building based on ALL pairs
                # Priority: true_match > false_positive > no_match
                if has_true_match:
                    status = 'true_match'  # At least one pair with predicted_label=1 and true_label=1
                elif has_false_positive:
                    status = 'false_positive'  # At least one pair with predicted_label=1 and true_label=0
                elif building_has_pairs:
                    # Has pairs but all predictions are 0, or prediction=1 with unknown true_label
                    status = 'no_match'
                else:
                    status = None  # No pairs at all - keep previous stage color
                
                # Store for both full ID and numeric ID
                numeric_match = re.search(r'(\d{10,})', source_id_str)
                numeric_id = numeric_match.group(1) if numeric_match else None
                
                if status:
                    match_status[source_id_str] = status
                    if numeric_id:
                        match_status[numeric_id] = status
        
        # Combine all building IDs
        all_building_ids = has_features.union(has_pairs).union(match_status.keys())
        
        # Build result
        for building_id in all_building_ids:
            building_id_str = str(building_id)
            result[building_id_str] = {
                'has_features': building_id_str in has_features,
                'has_pairs': building_id_str in has_pairs,
                'match_status': match_status.get(building_id_str, None)
            }
        
        return jsonify({
            'success': True,
            'buildings': result,
            'total': len(result)
        })
        
    except Exception as e:
        import traceback
        print(f"Error getting building status: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/classifier/summary', methods=['GET'])
def get_classifier_summary():
    """
    Get classifier results summary with success rates calculated per file
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        print(f"Getting classifier summary for file: {file_path}")
        
        # Load metrics summary JSON
        if not DEMO_METRICS_JSON.exists():
            return jsonify({'error': f'Metrics summary file not found at {DEMO_METRICS_JSON}'}), 404
        
        with open(DEMO_METRICS_JSON, 'r', encoding='utf-8') as f:
            metrics_data = json.load(f)
        
        # Extract model metrics (XGBClassifier)
        model_name = 'XGBClassifier'
        if model_name not in metrics_data:
            return jsonify({'error': f'Model {model_name} not found in metrics file'}), 404
        
        model_metrics = metrics_data[model_name]
        file_metrics = model_metrics.get('file_metrics', {})
        
        # Get file name to match against file_metrics keys
        file_name = Path(file_path).name
        
        # Find matching file in file_metrics (try exact match first, then partial)
        file_metric_data = None
        for key in file_metrics.keys():
            if key == file_name or file_name in key or key in file_name:
                file_metric_data = file_metrics[key]
                print(f"Found metrics for file: {key}")
                break
        
        if not file_metric_data:
            return jsonify({'error': f'No metrics found for file: {file_name}'}), 404
        
        # Use metrics from JSON file
        potential_matches_in_index = file_metric_data.get('potential_matches_in_index', 0)
        potential_matches_in_blocking = file_metric_data.get('potential_matches_in_blocking', 0)
        potential_true_matches = potential_matches_in_blocking  # In BKAFI sets
        potential_true_matches_not_in_bkafi = potential_matches_in_index - potential_matches_in_blocking  # NOT in BKAFI sets
        
        # Threshold metrics (confidence > 0.5)
        threshold_precision = file_metric_data.get('threshold_precision', 0.0)
        threshold_recall_overall = file_metric_data.get('threshold_recall_overall', 0.0)
        threshold_recall_blocking = file_metric_data.get('threshold_recall_blocking', 0.0)
        threshold_recall_matching = file_metric_data.get('threshold_recall_matching', 0.0)
        threshold_f1_score = file_metric_data.get('threshold_f1_score', 0.0)
        threshold_true_positives = file_metric_data.get('threshold_true_positives', 0)
        threshold_false_positives = file_metric_data.get('threshold_false_positives', 0)
        threshold_false_negatives = file_metric_data.get('threshold_total_false_negatives', 0)
        threshold_false_negatives_in_blocking = file_metric_data.get('threshold_false_negatives_in_blocking', 0)
        threshold_false_negatives_not_in_blocking = file_metric_data.get('threshold_false_negatives_not_in_blocking', 0)
        
        # Best match metrics (highest confidence)
        best_match_precision = file_metric_data.get('best_match_precision', 0.0)
        best_match_recall_overall = file_metric_data.get('best_match_recall_overall', 0.0)
        best_match_recall_blocking = file_metric_data.get('best_match_recall_blocking', 0.0)
        best_match_recall_matching = file_metric_data.get('best_match_recall_matching', 0.0)
        best_match_f1_score = file_metric_data.get('best_match_f1_score', 0.0)
        best_match_true_positives = file_metric_data.get('best_match_true_positives', 0)
        best_match_false_positives = file_metric_data.get('best_match_false_positives', 0)
        best_match_false_negatives = file_metric_data.get('best_match_total_false_negatives', 0)
        best_match_false_negatives_in_blocking = file_metric_data.get('best_match_false_negatives_in_blocking', 0)
        best_match_false_negatives_not_in_blocking = file_metric_data.get('best_match_false_negatives_not_in_blocking', 0)
        
        # Calculate found true matches (true positives for threshold)
        found_true_matches = threshold_true_positives
        
        # Calculate total pairs from detailed results (need to load BKAFI cache for this)
        bkafi_by_file = get_bkafi_by_file_cache()
        if bkafi_by_file is None and DEMO_RESULTS_JSON.exists():
            with open(DEMO_RESULTS_JSON, 'r', encoding='utf-8') as f:
                results_dict = json.load(f)
            bkafi_by_file = results_dict
            cache_set_json('bkafi:by_file', results_dict)
        
        # Count total pairs for this file
        total_pairs = 0
        if bkafi_by_file and file_name in bkafi_by_file:
            for building_data in bkafi_by_file[file_name].values():
                total_pairs += len(building_data.get('possible_matches', []))
        
        # Get total buildings in file
        candidates_in_file = file_metric_data.get('candidates_in_file', 0)
        
        # Recall metrics (from threshold metrics)
        recall = threshold_recall_matching  # Matching recall for backward compatibility
        overall_recall = threshold_recall_overall
        blocking_recall = threshold_recall_blocking
        matching_recall = threshold_recall_matching
        
        # Precision metrics
        precision = threshold_precision
        precision_conf_threshold = threshold_precision
        precision_highest_conf = best_match_precision
        
        # Predicted counts (approximate from true_positives + false_positives)
        predicted_with_conf_threshold = threshold_true_positives + threshold_false_positives
        predicted_highest_conf = best_match_true_positives + best_match_false_positives
        
        # True matches not in blocking
        true_matches_not_in_blocking = threshold_false_negatives_not_in_blocking
        
        summary = {
            'total_buildings': candidates_in_file,  # Buildings in file that are in BKAFI results
            'total_buildings_in_file': candidates_in_file,  # Total candidates in file
            'potential_true_matches': potential_true_matches,  # Potential true matches IN BKAFI sets
            'potential_true_matches_not_in_bkafi': potential_true_matches_not_in_bkafi,  # Potential true matches NOT in BKAFI sets
            'buildings_with_true_match_in_bkafi': potential_matches_in_blocking,  # Buildings with true match in BKAFI blocking
            'found_true_matches': found_true_matches,
            'recall': recall,
            'precision': precision,
            'precision_conf_threshold': precision_conf_threshold,
            'precision_highest_conf': precision_highest_conf,
            'predicted_with_conf_threshold': predicted_with_conf_threshold,
            'predicted_highest_conf': predicted_highest_conf,
            'true_positive': threshold_true_positives,
            'false_positive': threshold_false_positives,
            'false_negative': threshold_false_negatives,
            'false_negative_in_blocking': threshold_false_negatives_in_blocking,
            'false_negative_not_in_blocking': threshold_false_negatives_not_in_blocking,
            'best_match_false_negative_in_blocking': best_match_false_negatives_in_blocking,
            'best_match_false_negative_not_in_blocking': best_match_false_negatives_not_in_blocking,
            'best_match_total_false_negatives': best_match_false_negatives,
            'true_matches_not_in_blocking': true_matches_not_in_blocking,
            'total_pairs': total_pairs,
            'overall_recall': overall_recall,
            'blocking_recall': blocking_recall,
            'matching_recall': matching_recall,
            'f1_score': best_match_f1_score,  # Use best match F1 score
            'best_match_precision': best_match_precision,
            'best_match_recall_overall': best_match_recall_overall,
            'best_match_recall_blocking': best_match_recall_blocking,
            'best_match_recall_matching': best_match_recall_matching,
            'best_match_f1_score': best_match_f1_score,
            'best_match_true_positives': best_match_true_positives,
            'best_match_false_positives': best_match_false_positives
        }
        
        return jsonify({
            'success': True,
            'summary': summary
        })
        
    except json.JSONDecodeError as e:
        import traceback
        print(f"Error parsing JSON: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 500
    except Exception as e:
        import traceback
        print(f"Error getting classifier summary: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)