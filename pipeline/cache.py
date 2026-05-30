"""
Cache helpers for the online inference pipeline.

The demo is locked to two CityJSON inputs (Source A = cands, Source B = index).
Every stage's output lives under `results_demo/cache/<input_hash>/`, where
input_hash is a 16-char SHA-256 prefix over the two file paths + sizes + mtimes
+ a config version. Same inputs → same dir → instant cache HITs.
"""

import shutil
import sys
from pathlib import Path

# Make the standalone pipeline bundle importable.
_APP_ROOT = Path(__file__).resolve().parent.parent
_PIPELINE_DIR = _APP_ROOT / 'demo_infrance_pipeline'
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

import pipeline_stages  # imports config_demo as a side effect (mutates config)

CACHE_ROOT = _APP_ROOT / 'results_demo' / 'cache'

# Locked input locations. setup_demo_inputs.sh keeps each of these directories
# populated with exactly one CityJSON file (plus its .prebaked.json sibling).
SOURCE_A_DIR = _APP_ROOT / 'data' / 'RawCitiesData' / 'The Hague' / 'Source A'
SOURCE_B_DIR = _APP_ROOT / 'data' / 'RawCitiesData' / 'The Hague' / 'Source B'


def _pick_input_file(source_dir: Path, role: str) -> Path:
    """Return the unique non-prebaked CityJSON file in source_dir."""
    files = [
        p for p in sorted(source_dir.glob('*.json'))
        if not p.name.endswith('.prebaked.json')
    ]
    if not files:
        raise FileNotFoundError(
            f"No CityJSON file found in {source_dir} for {role}. "
            f"Run scripts/setup_demo_inputs.sh to re-stage the locked inputs."
        )
    if len(files) > 1:
        raise RuntimeError(
            f"Expected exactly one {role} file in {source_dir}, found {len(files)}: "
            f"{[f.name for f in files]}"
        )
    return files[0]


def get_locked_input_paths() -> tuple[str, str]:
    """Return (cands_path, index_path) as strings."""
    cands = _pick_input_file(SOURCE_A_DIR, role='cands')
    index = _pick_input_file(SOURCE_B_DIR, role='index')
    return str(cands), str(index)


def get_current_hash() -> str:
    cands, index = get_locked_input_paths()
    return pipeline_stages.compute_input_hash(cands, index)


def get_cache_dir(input_hash: str = None) -> Path:
    if input_hash is None:
        input_hash = get_current_hash()
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cache_dir = CACHE_ROOT / input_hash
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def wipe_cache(input_hash: str = None) -> Path:
    if input_hash is None:
        input_hash = get_current_hash()
    cache_dir = CACHE_ROOT / input_hash
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    return cache_dir
