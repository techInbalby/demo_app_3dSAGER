"""
3dSAGER Demo Flask Application
Provides web interface and API endpoints for 3D geospatial entity resolution
"""

import os
import json
import re
import pandas as pd
from flask import Flask, render_template, jsonify, request
from pathlib import Path

app = Flask(__name__)

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
        print(f"DEBUG: File size: {found_path.stat().st_size} bytes")
        
        with open(found_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"DEBUG: Successfully loaded JSON, {len(data)} top-level keys")
            return jsonify(data)
            
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


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)