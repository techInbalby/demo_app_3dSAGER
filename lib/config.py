"""Centralised configuration for the demo app.

Single source of truth for filesystem layout, Redis/cache settings, and the
hardcoded classifier confidence threshold. Imported by every blueprint
that needs to know where data lives or how to talk to Redis.

Environment variables (read here, not re-read elsewhere):
    REDIS_URL              default redis://redis:6379/0
    CACHE_TTL_SECONDS      default 21600 (6 h)
"""
import os
from pathlib import Path

# Repo root — the directory that holds app.py.
BASE_DIR = Path(__file__).resolve().parent.parent

# Top-level data dirs.
DATA_DIR = BASE_DIR / 'data'
RESULTS_DIR = BASE_DIR / 'results_demo'
SAVED_MODEL_DIR = BASE_DIR / 'saved_model_files'
LOGS_DIR = BASE_DIR / 'logs'

# Per-input-hash cache root used by the new pipeline (see pipeline_stages.py).
PIPELINE_CACHE_ROOT = RESULTS_DIR / 'cache'

# Pre-baked legacy results — still read by /api/classifier/summary.
DEMO_RESULTS_JSON = RESULTS_DIR / 'demo_inference' / 'demo_detailed_results_XGBClassifier_seed1.json'
DEMO_METRICS_JSON = RESULTS_DIR / 'demo_inference' / 'demo_metrics_summary_seed1.json'

# Pre-computed feature parquet — fallback path when Redis features:* keys are missing.
FEATURES_PARQUET = DATA_DIR / 'property_dicts' / 'features.parquet'

# Legacy classifier confidence cutoff (used by the bkafi/matches routes). The
# new alignment pipeline uses pipeline_stages.DEFAULT_MATCH_THRESHOLD (0.40)
# — these aren't related; this 0.5 only gates the pre-alignment per-building
# match badge the old UI showed.
CONFIDENCE_THRESHOLD = 0.5

# Redis connection details.
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
CACHE_TTL_SECONDS = int(os.getenv('CACHE_TTL_SECONDS', '21600'))


def ensure_directories_exist():
    """Create the data/results/model/log dirs if they don't already.

    Called once at app startup. Idempotent."""
    for directory in (DATA_DIR, RESULTS_DIR, SAVED_MODEL_DIR, LOGS_DIR):
        directory.mkdir(exist_ok=True)
