"""
Flask routes for single-building CityJSON extraction + cross-file lookup.

Endpoints:

    GET /api/building/single/<id>?file=<path>      — minimal CityJSON for one building
    GET /api/building/find-file/<id>               — search Source A/B for the file containing <id>

The blueprint is mounted at /api (with the second-level path in each route).
"""
import json
import traceback
from pathlib import Path
from typing import List, Optional

from flask import Blueprint, jsonify, request

from lib.cache import cache_get_json, cache_set_json
from lib.config import DATA_DIR
from lib.id_utils import extract_numeric_id, numeric_ids_match

from .geometry import extract_and_remap_vertices

building_api_bp = Blueprint('building_api', __name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_cityjson_path(file_path: str) -> Optional[Path]:
    """Find a CityJSON file given a path that may be relative, may be under
    `RawCitiesData/The Hague/Source A/`, etc. Returns the first existing
    candidate, or None."""
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
    for path in possible_paths:
        if path and path.exists() and path.is_file():
            return path
    return None


def _find_object_id_for_building(city_objects: dict, building_id, numeric_id: str):
    """Find which key in `city_objects` corresponds to the requested
    building_id, allowing for prefix variants (`bag_`, `NL.IMBAG.Pand.`).
    Returns the matching key or None."""
    for obj_id in city_objects:
        if obj_id == building_id or obj_id == numeric_id or numeric_ids_match(obj_id, numeric_id):
            return obj_id
    return None


def _source_dirs(source: str) -> List[Path]:
    """Layout variations for Source A/B drop-ins, in preference order."""
    name_with_space = f'Source {source}'
    name_without_space = f'Source{source}'
    return [
        DATA_DIR / 'RawCitiesData' / 'The Hague' / name_with_space,
        DATA_DIR / 'RawCitiesData' / 'The Hague' / name_without_space,
        DATA_DIR / name_with_space,
        DATA_DIR / name_without_space,
        DATA_DIR,
    ]


def _first_existing_dir(candidates: List[Path]) -> Optional[Path]:
    return next((p for p in candidates if p.exists()), None)


def _search_dir_for_building(directory: Optional[Path], building_id, numeric_id: str) -> Optional[str]:
    """Scan every *.json file under `directory` for a CityObject matching
    `building_id` / `numeric_id`. Returns the matched file's path relative
    to DATA_DIR, or None."""
    if not directory or not directory.exists():
        return None
    for file_path in directory.rglob('*.json'):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            city_objects = data.get('CityObjects', {})
            if _find_object_id_for_building(city_objects, building_id, numeric_id):
                return str(file_path.relative_to(DATA_DIR))
        except Exception as e:
            print(f"Error reading file {file_path}: {e}")
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@building_api_bp.route('/building/single/<building_id>', methods=['GET'])
def get_single_building(building_id):
    """Extract a single building from a CityJSON file and return a minimal
    CityJSON containing just its vertices + geometries (re-indexed). The
    three.js per-building viewer uses this to render one cand or index
    building in isolation."""
    try:
        file_path = request.args.get('file', '')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400

        cache_key = f"building:{file_path}:{building_id}"
        cached_building = cache_get_json(cache_key)
        if cached_building is not None:
            return jsonify(cached_building)

        found_path = _resolve_cityjson_path(file_path)
        if not found_path:
            return jsonify({'error': f'File not found: {file_path}'}), 404

        with open(found_path, 'r', encoding='utf-8') as f:
            city_json = json.load(f)

        numeric_id = str(
            extract_numeric_id(building_id)
            or (building_id.split('_')[-1] if '_' in str(building_id) else building_id)
        )

        target_id = _find_object_id_for_building(city_json.get('CityObjects', {}), building_id, numeric_id)
        if target_id is None:
            return jsonify({'error': f'Building {building_id} not found in file {file_path}'}), 404
        target_building = city_json['CityObjects'][target_id]

        new_vertices, new_geometries = extract_and_remap_vertices(
            target_building,
            city_json.get('vertices', []),
        )

        minimal_cityjson = {
            'type': 'CityJSON',
            'version': city_json.get('version', '1.0'),
            'CityObjects': {
                target_id: {**target_building, 'geometry': new_geometries},
            },
            'vertices': new_vertices,
        }
        if 'metadata' in city_json:
            minimal_cityjson['metadata'] = city_json['metadata']
        if 'transform' in city_json:
            minimal_cityjson['transform'] = city_json['transform']

        cache_set_json(cache_key, minimal_cityjson)
        return jsonify(minimal_cityjson)

    except Exception as e:
        print(f"Error extracting single building: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@building_api_bp.route('/building/find-file/<building_id>', methods=['GET'])
def find_building_file(building_id):
    """Search Source A then Source B for the CityJSON file containing
    `building_id`. Result is cached for 24 h since file→building mappings
    are fixed at deploy time."""
    try:
        numeric_id = str(
            extract_numeric_id(building_id)
            or (building_id.split('_')[-1] if '_' in str(building_id) else building_id)
        )

        cache_key = f"find-file:{numeric_id}"
        cached = cache_get_json(cache_key)
        if cached:
            return jsonify(cached)

        for source in ('A', 'B'):
            directory = _first_existing_dir(_source_dirs(source))
            relative_path = _search_dir_for_building(directory, building_id, numeric_id)
            if relative_path:
                result = {'building_id': building_id, 'file_path': relative_path, 'source': source}
                cache_set_json(cache_key, result, ttl=86400)
                return jsonify(result)

        return jsonify({
            'building_id': building_id,
            'file_path': None,
            'source': None,
            'message': f'Building {building_id} not found in any file',
        }), 404

    except Exception as e:
        print(f"Error finding building file: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
