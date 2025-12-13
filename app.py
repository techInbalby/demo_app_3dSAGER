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

app = Flask(__name__)
# Enable compression for all responses (gzip)
Compress(app)

# Configuration
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
RESULTS_DIR = BASE_DIR / 'results'
SAVED_MODEL_DIR = BASE_DIR / 'saved_model_files'
UPLOADS_DIR = BASE_DIR / 'uploads'
LOGS_DIR = BASE_DIR / 'logs'

# Ensure directories exist
for directory in [DATA_DIR, RESULTS_DIR, SAVED_MODEL_DIR, UPLOADS_DIR, LOGS_DIR]:
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
        
        with open(found_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"DEBUG: Successfully loaded JSON, {len(data)} top-level keys")
            
            # Create response with caching headers
            response = jsonify(data)
            response.headers['ETag'] = etag
            response.headers['Cache-Control'] = 'public, max-age=3600'  # Cache for 1 hour
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
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


# Global cache for loaded features
features_cache = {}
# Global cache for BKAFI results
bkafi_cache = None

@app.route('/api/features/calculate', methods=['POST'])
def calculate_all_features():
    """
    Calculate geometric features for all buildings in the selected file
    Loads from joblib file: data/property_dicts/Hague_130425_test_matching_large_neg_samples_num=2_vector_normalization=True_seed=1.joblib
    """
    try:
        data = request.get_json()
        file_path = data.get('file_path', '')
        
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        print(f"Calculating features for all buildings in file: {file_path}")
        
        # Load from joblib file
        joblib_path = DATA_DIR / 'property_dicts' / 'Hague_130425_test_matching_large_neg_samples_num=2_vector_normalization=True_seed=1.joblib'
        
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
        cache_key = file_path
        features_cache[cache_key] = building_features
        
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
        cache_key = file_path
        if cache_key in features_cache:
            building_features = features_cache[cache_key]
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
        
        # Try to load from joblib file if not in cache
        joblib_path = DATA_DIR / 'property_dicts' / 'Hague_130425_test_matching_large_neg_samples_num=2_vector_normalization=True_seed=1.joblib'
        
        if joblib_path.exists():
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
            features_cache[cache_key] = building_features
            
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
        print(f"This building may not have features calculated, or it's not in the dataset used for feature calculation")
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
    Load BKAFI prediction results from pkl file
    Path: results/prediction_results/Hague_matching_BaggingClassifier_seed=1.pkl
    """
    try:
        global bkafi_cache
        
        # Load from pkl file
        pkl_path = RESULTS_DIR / 'prediction_results' / 'Hague_matching_BaggingClassifier_seed=1.pkl'
        
        if not pkl_path.exists():
            return jsonify({'error': f'BKAFI results file not found at {pkl_path}'}), 404
        
        import pickle
        
        with open(pkl_path, 'rb') as f:
            df = pickle.load(f)
        
        print(f"Loaded BKAFI results from: {pkl_path}")
        print(f"DataFrame shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")
        
        # Store in global cache
        bkafi_cache = df
        
        # Return success with summary
        total_pairs = len(df)
        unique_candidates = df['Source_Building_ID'].nunique()
        
        return jsonify({
            'success': True,
            'message': f'BKAFI results loaded: {total_pairs} pairs for {unique_candidates} candidate buildings',
            'total_pairs': int(total_pairs),
            'unique_candidates': int(unique_candidates)
        })
            
    except Exception as e:
        import traceback
        print(f"Error loading BKAFI results: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


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
        global bkafi_cache
        file_path = request.args.get('file', '')
        print(f"Getting BKAFI pairs for building {building_id} from file {file_path}")
        
        if bkafi_cache is None:
            # Try to load if not cached
            pkl_path = RESULTS_DIR / 'prediction_results' / 'Hague_matching_BaggingClassifier_seed=1.pkl'
            if pkl_path.exists():
                import pickle
                with open(pkl_path, 'rb') as f:
                    bkafi_cache = pickle.load(f)
                print(f"Loaded BKAFI results from: {pkl_path}")
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
        
        # Filter DataFrame for this candidate building (Source_Building_ID)
        # Try exact match first
        candidate_pairs = bkafi_cache[bkafi_cache['Source_Building_ID'] == numeric_id]
        
        # If no exact match, try string comparison
        if len(candidate_pairs) == 0:
            candidate_pairs = bkafi_cache[
                bkafi_cache['Source_Building_ID'].astype(str) == numeric_id
            ]
        
        # If still no match, try regex search
        if len(candidate_pairs) == 0:
            candidate_pairs = bkafi_cache[
                bkafi_cache['Source_Building_ID'].astype(str).str.contains(numeric_id, regex=False, na=False)
            ]
        
        print(f"Found {len(candidate_pairs)} pairs for building {building_id} (numeric: {numeric_id})")
        
        if len(candidate_pairs) == 0:
            return jsonify({
                'building_id': building_id,
                'pairs': [],
                'message': f'No BKAFI pairs found for building {building_id}'
            })
        
        # Convert to list of dictionaries
        pairs = []
        for _, row in candidate_pairs.iterrows():
            pair = {
                'candidate_id': str(row['Source_Building_ID']),
                'index_id': str(row['Candidate_Building_ID']),
                'prediction': int(row['Is_Match_Prediction']) if pd.notna(row['Is_Match_Prediction']) else 0,
                'true_label': int(row['Is_Match_True_Label']) if pd.notna(row['Is_Match_True_Label']) else None
            }
            pairs.append(pair)
        
        # Sort by prediction (1 first, then 0) and limit to top pairs
        pairs.sort(key=lambda x: x['prediction'], reverse=True)
        
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
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        print(f"Getting matches for building {building_id} from file {file_path}")
        
        # TODO: Load from prediction results pkl
        # Expected path: data/results/{file_name}/prediction_results.pkl
        # or data/saved_model_files/{file_name}/matches.pkl
        
        # Try to load from results
        import pickle
        results_path = RESULTS_DIR / Path(file_path).stem / 'prediction_results.pkl'
        
        if results_path.exists():
            with open(results_path, 'rb') as f:
                results = pickle.load(f)
            print(f"Loaded matches from: {results_path}")
            # Extract matches for this building
            # This depends on your data structure
            matches = []  # Extract from results
            return jsonify({'building_id': building_id, 'matches': matches})
        else:
            # Fallback: return mock data
            print(f"Matches not found at {results_path}, using mock data")
            mock_matches = [
                {
                    'id': f'match_{building_id}_1',
                    'building_id': f'B_{building_id}',
                    'source': 'Source B',
                    'confidence': 0.85,
                    'similarity': 0.87,
                    'features': 'Geometric similarity: 0.87'
                }
            ]
            return jsonify({'building_id': building_id, 'matches': mock_matches})
            
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
        cache_key = file_path
        has_features = set()
        if cache_key in features_cache:
            has_features = set(features_cache[cache_key].keys())
        
        # 2. Check which buildings have BKAFI pairs
        has_pairs = set()
        if bkafi_cache is not None:
            # Get all unique Source_Building_ID values
            if 'Source_Building_ID' in bkafi_cache.columns:
                # Extract numeric IDs and store both numeric and full format
                for bid in bkafi_cache['Source_Building_ID'].unique():
                    bid_str = str(bid)
                    has_pairs.add(bid_str)
                    # Also add numeric version for matching
                    numeric_match = re.search(r'(\d{10,})', bid_str)
                    if numeric_match:
                        has_pairs.add(numeric_match.group(1))
        
        # 3. Check match status (true match, false positive, no match)
        # For each building, check all its pairs to determine overall status
        match_status = {}  # building_id -> 'true_match', 'false_positive', 'no_match'
        if bkafi_cache is not None:
            # Group by Source_Building_ID to check all pairs for each building
            for source_id, group in bkafi_cache.groupby('Source_Building_ID'):
                source_id_str = str(source_id)
                
                # Check all pairs for this building
                has_true_match = False
                has_false_positive = False
                building_has_pairs = len(group) > 0  # Use different variable name to avoid shadowing outer has_pairs
                
                for _, row in group.iterrows():
                    prediction = int(row['Is_Match_Prediction']) if pd.notna(row['Is_Match_Prediction']) else 0
                    true_label = int(row['Is_Match_True_Label']) if pd.notna(row['Is_Match_True_Label']) else None
                    
                    if prediction == 1:
                        if true_label == 1:
                            has_true_match = True
                        elif true_label == 0:
                            has_false_positive = True
                
                # Determine overall status for this building based on ALL pairs
                # Priority: true_match > false_positive > no_match
                # This uses data from results/prediction_results/Hague_matching_BaggingClassifier_seed=1.pkl
                if has_true_match:
                    status = 'true_match'  # At least one pair with prediction=1 and true_label=1
                elif has_false_positive:
                    status = 'false_positive'  # At least one pair with prediction=1 and true_label=0
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
    Get classifier results summary with success rates
    Query params: file (the selected file path)
    """
    try:
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        print(f"Getting classifier summary for file: {file_path}")
        
        # Initialize counters
        total_buildings = 0
        true_positive = 0
        false_positive = 0
        false_negative = 0
        no_pairs_but_exists = 0
        
        # Get all building IDs from the loaded file (if available)
        # For now, we'll use BKAFI cache to determine total buildings
        if bkafi_cache is not None and 'Source_Building_ID' in bkafi_cache.columns:
            unique_buildings = set(str(bid) for bid in bkafi_cache['Source_Building_ID'].unique())
            total_buildings = len(unique_buildings)
            
            # Count match types - count each pair (not each building)
            # True positive: prediction=1 and true_label=1
            # False positive: prediction=1 and true_label=0
            # False negative: prediction=0 and true_label=1
            for _, row in bkafi_cache.iterrows():
                prediction = int(row['Is_Match_Prediction']) if pd.notna(row['Is_Match_Prediction']) else 0
                true_label = int(row['Is_Match_True_Label']) if pd.notna(row['Is_Match_True_Label']) else None
                
                if prediction == 1 and true_label == 1:
                    true_positive += 1
                elif prediction == 1 and true_label == 0:
                    false_positive += 1
                elif prediction == 0 and true_label == 1:
                    false_negative += 1
        
        # Calculate success rate
        total_pairs = true_positive + false_positive + false_negative
        success_rate = true_positive / total_pairs if total_pairs > 0 else 0
        
        # Calculate percentage of buildings with no pairs but right one exists in index
        # NOTE: This is currently using mock data - replace with actual data when available
        buildings_with_pairs = set()
        if bkafi_cache is not None and 'Source_Building_ID' in bkafi_cache.columns:
            buildings_with_pairs = set(str(bid) for bid in bkafi_cache['Source_Building_ID'].unique())
        
        # Mock: assume 10% of buildings without pairs have the right match in index
        # TODO: Replace with actual data from provided file
        no_pairs_count = max(0, total_buildings - len(buildings_with_pairs))
        no_pairs_but_exists = int(no_pairs_count * 0.1)  # Mock: 10% of no-pair buildings
        no_pairs_percentage = no_pairs_but_exists / total_buildings if total_buildings > 0 else 0
        
        # Mark which fields are mock data
        is_mock = {
            'no_pairs_but_exists': True,  # This is mock data
            'no_pairs_percentage': True   # This is mock data
        }
        
        summary = {
            'total_buildings': total_buildings,
            'true_positive': true_positive,
            'false_positive': false_positive,
            'false_negative': false_negative,
            'no_pairs_but_exists': no_pairs_but_exists,
            'success_rate': success_rate,
            'no_pairs_percentage': no_pairs_percentage,
            'is_mock': is_mock  # Indicate which fields are mock data
        }
        
        return jsonify({
            'success': True,
            'summary': summary
        })
        
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