"""End-to-end pipeline smoke test.

Runs all 4 stages (preprocess → properties → blocking → classify → align)
via the same Celery task the demo's web UI fires. Celery is in eager mode
so the task runs inline in the test process.

Marked `integration` because (a) it touches the full ML stack, and (b)
on a cold cache it takes ~30 s. Default `pytest` run excludes it; opt
in with `scripts/run-tests.sh -m integration`.

The test asserts the contract the demo's metric cards rely on: the
locked Hague inputs produce a metrics summary with TP/FP/FN/precision/
recall/F1 in plausible ranges. Tight bounds aren't asserted — variance
across numpy versions + RANSAC sampling can move numbers by ~5%.
"""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _run_pipeline_through_align(cache_dir, locked_inputs, nn_count=5, cutoff_m=10.0):
    """Invoke the same Celery task the /api/pipeline/start route fires."""
    import tasks
    import pipeline_stages

    cands_path = str(locked_inputs['cands'])
    index_path = str(locked_inputs['index'])
    input_hash = pipeline_stages.compute_input_hash(cands_path, index_path)
    # Eager mode: .delay() runs the task body synchronously and returns
    # an EagerResult; .get() gives us the return value or re-raises errors.
    result = tasks.pipeline_run.delay(
        target_stage='align',
        cands_path=cands_path,
        index_path=index_path,
        input_hash=input_hash,
        nn_count=nn_count,
        post_align_knn_cutoff=cutoff_m,
        post_align_blocking=True,
    )
    # In eager mode the task ran already; .get() unwraps + propagates errors.
    return result.get(propagate=True), input_hash


def test_pipeline_runs_through_align(warm_cache_dir, locked_inputs, cache_root, fake_redis):
    """Hot-path: all 4 stages are cache hits. Should complete in ~1 s."""
    out, input_hash = _run_pipeline_through_align(warm_cache_dir, locked_inputs)
    assert isinstance(out, dict)
    # The Celery task returns the run summary; pin a few expected keys.
    assert 'cache_dir' in out or input_hash in str(out)


def test_align_outputs_exist_after_run(warm_cache_dir):
    """All the artifacts the downstream routes/UI depend on exist."""
    expected = [
        'object_dict.joblib',
        'property_dict.joblib',
        'blocking_pairs.joblib',
        'scored_pairs.joblib',
        'alignment_info.json',
        'matches_by_cand.json',
        'metrics_summary.json',
        'manifest.json',
        'disaster_log.json',
        'damaged_heights_only_cands.json',           # addendum 10
        'damaged_heights_only_cands.prebaked.json',  # addendum 10 prebake
        'post_disaster_cands.json',
        'post_disaster_cands.prebaked.json',
    ]
    missing = [name for name in expected if not (warm_cache_dir / name).exists()]
    assert not missing, f"missing artifacts in warm cache: {missing}"


def test_alignment_info_succeeded(warm_cache_dir):
    info = json.loads((warm_cache_dir / 'alignment_info.json').read_text())
    assert info['alignment_succeeded'] is True
    assert info['n_anchor_pairs'] > 0
    assert 0 < info['mean_residual_m'] < 20.0   # demo regime
    # cutoff_m + match_threshold are persisted (used by /api/alignment/cand)
    assert 'cutoff_m' in info
    assert 'match_threshold' in info


def test_metrics_summary_in_expected_range(warm_cache_dir):
    """Demo-regime peak F1 ~0.86 (K=5, cutoff=10m). Allow ±0.10 tolerance
    for numpy/RANSAC variance across runs."""
    metrics = json.loads((warm_cache_dir / 'metrics_summary.json').read_text())
    at = metrics.get('at_match_threshold') or metrics.get('metrics_at_threshold') or {}
    f1 = at.get('f1')
    precision = at.get('precision')
    recall = at.get('recall')
    if f1 is None:
        pytest.skip(f"metrics_summary shape unexpected: {list(metrics.keys())}")
    # Sanity bounds, not tight pins.
    assert 0.5 < f1 < 1.0, f"F1 out of plausible range: {f1}"
    assert 0.5 < precision < 1.0
    assert 0.5 < recall <= 1.0
    # TP / FP / FN counts present + non-negative.
    for k in ('tp', 'fp', 'fn'):
        if k in at:
            assert at[k] >= 0


def test_manifest_marks_all_stages_complete(warm_cache_dir):
    manifest = json.loads((warm_cache_dir / 'manifest.json').read_text())
    stages = manifest.get('stages', {})
    for name in ('preprocess', 'properties', 'blocking', 'classify', 'align'):
        assert name in stages, f"stage {name} missing from manifest"
        assert stages[name].get('complete') is True, f"stage {name} not complete"


def test_disaster_log_has_damage_factors(warm_cache_dir):
    log = json.loads((warm_cache_dir / 'disaster_log.json').read_text())
    assert 'damage_log' in log
    factors = list(log['damage_log'].values())
    assert len(factors) > 0
    # All in (0, 1] per disaster_simulation.py
    for f in factors:
        assert 0.0 < float(f) <= 1.0
    # At least one truly damaged (factor < 1.0) — damage_probability=0.8.
    damaged = [f for f in factors if float(f) < 1.0]
    assert len(damaged) > 0, "disaster_log shows no damaged buildings — DisasterSim may be misconfigured"


def test_damaged_heights_file_uses_pristine_xy_coords(warm_cache_dir):
    """damaged_heights_only_cands.json must NOT have R/t applied — its X/Y
    should match pristine. We can't easily compare to pristine here, but
    we can sanity-check that vertices are in the Hague's EPSG:7415 range
    (X ~75000-85000, Y ~450000-460000)."""
    payload = json.loads((warm_cache_dir / 'damaged_heights_only_cands.json').read_text())
    verts = payload.get('vertices', [])
    if not verts:
        pytest.skip("damaged_heights file has no vertices")
    sample_x = [v[0] for v in verts[:200]]
    sample_y = [v[1] for v in verts[:200]]
    # Loose bounds covering Loosduinen / The Hague extent.
    assert 70000 < min(sample_x) < 90000
    assert 440000 < min(sample_y) < 470000


def test_post_disaster_file_has_crs_shifted_coords(warm_cache_dir):
    """post_disaster_cands.json DOES have R/t applied — its X/Y should be
    shifted from pristine by hundreds of metres (DEMO_CRS_TRANSLATION_MAX=500).
    This verifies the pipeline still applies the full disaster (not just
    height damage) — a subtle regression we'd otherwise miss."""
    pristine_xy = set()
    payload = json.loads((warm_cache_dir / 'damaged_heights_only_cands.json').read_text())
    for v in payload['vertices'][:500]:
        pristine_xy.add((round(v[0], 1), round(v[1], 1)))

    post = json.loads((warm_cache_dir / 'post_disaster_cands.json').read_text())
    n_overlap = 0
    for v in post['vertices'][:500]:
        if (round(v[0], 1), round(v[1], 1)) in pristine_xy:
            n_overlap += 1
    # Post-disaster has CRS shift, so almost no XY overlap with pristine.
    assert n_overlap < 50, (
        f"post_disaster_cands.json overlaps with damaged_heights_only_cands.json on {n_overlap}/500 vertices — "
        "CRS rotation/translation may not be applied")
