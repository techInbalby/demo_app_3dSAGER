"""
prebake_cityjson.py
-------------------
Build-time pre-processor for CityJSON files.

For every *.json file under /app/data (or DATA_DIR env var) that looks like a
CityJSON file, this script:
  1. Reads the file and applies the CityJSON transform (scale + translate).
  2. Projects all footprint vertices from the source CRS (EPSG:28992 / EPSG:7415)
     to WGS84 (EPSG:4326) using pyproj.
  3. Extracts one footprint ring + building height per city object.
  4. Writes a compact *.prebaked.json next to the original file.

The pre-baked format is a small JSON file (no raw vertex array) that the browser
can parse without any coordinate maths, removing the need for client-side proj4.

Pre-baked format
----------------
{
  "prebaked": true,
  "version": 1,
  "buildings": {
    "<id>": {
      "type": "<CityObject type>",
      "attributes": { ... },
      "positions": [[lon, lat], ...],   // WGS84 degrees, footprint ring
      "minZ": <float>,                  // metres above datum
      "height": <float>                 // building height in metres
    },
    ...
  }
}

Run:
    python scripts/prebake_cityjson.py
    # or set DATA_DIR env var:
    DATA_DIR=/my/data python scripts/prebake_cityjson.py
"""

import json
import os
import sys
import time
from pathlib import Path

try:
    from pyproj import Transformer
except ImportError:
    print("ERROR: pyproj is not installed. Run: pip install pyproj")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))

# Extra roots scanned in addition to DATA_DIR. Defaults to the demo's per-input-
# hash cache directory so freshly-aligned CityJSON files get pre-baked next to
# their source files without a separate run. Override via EXTRA_PREBAKE_DIRS
# (comma-separated list of paths).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_EXTRA = str(_REPO_ROOT / "results_demo" / "cache")
EXTRA_DIRS = [
    Path(p.strip()) for p in os.environ.get("EXTRA_PREBAKE_DIRS", _DEFAULT_EXTRA).split(",")
    if p.strip()
]

# Horizontal CRS used by The Hague data (EPSG:7415 is a compound CRS whose
# horizontal part is RD New = EPSG:28992).
DEFAULT_SOURCE_CRS = "EPSG:28992"


def _parse_epsg_from_crs(crs_string: str) -> str:
    """Return a pyproj-compatible CRS string from a CityJSON referenceSystem.

    Handles formats like:
      "EPSG:28992"
      "urn:ogc:def:crs:EPSG::28992"
      "http://www.opengis.net/def/crs/EPSG/0/7415"
    """
    if not crs_string:
        return DEFAULT_SOURCE_CRS
    import re
    # OGC URI pattern: .../EPSG/0/<code> — grab the *last* digit run
    m = re.search(r"/EPSG/\d+/(\d+)", crs_string, re.IGNORECASE)
    if not m:
        # Fallback: last sequence of digits preceded by "EPSG" separator
        m = re.search(r"EPSG[:/]+(\d+)", crs_string, re.IGNORECASE)
        # Pick the last (rightmost) match so we get the actual code, not "0"
        for candidate in re.finditer(r"EPSG[:/]+(\d+)", crs_string, re.IGNORECASE):
            m = candidate
    if m:
        code = int(m.group(1))
        # EPSG:7415 is a compound CRS (RD New + NAP height); use horizontal only
        if code == 7415:
            return DEFAULT_SOURCE_CRS
        return f"EPSG:{code}"
    return DEFAULT_SOURCE_CRS


def _make_transformer(source_crs: str):
    """Create a pyproj Transformer for source_crs -> WGS84, with caching."""
    return Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)


def _get_footprint_and_height(geometry: dict, vertices: list, transform: dict):
    """
    Extract the footprint ring indices, apply the CityJSON affine transform and
    compute min/max Z across the whole city object geometry.

    Returns (footprint_indices, minZ, maxZ) or (None, None, None) on failure.
    """
    scale = transform["scale"] if transform else [1, 1, 1]
    translate = transform["translate"] if transform else [0, 0, 0]

    def apply(idx):
        v = vertices[idx]
        return (
            v[0] * scale[0] + translate[0],
            v[1] * scale[1] + translate[1],
            v[2] * scale[2] + translate[2],
        )

    geom_type = geometry.get("type", "")
    boundaries = geometry.get("boundaries", [])

    footprint_indices = None

    if geom_type == "Solid":
        outer_shell = boundaries[0] if boundaries else []
        if outer_shell and outer_shell[0] and outer_shell[0][0]:
            footprint_indices = outer_shell[0][0]  # first face, first ring
    elif geom_type in ("MultiSurface", "CompositeSurface"):
        if boundaries and boundaries[0]:
            footprint_indices = boundaries[0][0]  # first ring of first surface

    if not footprint_indices:
        return None, None, None

    # Collect footprint ring raw (transformed) coords
    footprint_raw = []
    minZ = float("inf")
    maxZ = float("-inf")

    for idx in footprint_indices:
        if idx < 0 or idx >= len(vertices):
            continue
        x, y, z = apply(idx)
        footprint_raw.append((x, y, z))
        if z < minZ:
            minZ = z
        if z > maxZ:
            maxZ = z

    if not footprint_raw:
        return None, None, None

    # For Solid: scan *all* faces to find the true maxZ (roof height)
    if geom_type == "Solid":
        outer_shell = boundaries[0] if boundaries else []
        for face in outer_shell:
            for ring in face:
                for idx in ring:
                    if idx < 0 or idx >= len(vertices):
                        continue
                    z = vertices[idx][2] * scale[2] + translate[2]
                    if z < minZ:
                        minZ = z
                    if z > maxZ:
                        maxZ = z

    return footprint_raw, minZ, maxZ


def prebake_file(json_path: Path) -> bool:
    """
    Generate a .prebaked.json for a single CityJSON file.

    Returns True on success, False on error.
    """
    out_path = json_path.with_suffix(".prebaked.json")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"  ERROR reading {json_path.name}: {exc}")
        return False

    # Validate it's a CityJSON file
    if data.get("type") != "CityJSON" or "CityObjects" not in data:
        print(f"  Skipping {json_path.name}: not a CityJSON file")
        return False

    # Also skip if it's somehow already a pre-baked stub
    if data.get("prebaked"):
        print(f"  Skipping {json_path.name}: already pre-baked")
        return True

    city_objects = data.get("CityObjects", {})
    vertices = data.get("vertices", [])
    transform = data.get("transform", None)
    crs_string = data.get("metadata", {}).get("referenceSystem", "")
    source_crs = _parse_epsg_from_crs(crs_string)

    transformer = _make_transformer(source_crs)

    buildings_out = {}
    skipped = 0

    for obj_id, city_obj in city_objects.items():
        geometries = city_obj.get("geometry", [])
        if not geometries:
            skipped += 1
            continue

        # Use the first (usually only) geometry
        geo = geometries[0]
        footprint_raw, minZ, maxZ = _get_footprint_and_height(geo, vertices, transform)

        if footprint_raw is None or minZ == float("inf"):
            skipped += 1
            continue

        building_height = maxZ - minZ
        if building_height <= 0:
            skipped += 1
            continue

        # Project footprint ring to WGS84
        positions_wgs84 = []
        for x, y, _ in footprint_raw:
            lon, lat = transformer.transform(x, y)
            positions_wgs84.append([lon, lat])

        buildings_out[obj_id] = {
            "type": city_obj.get("type", "Building"),
            "attributes": city_obj.get("attributes", {}),
            "positions": positions_wgs84,
            "minZ": round(minZ, 4),
            "height": round(building_height, 4),
        }

    prebaked = {
        "prebaked": True,
        "version": 1,
        "buildings": buildings_out,
    }

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(prebaked, f, separators=(",", ":"))  # compact output
    except Exception as exc:
        print(f"  ERROR writing {out_path.name}: {exc}")
        return False

    in_mb = json_path.stat().st_size / 1_048_576
    out_mb = out_path.stat().st_size / 1_048_576
    reduction = (1 - out_mb / in_mb) * 100 if in_mb > 0 else 0
    print(
        f"  {json_path.name}: {len(buildings_out)} buildings "
        f"({skipped} skipped) | "
        f"{in_mb:.1f} MB -> {out_mb:.1f} MB ({reduction:.0f}% smaller)"
    )
    return True


def main():
    # Build the list of roots to walk: DATA_DIR plus any EXTRA_DIRS that exist.
    roots = []
    if DATA_DIR.exists():
        roots.append(DATA_DIR)
    else:
        print(f"DATA_DIR does not exist (skipping): {DATA_DIR}")
    for extra in EXTRA_DIRS:
        if extra.exists():
            roots.append(extra)
        else:
            print(f"EXTRA_DIR not present (skipping): {extra}")

    if not roots:
        print("No directories to scan. Exiting.")
        sys.exit(1)

    print("Pre-baking CityJSON files under:")
    for r in roots:
        print(f"  - {r}")
    print()

    t0 = time.time()
    ok = fail = skipped = 0

    for root in roots:
        json_files = sorted(root.rglob("*.json"))
        candidates = [p for p in json_files if not p.name.endswith(".prebaked.json")]
        if not candidates:
            continue
        for p in candidates:
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = p
            print(f"Processing: {root.name}/{rel}")
            result = prebake_file(p)
            if result is True:
                ok += 1
            elif result is False:
                fail += 1
            else:
                skipped += 1

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s — {ok} succeeded, {fail} failed, {skipped} skipped")


if __name__ == "__main__":
    main()
