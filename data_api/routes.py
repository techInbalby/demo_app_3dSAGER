"""
Flask routes for the data-file picker + raw CityJSON streaming.

Endpoints (registered under /api/data):

    GET    /files                    — list locked Source A / Source B CityJSON files
    POST   /select                   — confirm a layer choice (returns a session id)
    GET    /file/<path>              — stream the prebaked-or-raw CityJSON at <path>
    GET    /file?path=<path>         — same, via query param (avoids URL-encoded slashes)

Source A and Source B directories follow the inference pipeline's convention:
Source A = cands, Source B = index. The demo is locked to exactly one CityJSON
per source (scripts/setup_demo_inputs.sh enforces this).

The /file routes prefer the .prebaked.json sibling when it exists — the Cesium
viewer's fast WGS84 path avoids proj4 transforms when the geometry has been
pre-baked.
"""
import hashlib
import json
import os
import traceback
import uuid
from pathlib import Path
from urllib.parse import unquote

from flask import Blueprint, jsonify, make_response, request

from lib.cache import cache_get_json, cache_set_json
from lib.config import DATA_DIR

data_api_bp = Blueprint('data_api', __name__)


# ---------------------------------------------------------------------------
# Source directory resolution
# ---------------------------------------------------------------------------

def _candidate_source_dirs(source: str):
    """All filesystem layouts we've ever supported for a Source A/B drop-in.
    Listed in the order we try them — first existing wins."""
    name_with_space = f'Source {source}'        # "Source A"
    name_without_space = f'Source{source}'      # "SourceA"
    return [
        DATA_DIR / 'RawCitiesData' / 'The Hague' / name_with_space,
        DATA_DIR / 'RawCitiesData' / 'The Hague' / name_without_space,
        DATA_DIR / name_with_space,
        DATA_DIR / name_without_space,
        DATA_DIR,
    ]


def _resolve_source_dir(source: str) -> Path:
    """First existing candidate dir for Source A or B; falls back to DATA_DIR."""
    for path in _candidate_source_dirs(source):
        if path.exists():
            return path
    return DATA_DIR


def _list_cityjson(directory: Path) -> list:
    """Return [{filename, path (relative to DATA_DIR), size}] for every
    non-prebaked .json under `directory`. Prebaked siblings are an internal
    optimisation; users shouldn't be able to pick them as layers."""
    files = []
    if not (directory.exists() and directory.is_dir()):
        return files
    for file_path in directory.rglob('*.json'):
        if file_path.name.endswith('.prebaked.json'):
            continue
        try:
            rel_path = file_path.relative_to(DATA_DIR)
            path_str = str(rel_path)
        except ValueError:
            path_str = str(file_path)
        files.append({
            'filename': file_path.name,
            'path': path_str,
            'size': file_path.stat().st_size,
        })
    return files


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@data_api_bp.route('/files', methods=['GET'])
def get_files():
    """List available CityJSON files from Source A and Source B."""
    try:
        return jsonify({
            'source_a': _list_cityjson(_resolve_source_dir('A')),
            'source_b': _list_cityjson(_resolve_source_dir('B')),
        })
    except Exception as e:
        return jsonify({'error': str(e), 'source_a': [], 'source_b': []}), 500


@data_api_bp.route('/select', methods=['POST'])
def select_file():
    """Confirm a layer choice. Validates the path exists; returns a session id."""
    try:
        data = request.get_json() or {}
        file_path = data.get('file_path')
        source = data.get('source', 'A')

        if not file_path:
            return jsonify({'success': False, 'error': 'No file path provided'}), 400

        full_path = DATA_DIR / file_path
        if not full_path.exists():
            alt_paths = [
                DATA_DIR / 'RawCitiesData' / 'The Hague' / file_path,
                DATA_DIR / file_path,
                Path(file_path) if os.path.isabs(file_path) else None,
            ]
            for alt in alt_paths:
                if alt and alt.exists():
                    full_path = alt
                    break
            else:
                return jsonify({'success': False, 'error': f'File not found: {file_path}'}), 404

        return jsonify({
            'success': True,
            'session_id': str(uuid.uuid4()),
            'file_path': file_path,
            'source': source,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _stream_cityjson(file_path: str):
    """Shared implementation used by both /file/<path> and /file?path=."""
    try:
        file_path = unquote(str(file_path))
        file_name = Path(file_path).name
        possible_paths = [
            DATA_DIR / file_path,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source A' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'Source B' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceA' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / 'SourceB' / file_name,
            DATA_DIR / 'RawCitiesData' / 'The Hague' / file_path,
        ]
        # If the request already includes the structure prefix, try the raw
        # path first.
        if 'RawCitiesData' in file_path or 'The Hague' in file_path:
            possible_paths.insert(0, DATA_DIR / file_path)

        found_path = next((p for p in possible_paths if p and p.exists() and p.is_file()), None)
        if not found_path:
            return jsonify({
                'error': f'File not found: {file_path}',
                'tried_paths': [str(p) for p in possible_paths if p],
                'data_dir': str(DATA_DIR),
                'data_dir_exists': DATA_DIR.exists(),
            }), 404

        # Prefer the prebaked sibling — Cesium loads it ~10× faster.
        prebaked_path = found_path.with_suffix('.prebaked.json')
        if prebaked_path.exists():
            found_path = prebaked_path

        mtime = found_path.stat().st_mtime
        etag = hashlib.md5(f"{found_path}_{mtime}".encode()).hexdigest()

        if request.headers.get('If-None-Match') == etag:
            response = make_response('', 304)
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

        response = jsonify(data)
        response.headers['ETag'] = etag
        response.headers['Cache-Control'] = 'public, max-age=3600'
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
        cache_set_json(cache_key, data)
        return response

    except json.JSONDecodeError as e:
        return jsonify({'error': f'Invalid JSON: {str(e)}'}), 400
    except Exception as e:
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc(),
        }), 500


@data_api_bp.route('/file/<path:file_path>', methods=['GET'])
def get_file(file_path):
    """Stream a CityJSON file by URL path (path traversal handled by resolver)."""
    return _stream_cityjson(file_path)


@data_api_bp.route('/file', methods=['GET'])
def get_file_by_query():
    """Stream a CityJSON file by query param (avoids URL-encoded slashes)."""
    file_path = request.args.get('path')
    if not file_path:
        return jsonify({'error': 'Missing path parameter'}), 400
    return _stream_cityjson(file_path)
