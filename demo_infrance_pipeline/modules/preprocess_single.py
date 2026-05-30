"""
preprocess_single.py

Single-file adapter for preprocess_hague: reads ONE CityJSON file as cands
and ONE as index, instead of walking source directories.

All low-level helpers (transform, key standardization, mesh extraction,
grid-cell assignment) are reused from preprocess_hague. The only behavioural
difference is that this version does NOT filter cands to shared IDs by
default — demo files may legitimately have no overlap.
"""

import os
import joblib
import numpy as np

from preprocess_hague import _process_file, _assign_grid_cells


def preprocess_files(
    cands_file: str,
    index_file: str,
    output_path: str,
    cell_size: float = 500.0,
    min_surfaces: int = 10,
    require_shared_ids: bool = False,
):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    print(f"  reading cands: {os.path.basename(cands_file)}")
    cands = _process_file((0, cands_file, 'cands', min_surfaces))
    print(f"    -> {len(cands)} cand buildings")

    print(f"  reading index: {os.path.basename(index_file)}")
    index = _process_file((0, index_file, 'index', min_surfaces))
    print(f"    -> {len(index)} index buildings")

    if not cands or not index:
        raise RuntimeError(
            f"Empty input: cands={len(cands)} index={len(index)}. "
            "Check file paths and that the CityJSON contains usable Solid geometry."
        )

    shared_ids = set(cands.keys()) & set(index.keys())
    print(f"  shared BAG IDs (cands ∩ index): {len(shared_ids)}")
    if require_shared_ids:
        cands = {k: cands[k] for k in shared_ids}
        if not cands:
            raise RuntimeError("require_shared_ids=True but cands and index share no IDs.")

    all_centroids = np.array([b['centroid'] for b in index.values()])
    x_min, y_min = float(all_centroids[:, 0].min()), float(all_centroids[:, 1].min())
    x_max, y_max = float(all_centroids[:, 0].max()), float(all_centroids[:, 1].max())
    print(f"  spatial extent (index centroids): "
          f"X=[{x_min:.0f}, {x_max:.0f}], Y=[{y_min:.0f}, {y_max:.0f}]")

    _assign_grid_cells(cands, x_min, y_min, cell_size)
    _assign_grid_cells(index, x_min, y_min, cell_size)

    mapping_dict, inv_mapping_dict = {}, {}
    for src, d in [('cands', cands), ('index', index)]:
        keys = sorted(d.keys())
        mapping_dict[src]     = {i: k for i, k in enumerate(keys)}
        inv_mapping_dict[src] = {k: i for i, k in enumerate(keys)}

    result = {
        'cands':            cands,
        'index':            index,
        'mapping_dict':     mapping_dict,
        'inv_mapping_dict': inv_mapping_dict,
        'grid_meta':        {'x_min': x_min, 'y_min': y_min, 'cell_size': cell_size},
    }

    print(f"  saving cache: {output_path}")
    joblib.dump(result, output_path, compress=3)
    size_mb = os.path.getsize(output_path) / 1e6
    print(f"    {len(cands)} cands | {len(index)} index | {size_mb:.1f} MB")
    return result
