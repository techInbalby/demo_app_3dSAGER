"""CityJSON geometry surgery for single-building extraction.

`get_single_building` needs to pull one CityObject out of a parent CityJSON
file and emit a minimal CityJSON containing just that building's vertices,
re-indexed so the remap is correct. The walk + remap is fiddly enough to be
worth its own module (and worth testing independently — the off-by-one
ceiling here is a silent regression that produces invisible 3D geometry).

CityJSON supports both Solid (4-level nested: shells/faces/rings/indices)
and MultiSurface (3-level nested: surfaces/rings/indices). The helpers
below handle both."""
from typing import Iterable, Set


def collect_vertex_indices(geometry: dict) -> Set[int]:
    """Walk a single Geometry object and return the set of vertex indices
    it references. Handles Solid + MultiSurface; ignores anything else
    silently (CityJSON also defines MultiSolid, CompositeSurface, etc., but
    the demo's CityJSON files only contain Solid + MultiSurface)."""
    indices: Set[int] = set()
    geom_type = geometry.get('type')
    boundaries = geometry.get('boundaries') or []
    if geom_type == 'Solid':
        for shell in boundaries:
            for face in shell:
                for ring in face:
                    for v_idx in ring:
                        if isinstance(v_idx, int) and v_idx >= 0:
                            indices.add(v_idx)
    elif geom_type == 'MultiSurface':
        for surface in boundaries:
            for ring in surface:
                for v_idx in ring:
                    if isinstance(v_idx, int) and v_idx >= 0:
                        indices.add(v_idx)
    return indices


def collect_all_vertex_indices(geometries: Iterable[dict]) -> Set[int]:
    """Union of `collect_vertex_indices` across every geometry on a building."""
    out: Set[int] = set()
    for g in geometries:
        out |= collect_vertex_indices(g)
    return out


def remap_geometry(geometry: dict, index_mapping: dict) -> dict:
    """Return a copy of `geometry` with every vertex index translated through
    `index_mapping`. Geometries the walker doesn't recognise are passed
    through unchanged."""
    new_geometry = dict(geometry)
    geom_type = new_geometry.get('type')
    boundaries = new_geometry.get('boundaries') or []
    if geom_type == 'Solid':
        new_geometry['boundaries'] = [
            [
                [
                    [index_mapping.get(v_idx, v_idx) for v_idx in ring]
                    for ring in face
                ]
                for face in shell
            ]
            for shell in boundaries
        ]
    elif geom_type == 'MultiSurface':
        new_geometry['boundaries'] = [
            [
                [index_mapping.get(v_idx, v_idx) for v_idx in ring]
                for ring in surface
            ]
            for surface in boundaries
        ]
    return new_geometry


def extract_and_remap_vertices(target_building: dict, all_vertices: list):
    """End-to-end: collect indices used by target_building, build the
    old→new map, trim the vertex array, and rewrite every boundary in the
    building's geometries. Returns (new_vertices, new_geometries).

    The same vertex can appear in multiple boundaries (faces sharing edges);
    the set automatically deduplicates. Indices >= len(all_vertices) are
    silently dropped — the demo's CityJSON files have always been well-formed,
    but the bound check is cheap insurance.
    """
    geometries = target_building.get('geometry', []) or []
    used = collect_all_vertex_indices(geometries)
    sorted_used = sorted(used)
    index_mapping = {old: new for new, old in enumerate(sorted_used)}
    new_vertices = [all_vertices[i] for i in sorted_used if i < len(all_vertices)]
    new_geometries = [remap_geometry(g, index_mapping) for g in geometries]
    return new_vertices, new_geometries
