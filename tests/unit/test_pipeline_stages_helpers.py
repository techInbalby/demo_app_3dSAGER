"""Unit tests for pure helpers in pipeline_stages.py.

These functions are deterministic and don't touch the heavy ML stack
(faiss, xgboost, etc.) — testable with stdlib + numpy alone.
"""
import json
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_compute_input_hash_deterministic(locked_inputs):
    """Same inputs → same 16-char hex hash, both calls."""
    import pipeline_stages
    h1 = pipeline_stages.compute_input_hash(str(locked_inputs['cands']), str(locked_inputs['index']))
    h2 = pipeline_stages.compute_input_hash(str(locked_inputs['cands']), str(locked_inputs['index']))
    assert h1 == h2
    assert len(h1) == 16
    assert all(c in '0123456789abcdef' for c in h1)


def test_compute_input_hash_changes_with_config_version(locked_inputs):
    """CONFIG_VERSION is mixed into the hash so cache busts on config bumps."""
    import pipeline_stages
    h_default = pipeline_stages.compute_input_hash(str(locked_inputs['cands']), str(locked_inputs['index']))
    h_other = pipeline_stages.compute_input_hash(
        str(locked_inputs['cands']), str(locked_inputs['index']), config_version='deliberately_other'
    )
    assert h_default != h_other


def test_compute_input_hash_changes_when_path_changes(tmp_path):
    """Different paths → different hashes (assuming the files differ)."""
    import pipeline_stages
    a = tmp_path / 'a.json'
    b = tmp_path / 'b.json'
    a.write_text('{"x":1}')
    b.write_text('{"y":2}')
    assert pipeline_stages.compute_input_hash(str(a), str(b)) != pipeline_stages.compute_input_hash(str(b), str(a))


def test_ensure_cache_dir_creates(tmp_path):
    import pipeline_stages
    out = pipeline_stages.ensure_cache_dir(tmp_path, 'abc123')
    assert out.exists() and out.is_dir()
    assert out.name == 'abc123'


def test_atomic_write_json_roundtrip(tmp_path):
    """_atomic_write_json writes valid JSON that round-trips."""
    import pipeline_stages
    payload = {'a': 1, 'b': [1, 2, 3], 'c': None, 'nested': {'x': 'y'}}
    target = tmp_path / 'out.json'
    pipeline_stages._atomic_write_json(payload, target)
    assert json.loads(target.read_text()) == payload


def test_atomic_write_json_overwrites(tmp_path):
    """Second write replaces the first (no append, no error)."""
    import pipeline_stages
    target = tmp_path / 'out.json'
    pipeline_stages._atomic_write_json({'v': 1}, target)
    pipeline_stages._atomic_write_json({'v': 2}, target)
    assert json.loads(target.read_text()) == {'v': 2}


def test_default_match_threshold_is_in_open_unit_interval():
    """0 < threshold < 1 (we set 0.40 because the source paper's 0.65 was
    tuned for the ±100km regime, not our 500m demo)."""
    import pipeline_stages
    assert 0.0 < pipeline_stages.DEFAULT_MATCH_THRESHOLD < 1.0


def test_config_version_pinned():
    """CONFIG_VERSION drives full cache invalidation — changing this is a
    deliberate act. Test pins the current value so a careless bump shows up
    in code review."""
    import pipeline_stages
    assert pipeline_stages.CONFIG_VERSION == 'v2'


def test_record_stage_writes_manifest(tmp_path):
    """_record_stage updates manifest.json under cache_dir."""
    import pipeline_stages
    pipeline_stages._record_stage(tmp_path, 'demo_stage', 0.42, cache_hit=False, extra_field='x')
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert 'stages' in manifest
    assert manifest['stages']['demo_stage']['cache_hit'] is False
    assert manifest['stages']['demo_stage']['duration_s'] == 0.42
    assert manifest['stages']['demo_stage']['extra_field'] == 'x'
    assert 'mtime' in manifest['stages']['demo_stage']


def test_record_stage_appends_without_clobbering(tmp_path):
    """Recording stage B doesn't drop stage A's entry."""
    import pipeline_stages
    pipeline_stages._record_stage(tmp_path, 'a', 1.0, cache_hit=True)
    pipeline_stages._record_stage(tmp_path, 'b', 2.0, cache_hit=False)
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert set(manifest['stages'].keys()) == {'a', 'b'}
    assert manifest['stages']['a']['cache_hit'] is True
    assert manifest['stages']['b']['cache_hit'] is False


def test_invalidate_downstream_of_blocking_drops_classify_and_align(tmp_path):
    """When K changes, _invalidate_downstream_of_blocking removes the
    scored_pairs + alignment outputs so they recompute on next click."""
    import pipeline_stages
    # Create fake downstream outputs
    (tmp_path / 'scored_pairs.joblib').write_bytes(b'fake')
    (tmp_path / 'alignment_info.json').write_text('{}')
    (tmp_path / 'matches_by_cand.json').write_text('{}')
    # Seed a manifest with classify + align entries
    manifest = {
        'stages': {
            'preprocess': {'complete': True, 'cache_hit': False, 'duration_s': 1.0, 'mtime': 1.0},
            'properties': {'complete': True, 'cache_hit': False, 'duration_s': 1.0, 'mtime': 1.0},
            'blocking':   {'complete': True, 'cache_hit': False, 'duration_s': 1.0, 'mtime': 1.0, 'nn_count': 30},
            'classify':   {'complete': True, 'cache_hit': False, 'duration_s': 1.0, 'mtime': 1.0},
            'align':      {'complete': True, 'cache_hit': False, 'duration_s': 1.0, 'mtime': 1.0},
        }
    }
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))

    pipeline_stages._invalidate_downstream_of_blocking(tmp_path)

    # scored_pairs + align outputs are gone
    assert not (tmp_path / 'scored_pairs.joblib').exists()
    assert not (tmp_path / 'alignment_info.json').exists()
    assert not (tmp_path / 'matches_by_cand.json').exists()

    # manifest no longer records classify or align as complete
    after = json.loads((tmp_path / 'manifest.json').read_text())
    assert 'classify' not in after['stages']
    assert 'align' not in after['stages']
    # Upstream stages preserved
    assert 'preprocess' in after['stages']
    assert 'blocking' in after['stages']
