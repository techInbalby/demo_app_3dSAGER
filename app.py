from flask import Flask, render_template, request, jsonify, send_file
import os
import json
import time
import glob
from datetime import datetime
import uuid

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size



@app.route('/')
def home():
    return render_template('index.html')

@app.route('/demo')
def demo():
    return render_template('demo.html')

@app.route('/test')
def test():
    return send_file('test_file_selection.html')

@app.route('/debug')
def debug():
    return send_file('debug_demo.html')

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle file upload for 3D data processing"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Generate unique filename
    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{file.filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    return jsonify({
        'success': True,
        'file_id': file_id,
        'filename': filename,
        'message': 'File uploaded successfully'
    })

@app.route('/api/pipeline/run', methods=['POST'])
def run_pipeline():
    """Execute the 3dSAGER pipeline"""
    data = request.get_json()
    file_id = data.get('file_id')
    
    if not file_id:
        return jsonify({'error': 'File ID required'}), 400
    
    # Simulate pipeline execution
    # In a real implementation, this would call your actual 3dSAGER pipeline
    pipeline_stages = [
        {'id': 'preprocessing', 'name': 'Data Preprocessing', 'status': 'running'},
        {'id': 'analysis', 'name': 'Scene Analysis', 'status': 'pending'},
        {'id': 'generation', 'name': 'Content Generation', 'status': 'pending'},
        {'id': 'visualization', 'name': '3D Visualization', 'status': 'pending'}
    ]
    
    # Simulate processing time
    time.sleep(2)
    
    # Mock results
    results = {
        'objects_detected': 42,
        'processing_time': 1250,
        'confidence': 94.5,
        'pipeline_complete': True,
        'stages': [
            {'id': 'preprocessing', 'name': 'Data Preprocessing', 'status': 'completed'},
            {'id': 'analysis', 'name': 'Scene Analysis', 'status': 'completed'},
            {'id': 'generation', 'name': 'Content Generation', 'status': 'completed'},
            {'id': 'visualization', 'name': '3D Visualization', 'status': 'completed'}
        ]
    }
    
    return jsonify(results)

@app.route('/api/pipeline/status/<file_id>')
def pipeline_status(file_id):
    """Get pipeline execution status"""
    # Mock status - in real implementation, check actual pipeline status
    return jsonify({
        'file_id': file_id,
        'status': 'completed',
        'progress': 100,
        'stages': [
            {'id': 'preprocessing', 'status': 'completed'},
            {'id': 'analysis', 'status': 'completed'},
            {'id': 'generation', 'status': 'completed'},
            {'id': 'visualization', 'status': 'completed'}
        ]
    })

@app.route('/api/results/<file_id>')
def get_results(file_id):
    """Get pipeline results"""
    # Mock results - in real implementation, return actual results
    results = {
        'file_id': file_id,
        'timestamp': datetime.now().isoformat(),
        'objects_detected': 42,
        'processing_time': 1250,
        'confidence': 94.5,
        'scene_analysis': {
            'objects': [
                {'type': 'Building', 'count': 15, 'confidence': 0.95},
                {'type': 'Vehicle', 'count': 8, 'confidence': 0.87},
                {'type': 'Tree', 'count': 12, 'confidence': 0.92},
                {'type': 'Road', 'count': 7, 'confidence': 0.98}
            ]
        },
        'generation_results': {
            'synthetic_objects': 5,
            'enhanced_scene': True,
            'quality_score': 0.89
        }
    }
    
    return jsonify(results)

@app.route('/api/export/<file_id>')
def export_results(file_id):
    """Export pipeline results as JSON file"""
    results = get_results(file_id).get_json()
    
    # Create export file
    export_filename = f"3dsager_results_{file_id}.json"
    export_path = os.path.join(app.config['UPLOAD_FOLDER'], export_filename)
    
    with open(export_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    return send_file(export_path, as_attachment=True, download_name=export_filename)

@app.route('/api/data/files')
def get_data_files():
    """Get available data files from Source A and Source B"""
    data_dir = 'data/RawCitiesData/The Hague'
    
    source_a_files = []
    source_b_files = []
    
    # Get Source A files
    source_a_path = os.path.join(data_dir, 'Source A', '*.json')
    for file_path in glob.glob(source_a_path):
        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        source_a_files.append({
            'filename': filename,
            'path': file_path,
            'size': file_size,
            'source': 'A'
        })
    
    # Get Source B files (index set)
    source_b_path = os.path.join(data_dir, 'Source B', '*.json')
    for file_path in glob.glob(source_b_path):
        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        source_b_files.append({
            'filename': filename,
            'path': file_path,
            'size': file_size,
            'source': 'B'
        })
    
    return jsonify({
        'source_a': source_a_files,
        'source_b': source_b_files,
        'total_files': len(source_a_files) + len(source_b_files)
    })

@app.route('/api/data/select', methods=['POST'])
def select_data_file():
    """Select a data file for processing"""
    data = request.get_json()
    file_path = data.get('file_path')
    source = data.get('source')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Invalid file path'}), 400
    
    # Generate a unique session ID for this processing session
    session_id = str(uuid.uuid4())
    
    # Store the selected file info
    file_info = {
        'session_id': session_id,
        'file_path': file_path,
        'source': source,
        'filename': os.path.basename(file_path),
        'timestamp': datetime.now().isoformat()
    }
    
    return jsonify({
        'success': True,
        'session_id': session_id,
        'file_info': file_info,
        'message': f'Selected {source} file: {os.path.basename(file_path)}'
    })

@app.route('/api/pipeline/run/<session_id>', methods=['POST'])
def run_pipeline_with_session(session_id):
    """Execute the 3dSAGER pipeline with selected file"""
    # In a real implementation, you would retrieve the session data
    # and process the actual file. For now, we'll simulate the pipeline.
    
    # Simulate pipeline execution for 3dSAGER
    pipeline_stages = [
        {'id': 'preprocessing', 'name': 'Mesh Preprocessing', 'status': 'running'},
        {'id': 'featurization', 'name': 'Geometric Featurization', 'status': 'pending'},
        {'id': 'blocking', 'name': 'BKAFI Blocking', 'status': 'pending'},
        {'id': 'matching', 'name': 'Entity Matching', 'status': 'pending'}
    ]
    
    # Simulate processing time
    time.sleep(2)
    
    # Mock results for 3dSAGER pipeline
    results = {
        'session_id': session_id,
        'objects_processed': 156,
        'processing_time': 3200,
        'matching_confidence': 89.2,
        'pipeline_complete': True,
        'stages': [
            {'id': 'preprocessing', 'name': 'Mesh Preprocessing', 'status': 'completed'},
            {'id': 'featurization', 'name': 'Geometric Featurization', 'status': 'completed'},
            {'id': 'blocking', 'name': 'BKAFI Blocking', 'status': 'completed'},
            {'id': 'matching', 'name': 'Entity Matching', 'status': 'completed'}
        ],
        'entity_resolution_results': {
            'matched_entities': 23,
            'coordinate_agnostic_matches': 18,
            'geometric_similarity_score': 0.89,
            'blocking_efficiency': 0.94
        }
    }
    
    return jsonify(results)

@app.route('/api/data/file/<path:file_path>')
def serve_data_file(file_path):
    """Serve data files for 3D visualization"""
    try:
        print(f"API received file_path: {file_path}")
        
        # Ensure the file path is safe
        if '..' in file_path or file_path.startswith('/'):
            return jsonify({'error': 'Invalid file path'}), 400
        
        # Construct full path
        full_path = os.path.join('data/RawCitiesData/The Hague', file_path)
        print(f"Constructed full_path: {full_path}")
        
        if not os.path.exists(full_path):
            return jsonify({'error': 'File not found'}), 404
        
        # Read and return the JSON file
        with open(full_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0'
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=3000)
