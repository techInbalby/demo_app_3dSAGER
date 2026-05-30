"""
preprocess_hague.py

One-time preprocessing for The Hague dataset.

Reads ALL CityJSON files from Source A (cands) and Source B (index), extracts
polygon meshes, applies CityJSON transforms to get real-world EPSG:7415
coordinates, computes per-building centroids, assigns spatial grid cell IDs,
and saves a single joblib file.

Output
------
data/object_dicts/Hague_raw.joblib
    {
      'cands':  {building_id: {'polygon_mesh': ..., 'vertices': ndarray,
                               'centroid': ndarray, 'grid_cell': (cx, cy)}},
      'index':  {same structure},
      'grid_meta': {'x_min': float, 'y_min': float, 'cell_size': float}
    }

Only buildings whose ID appears in BOTH sources are kept in cands (positive-pair
requirement for entity resolution).  Index keeps all buildings.

Usage
-----
    conda run -n 3dsager python preprocess_hague.py
    conda run -n 3dsager python preprocess_hague.py --cell_size 500 --min_surfaces 10
"""

import argparse
import json
import math
import os
from multiprocessing import Pool, cpu_count

import joblib
import numpy as np


# ---------------------------------------------------------------------------
# CityJSON helpers
# ---------------------------------------------------------------------------

def _apply_transform(raw_vertices, transform):
    """Convert integer-compressed CityJSON vertices to real-world coordinates."""
    scale = transform.get('scale', [1.0, 1.0, 1.0])
    translate = transform.get('translate', [0.0, 0.0, 0.0])
    result = []
    for v in raw_vertices:
        result.append([v[j] * scale[j] + translate[j] for j in range(3)])
    return result


def _get_vertices_array(polygon_mesh):
    """Unique vertex array (N, 3) from a polygon mesh."""
    all_coords = [coord for surface in polygon_mesh for coord in surface]
    return np.unique(np.array(all_coords, dtype=np.float64), axis=0)


_LOD_ORDER = {'2.2': 5, '2': 4, '1.3': 3, '1.2': 2, '1': 1, '0': 0}


def _best_solid_geometry(geometries):
    """
    Return the highest-LOD Solid geometry from a list of CityJSON geometries.
    Falls back to MultiSurface if no Solid is found.
    Returns None if no usable geometry exists.
    """
    best_geom = None
    best_score = -1
    for g in geometries:
        score = _LOD_ORDER.get(str(g.get('lod', '0')), 0)
        if score > best_score and g.get('boundaries'):
            best_geom = g
            best_score = score
    return best_geom


def _get_polygon_mesh(data, obj_key, world_vertices, min_surfaces):
    """
    Extract polygon mesh for one building, using the highest available LOD.

    Source A (cands): Building + single Solid LOD2 geometry.
    Source B (index): BuildingPart + Solid LOD1.2 / LOD1.3 / LOD2.2 geometries.

    Parameters
    ----------
    data : dict          CityJSON file dict
    obj_key : str        raw object key in CityObjects
    world_vertices : list  real-world [x, y, z] vertices (transform already applied)
    min_surfaces : int   reject buildings with fewer surfaces

    Returns
    -------
    dict or None
    """
    city_obj = data['CityObjects'][obj_key]
    geometries = city_obj.get('geometry', [])
    if not geometries:
        return None

    geom = _best_solid_geometry(geometries)
    if geom is None:
        return None

    boundaries = geom.get('boundaries', [])
    if not boundaries:
        return None

    # Solid: boundaries[0] is the outer shell (list of surfaces)
    # MultiSurface: boundaries itself is the list of surfaces
    if geom.get('type') == 'Solid':
        surfaces_raw = boundaries[0]
    else:
        surfaces_raw = boundaries

    if len(surfaces_raw) < min_surfaces:
        return None

    polygon_mesh = []
    for surface in surfaces_raw:
        coords = []
        for sub in surface:
            if isinstance(sub, list):
                coords.extend([world_vertices[i] for i in sub])
            else:
                coords.append(world_vertices[sub])
        if coords:
            polygon_mesh.append(coords)

    if not polygon_mesh:
        return None

    verts = _get_vertices_array(polygon_mesh)
    centroid = verts.mean(axis=0)
    return {'polygon_mesh': polygon_mesh, 'vertices': verts, 'centroid': centroid}


def _standardize_key(raw_key, source_type=None):
    """Strip source-specific prefixes to get the bare BAG numeric ID.

    Works regardless of which source is assigned to cands vs index:
    - 'bag_0363100012160421'            → '0363100012160421'
    - 'NL.IMBAG.Pand.0363100012160421-0' → '0363100012160421'
    """
    if 'bag_' in raw_key:
        return raw_key.split('bag_')[1]
    if 'NL.IMBAG.Pand.' in raw_key:
        return raw_key.split('NL.IMBAG.Pand.')[1].split('-0')[0]
    return raw_key


# ---------------------------------------------------------------------------
# Per-file worker (runs in subprocess via multiprocessing)
# ---------------------------------------------------------------------------

def _process_file(args):
    file_idx, file_path, source_type, min_surfaces = args
    print(f"  [{source_type}] file {file_idx}: {os.path.basename(file_path)}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"    ERROR reading {file_path}: {e}")
        return {}

    raw_vertices = data.get('vertices', [])
    transform = data.get('transform')
    if transform:
        world_vertices = _apply_transform(raw_vertices, transform)
    else:
        world_vertices = raw_vertices  # already floats

    partial = {}
    for raw_key in data.get('CityObjects', {}):
        city_obj = data['CityObjects'][raw_key]
        # Skip non-building types
        if city_obj.get('type') == 'BuildingInstallation':
            continue
        # Note: BuildingPart entries ARE processed — for Source B (3D BAG) the 3D
        # geometry (LOD1.2 / LOD1.3 / LOD2.2) lives in BuildingPart, not Building.
        try:
            std_key = _standardize_key(raw_key, source_type)
        except Exception:
            continue
        try:
            mesh_data = _get_polygon_mesh(data, raw_key, world_vertices, min_surfaces)
        except Exception:
            continue
        if mesh_data is not None:
            partial[std_key] = mesh_data

    return partial


# ---------------------------------------------------------------------------
# Grid cell assignment
# ---------------------------------------------------------------------------

def _assign_grid_cells(buildings, x_min, y_min, cell_size):
    """Add 'grid_cell': (cx, cy) to each building dict in-place."""
    for bid, bdata in buildings.items():
        cx_world, cy_world = float(bdata['centroid'][0]), float(bdata['centroid'][1])
        cx = int(math.floor((cx_world - x_min) / cell_size))
        cy = int(math.floor((cy_world - y_min) / cell_size))
        bdata['grid_cell'] = (cx, cy)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def preprocess(cands_path, index_path, output_path, cell_size=500.0, min_surfaces=10):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Collect file lists
    cands_files = sorted(f for f in os.listdir(cands_path) if f.endswith('.json'))
    index_files = sorted(f for f in os.listdir(index_path) if f.endswith('.json'))
    print(f"Source A (cands): {len(cands_files)} files")
    print(f"Source B (index): {len(index_files)} files")

    n_workers = max(1, cpu_count() - 2)

    # --- Read Source A (cands) ---
    print(f"\nReading Source A with {n_workers} workers...")
    cands_args = [
        (i, os.path.join(cands_path, fn), 'cands', min_surfaces)
        for i, fn in enumerate(cands_files)
    ]
    with Pool(processes=n_workers) as pool:
        cands_results = pool.map(_process_file, cands_args)

    cands = {}
    for partial in cands_results:
        cands.update(partial)
    print(f"  → {len(cands)} cand buildings loaded")

    # --- Read Source B (index) ---
    print(f"\nReading Source B with {n_workers} workers...")
    index_args = [
        (i, os.path.join(index_path, fn), 'index', min_surfaces)
        for i, fn in enumerate(index_files)
    ]
    with Pool(processes=n_workers) as pool:
        index_results = pool.map(_process_file, index_args)

    index = {}
    for partial in index_results:
        index.update(partial)
    print(f"  → {len(index)} index buildings loaded")

    # --- Keep only cands whose ID appears in index (entity resolution requirement) ---
    shared_ids = set(cands.keys()) & set(index.keys())
    print(f"\nShared BAG IDs (cands ∩ index): {len(shared_ids)}")
    cands = {k: cands[k] for k in shared_ids}

    if not cands:
        raise RuntimeError(
            "No shared building IDs found between Source A and Source B. "
            "Check that standardize_key() is stripping prefixes correctly."
        )

    # --- Compute global bounding box (from index — the reference dataset) ---
    all_centroids = np.array([b['centroid'] for b in index.values()])
    x_min, y_min = float(all_centroids[:, 0].min()), float(all_centroids[:, 1].min())
    x_max, y_max = float(all_centroids[:, 0].max()), float(all_centroids[:, 1].max())
    print(f"\nSpatial extent (index centroids, EPSG:7415):")
    print(f"  X: {x_min:.0f} – {x_max:.0f}  ({x_max - x_min:.0f} m)")
    print(f"  Y: {y_min:.0f} – {y_max:.0f}  ({y_max - y_min:.0f} m)")

    # --- Assign grid cells ---
    print(f"\nAssigning {cell_size:.0f} m grid cells...")
    _assign_grid_cells(cands, x_min, y_min, cell_size)
    _assign_grid_cells(index, x_min, y_min, cell_size)

    unique_cells_cands  = set(b['grid_cell'] for b in cands.values())
    unique_cells_index  = set(b['grid_cell'] for b in index.values())
    shared_cells = unique_cells_cands & unique_cells_index
    print(f"  Grid cells — cands: {len(unique_cells_cands)}, "
          f"index: {len(unique_cells_index)}, shared: {len(shared_cells)}")

    # --- Mapping dicts (integer index ↔ string ID) ---
    mapping_dict     = {}
    inv_mapping_dict = {}
    for src, d in [('cands', cands), ('index', index)]:
        keys = sorted(d.keys())
        mapping_dict[src]     = {i: k for i, k in enumerate(keys)}
        inv_mapping_dict[src] = {k: i for i, k in enumerate(keys)}

    # --- Save ---
    result = {
        'cands':            cands,
        'index':            index,
        'mapping_dict':     mapping_dict,
        'inv_mapping_dict': inv_mapping_dict,
        'grid_meta': {
            'x_min':     x_min,
            'y_min':     y_min,
            'cell_size': cell_size,
        },
    }

    print(f"\nSaving to {output_path} ...")
    joblib.dump(result, output_path, compress=3)
    size_mb = os.path.getsize(output_path) / 1e6
    print(f"Done.  {len(cands)} cands | {len(index)} index | {size_mb:.1f} MB")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Preprocess The Hague CityJSON dataset')
    parser.add_argument('--cands_path',   default='../The Hague/Source B/')
    parser.add_argument('--index_path',   default='../The Hague/Source A/')
    parser.add_argument('--output',       default='data/object_dicts/Hague_raw.joblib')
    parser.add_argument('--cell_size',    type=float, default=500.0,
                        help='Grid cell size in metres (EPSG:7415)')
    parser.add_argument('--min_surfaces', type=int,   default=10,
                        help='Minimum surfaces to accept a building')
    args = parser.parse_args()

    preprocess(
        cands_path=args.cands_path,
        index_path=args.index_path,
        output_path=args.output,
        cell_size=args.cell_size,
        min_surfaces=args.min_surfaces,
    )
