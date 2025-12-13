"""
3dSAGER Demo Flask Application
Provides web interface and API endpoints for 3D geospatial entity resolution
"""

import os
import json
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


@app.route('/api/building/matches/<building_id>')
def get_building_matches(building_id):
    """Get matches for a specific building"""
    try:
        # Mock data - replace with actual matching logic
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
        return jsonify({'error': str(e), 'matches': []}), 500


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)