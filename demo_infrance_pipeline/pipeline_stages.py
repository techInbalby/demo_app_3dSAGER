"""
pipeline_stages.py — Cache-aware stage functions for the 3dSAGER inference pipeline.

Each stage:
  - Resolves its output filename(s) under `cache_dir`.
  - If the output already exists → loads & returns (cache HIT).
  - Otherwise → computes via `modules/*`, writes its output, records the run in
    `manifest.json`, returns.

The five stages mirror inference.py but are individually addressable for the
demo's online flow (Celery tasks pick a stage; routes chain prerequisites).
"""

# Apply path/flag overrides BEFORE any project module reads config.
import config_demo  # noqa: F401  side-effect: mutates config + extends sys.path

import glob
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from scipy.spatial import KDTree

import config
from preprocess_single import preprocess_files
from disaster_simulation import DisasterSimulator
from object_properties import ObjectPropertiesProcessor
from blocking import Blocker
from process_pairs import PairProcessor
from alignment import RigidAligner, write_cands_cityjson


CONFIG_VERSION = "v1"   # bump to invalidate all caches
STAGE_ORDER = ['preprocess', 'properties', 'blocking', 'classify', 'align']
DEFAULT_SEED = 1
# The reference run from the source repo uses 0.65 as the predicted_match
# cutoff for its ±100 km DisasterSim regime. The demo deliberately shrinks
# the translation to 500 m so post-disaster cands stay inside the WGS84
# projection's valid extent (otherwise viewer sub-stage 4a renders nowhere
# usable). In that smaller regime the look-alike pairs the classifier
# surfaces stay spatially closer to real index buildings after RANSAC,
# which compresses the score distribution. The actual demo-regime peak F1
# sits at threshold 0.40 (F1 ≈ 0.86, P ≈ 0.77, R ≈ 0.96 on the locked
# Hague inputs). 0.65 here would advertise an artificially weak F1 ≈ 0.77
# even though everything upstream is working as intended.
DEFAULT_MATCH_THRESHOLD = 0.40

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------- #
# Input-hash + cache helpers
# ---------------------------------------------------------------------------- #

def compute_input_hash(cands_path: str, index_path: str,
                       config_version: str = CONFIG_VERSION) -> str:
    """16-char SHA-256 prefix over input paths + sizes + mtimes + config version."""
    h = hashlib.sha256()
    for p in (cands_path, index_path):
        p_obj = Path(p).resolve()
        if not p_obj.exists():
            raise FileNotFoundError(p_obj)
        st = p_obj.stat()
        h.update(str(p_obj).encode())
        h.update(str(st.st_mtime_ns).encode())
        h.update(str(st.st_size).encode())
    h.update(config_version.encode())
    return h.hexdigest()[:16]


def ensure_cache_dir(cache_root: Path, input_hash: str) -> Path:
    cache_dir = Path(cache_root) / input_hash
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _atomic_write_json(payload, out_path: Path) -> None:
    out_path = Path(out_path)
    tmp = out_path.with_suffix(out_path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, default=_json_default)
    os.replace(tmp, out_path)


def _json_default(value):
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def read_manifest(cache_dir: Path) -> dict:
    p = Path(cache_dir) / "manifest.json"
    if not p.exists():
        return {"stages": {}}
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"stages": {}}


def _record_stage(cache_dir: Path, stage_name: str, duration_s: float,
                  cache_hit: bool, **extra) -> None:
    manifest = read_manifest(cache_dir)
    manifest.setdefault("stages", {})[stage_name] = {
        "complete": True,
        "duration_s": round(duration_s, 2),
        "cache_hit": cache_hit,
        "mtime": time.time(),
        **extra,
    }
    _atomic_write_json(manifest, Path(cache_dir) / "manifest.json")


def _find_saved_artifact(pattern_substr: str, prefer_large: bool = True) -> str:
    matches = sorted(glob.glob(os.path.join(
        config.FilePaths.saved_models_path, f'*{pattern_substr}*.joblib')))
    if not matches:
        raise FileNotFoundError(
            f"No saved artifact matching '*{pattern_substr}*' in "
            f"{config.FilePaths.saved_models_path}"
        )
    if prefer_large:
        large = [m for m in matches if '_large_' in m]
        if large:
            return large[0]
    return matches[0]


# ---------------------------------------------------------------------------- #
# Stages
# ---------------------------------------------------------------------------- #

def stage_preprocess(cache_dir: Path, cands_path: str, index_path: str,
                     seed: int = DEFAULT_SEED, apply_disaster: bool = True,
                     filter_shared_ids: bool = False, progress_cb=None) -> dict:
    """
    Preprocess two raw CityJSON files into an `object_dict`, optionally applying
    DisasterSimulator to cands. Writes:
      - object_dict.joblib        (post-disaster if apply_disaster)
      - disaster_log.json         (R_crs, t_crs, damage_log — only if disaster applied)
    """
    out_path = Path(cache_dir) / "object_dict.joblib"
    if out_path.exists():
        _record_stage(cache_dir, 'preprocess', 0.0, cache_hit=True)
        return joblib.load(out_path)

    t0 = time.time()
    if progress_cb: progress_cb('preprocess', 'reading CityJSON files')

    # preprocess_files writes its own cache; point it at the same path we want to
    # end up at, then we overwrite with the post-disaster version below.
    object_dict = preprocess_files(
        cands_path, index_path, str(out_path),
        require_shared_ids=filter_shared_ids,
    )

    if apply_disaster:
        if progress_cb: progress_cb('preprocess', 'applying DisasterSimulator')
        simulator = DisasterSimulator(config.DisasterSimulation, seed=seed)
        object_dict = simulator.apply(object_dict)
        # Persist the ground-truth transform + damage log so stage_align can
        # later log alignment error vs. ground truth.
        disaster_log = {
            "R_crs": simulator.R_crs.tolist() if simulator.R_crs is not None else None,
            "t_crs": simulator.t_crs.tolist() if simulator.t_crs is not None else None,
            "damage_log": {str(k): float(v) for k, v in simulator.damage_log.items()},
        }
        _atomic_write_json(disaster_log, Path(cache_dir) / "disaster_log.json")
        # Re-save the (now-mutated) object_dict.
        joblib.dump(object_dict, out_path, compress=3)

    _record_stage(cache_dir, 'preprocess', time.time() - t0, cache_hit=False,
                  n_cands=len(object_dict.get('cands', {})),
                  n_index=len(object_dict.get('index', {})),
                  disaster_applied=apply_disaster)
    return object_dict


def stage_properties(cache_dir: Path, progress_cb=None) -> dict:
    """Compute the 25 geometric features per building (both sides). Writes property_dict.joblib."""
    out_path = Path(cache_dir) / "property_dict.joblib"
    if out_path.exists():
        _record_stage(cache_dir, 'properties', 0.0, cache_hit=True)
        return joblib.load(out_path)

    obj_path = Path(cache_dir) / "object_dict.joblib"
    if not obj_path.exists():
        raise FileNotFoundError(f"stage_properties: prerequisite missing ({obj_path})")
    object_dict = joblib.load(obj_path)

    t0 = time.time()
    if progress_cb: progress_cb('properties', 'computing geometric features')
    proc = ObjectPropertiesProcessor(object_dict, vector_normalization=True)
    property_dict = proc.prop_vals_dict
    joblib.dump(property_dict, out_path, compress=3)

    _record_stage(cache_dir, 'properties', time.time() - t0, cache_hit=False,
                  n_features=len(property_dict))
    return property_dict


def stage_blocking(cache_dir: Path, progress_cb=None) -> List[Tuple[str, str]]:
    """Run BKAFI blocking. Writes blocking_pairs.joblib (list of (cand_id, index_id))."""
    out_path = Path(cache_dir) / "blocking_pairs.joblib"
    if out_path.exists():
        _record_stage(cache_dir, 'blocking', 0.0, cache_hit=True)
        return joblib.load(out_path)

    object_dict = joblib.load(Path(cache_dir) / "object_dict.joblib")
    property_dict = joblib.load(Path(cache_dir) / "property_dict.joblib")

    fi_path = _find_saved_artifact('feature_importance_dict')
    pr_path = _find_saved_artifact('property_ratios')
    feature_importance_dict = joblib.load(fi_path)
    property_ratios = joblib.load(pr_path)

    t0 = time.time()
    if progress_cb: progress_cb('blocking', 'running BKAFI KDTree NN search')
    blocker = Blocker(
        dataset_name=config.Constants.dataset_name,
        object_dict={'cands': object_dict['cands'], 'index': object_dict['index']},
        property_dict=property_dict,
        feature_importance_scores=feature_importance_dict,
        property_ratios=property_ratios,
        blocking_method='bkafi',
        sdr_factor=False,
        bkafi_criterion='feature_importance',
        train_or_test='test',
    )
    pos_dim = next(iter(blocker.pos_pairs_dict))
    pos_cpi = next(iter(blocker.pos_pairs_dict[pos_dim]))
    pos_pairs = blocker.pos_pairs_dict[pos_dim][pos_cpi]
    neg_pairs = blocker.neg_pairs_dict[pos_dim][pos_cpi]
    all_pairs = pos_pairs + neg_pairs
    joblib.dump(all_pairs, out_path, compress=3)

    _record_stage(cache_dir, 'blocking', time.time() - t0, cache_hit=False,
                  n_pairs=len(all_pairs), n_pos=len(pos_pairs))
    return all_pairs


def stage_classify(cache_dir: Path, progress_cb=None) -> List[Tuple[str, str, float]]:
    """Score each pair with the saved XGBClassifier. Writes scored_pairs.joblib."""
    out_path = Path(cache_dir) / "scored_pairs.joblib"
    if out_path.exists():
        _record_stage(cache_dir, 'classify', 0.0, cache_hit=True)
        return joblib.load(out_path)

    property_dict = joblib.load(Path(cache_dir) / "property_dict.joblib")
    all_pairs = joblib.load(Path(cache_dir) / "blocking_pairs.joblib")

    t0 = time.time()
    if progress_cb: progress_cb('classify', 'building feature matrix')
    any_attr = next(iter(property_dict))
    avail_cands = set(property_dict[any_attr]['cands'].keys())
    avail_index = set(property_dict[any_attr]['index'].keys())
    pairs = [p for p in all_pairs if p[0] in avail_cands and p[1] in avail_index]
    proc = PairProcessor(property_dict, pairs)
    X = np.asarray(proc.feature_vec, dtype=np.float64)

    if progress_cb: progress_cb('classify', 'loading XGBClassifier and predicting')
    xgb_path = _find_saved_artifact('XGBClassifier_matching')
    model_pkg = joblib.load(xgb_path)
    model = model_pkg['model']
    saved_features = model_pkg.get('feature_name_list')
    if saved_features is not None and list(saved_features) != list(proc.feature_name_list):
        raise RuntimeError(
            f"Feature ordering mismatch between saved model and current build.\n"
            f"  saved (first 3): {list(saved_features)[:3]}\n"
            f"  built (first 3): {list(proc.feature_name_list)[:3]}"
        )
    proba = model.predict_proba(X)
    match_idx = list(model.classes_).index(1)
    geo_scores = proba[:, match_idx]
    scored_pairs = [(c, i, float(s)) for (c, i), s in zip(pairs, geo_scores)]
    joblib.dump(scored_pairs, out_path, compress=3)

    _record_stage(cache_dir, 'classify', time.time() - t0, cache_hit=False,
                  n_pairs=len(scored_pairs))
    return scored_pairs


def stage_align(cache_dir: Path, seed: int = DEFAULT_SEED,
                match_threshold: float = DEFAULT_MATCH_THRESHOLD,
                post_align_blocking: bool = False,
                progress_cb=None) -> dict:
    """
    RANSAC rigid alignment + re-score + write all Step-4 artifacts.

    Writes (all under cache_dir):
      - post_disaster_cands.json           CityJSON snapshot pre-alignment, post-disaster
      - aligned_candidates_seed{N}.json    CityJSON post-alignment
      - anchor_pairs.json                  high-confidence anchors fed into RANSAC
      - matches.csv / matches.parquet      per-pair table
      - matches_by_cand.json               same table grouped by cand_id, demo-app schema
      - metrics_summary.json               P/R/F1 at threshold + PR sweep
      - alignment_info.json                aligner summary (residual, anchor count, alpha)

    If `post_align_blocking` is True and the aligner succeeds, the BKAFI
    candidate pool is replaced by per-cand 1-NN against the full index in
    post-alignment coordinates (see `post_align_knn_block`). Lifts blocking
    recall from ~47%% (BKAFI ceiling on the demo) to ~100%%.
    """
    aligned_path = Path(cache_dir) / f"aligned_candidates_seed{seed}.json"
    info_path = Path(cache_dir) / "alignment_info.json"
    # Cache hit only if the cached run matches the requested mode (otherwise the
    # metrics + matches.csv on disk are from a different scoring regime).
    if aligned_path.exists() and info_path.exists():
        try:
            with open(info_path) as f:
                cached_info = json.load(f)
            if bool(cached_info.get('post_align_blocking', False)) == bool(post_align_blocking):
                _record_stage(cache_dir, 'align', 0.0, cache_hit=True)
                return cached_info
        except (json.JSONDecodeError, OSError):
            pass  # fall through to recompute

    object_dict = joblib.load(Path(cache_dir) / "object_dict.joblib")
    scored_pairs = joblib.load(Path(cache_dir) / "scored_pairs.joblib")

    t0 = time.time()

    # --- 1. Serialise post-disaster (pre-alignment) cand geometry ---
    if progress_cb: progress_cb('align', 'writing post-disaster snapshot')
    output_crs = config.Alignment.output_crs
    write_cands_cityjson(
        object_dict['cands'],
        out_path=Path(cache_dir) / "post_disaster_cands.json",
        output_crs=output_crs,
        alignment_info=None,
    )

    # --- 2. Run RigidAligner ---
    # Snapshot pre-alignment cand centroids BEFORE the aligner mutates them in
    # place via _apply_transform_to_geometry — required for post-align-blocking
    # mode (1-NN against the index in post-alignment coordinates).
    pre_alignment_cand_centroids = None
    if post_align_blocking:
        pre_alignment_cand_centroids = {
            bid: np.asarray(data['centroid'], dtype=np.float64).copy()
            for bid, data in object_dict['cands'].items()
        }
    # RigidAligner._write_cityjson writes to config.FilePaths.results_path. We
    # redirect it here so the aligned CityJSON lands in cache_dir.
    prev_results_path = config.FilePaths.results_path
    config.FilePaths.results_path = str(cache_dir) + os.sep
    try:
        if progress_cb: progress_cb('align', 'estimating rigid transform (RANSAC)')
        gt_R, gt_t = _load_ground_truth(cache_dir)
        aligner = RigidAligner(config.Alignment)
        rescored_pairs = aligner.run(
            object_dict, scored_pairs,
            suffix=f'seed{seed}',
            ground_truth_R=gt_R, ground_truth_t=gt_t,
        )
    finally:
        config.FilePaths.results_path = prev_results_path

    # --- 2b. Optional: replace BKAFI candidate pool with post-alignment 1-NN ---
    # Lifts blocking recall from ~47%% (BKAFI ceiling on demo data) to ~100%%
    # by querying the full index spatially after RANSAC instead of relying on
    # BKAFI's geometric-feature shortlist.
    n_gt_override = None
    if post_align_blocking and aligner.alignment_succeeded:
        if progress_cb: progress_cb('align', 'post-alignment KNN re-blocking')
        cutoff_m = float(config.Alignment.post_align_knn_cutoff)
        scored_pairs, rescored_pairs = post_align_knn_block(
            object_dict, aligner, pre_alignment_cand_centroids, cutoff_m=cutoff_m,
        )
        # Fair denominator for metrics: every cand whose true counterpart exists
        # in the index is in principle recoverable post-alignment, regardless of
        # whether BKAFI ever surfaced it.
        n_gt_override = len(set(object_dict['cands'].keys()) & set(object_dict['index'].keys()))
        logger.info(f"[stage_align] post-align-blocking on; shared-population denominator = {n_gt_override}")
    elif post_align_blocking and not aligner.alignment_succeeded:
        logger.warning("[stage_align] post-align-blocking requested but alignment was "
                       "rejected; falling back to BKAFI pool + geometric scores.")

    # --- 3. Dump anchor pairs (the pool RANSAC was given) ---
    if progress_cb: progress_cb('align', 'writing anchor pairs')
    confidence_threshold = config.Alignment.confidence_threshold
    cands_keys = set(object_dict['cands'].keys())
    index_keys = set(object_dict['index'].keys())
    anchors = [
        {'cand_id': c, 'index_id': i, 'geometric_score': round(s, 4)}
        for c, i, s in scored_pairs
        if s >= confidence_threshold and c in cands_keys and i in index_keys
    ]
    _atomic_write_json({
        'confidence_threshold': confidence_threshold,
        'n_anchors': len(anchors),
        'anchors': anchors,
    }, Path(cache_dir) / "anchor_pairs.json")

    # --- 4. matches.csv + matches.parquet + matches_by_cand.json + metrics ---
    if progress_cb: progress_cb('align', 'saving matches and metrics')
    df, metrics = _build_matches_tables(scored_pairs, rescored_pairs, match_threshold,
                                        n_gt_override=n_gt_override)
    df.to_csv(Path(cache_dir) / "matches.csv", index=False)
    try:
        df.to_parquet(Path(cache_dir) / "matches.parquet", index=False)
    except (ImportError, Exception) as e:   # pyarrow may be missing in worker
        logger.info(f"[stage_align] parquet skipped: {e}")

    matches_by_cand = _group_matches_by_cand(df, cands_filename=f"aligned_candidates_seed{seed}.json")
    _atomic_write_json(matches_by_cand, Path(cache_dir) / "matches_by_cand.json")

    if metrics is not None:
        _atomic_write_json(metrics, Path(cache_dir) / "metrics_summary.json")

    # --- 5. Save alignment_info summary ---
    info = {
        'alignment_succeeded': bool(aligner.alignment_succeeded),
        'mean_residual_m': float(aligner.mean_residual) if aligner.mean_residual is not None else None,
        'n_anchor_pairs': int(aligner.n_anchors),
        'alpha': float(aligner.alpha),
        'confidence_threshold': float(confidence_threshold),
        'match_threshold': float(match_threshold),
        'seed': int(seed),
        'post_align_blocking': bool(post_align_blocking),
    }
    _atomic_write_json(info, info_path)

    _record_stage(cache_dir, 'align', time.time() - t0, cache_hit=False,
                  alignment_succeeded=info['alignment_succeeded'])
    return info


# ---------------------------------------------------------------------------- #
# Orchestration helpers
# ---------------------------------------------------------------------------- #

STAGE_FUNCS: Dict[str, Callable] = {
    'preprocess': stage_preprocess,
    'properties': stage_properties,
    'blocking':   stage_blocking,
    'classify':   stage_classify,
    'align':      stage_align,
}


def run_through(target_stage: str, cache_dir: Path, *,
                cands_path: str, index_path: str,
                seed: int = DEFAULT_SEED,
                match_threshold: float = DEFAULT_MATCH_THRESHOLD,
                apply_disaster: bool = True,
                filter_shared_ids: bool = False,
                post_align_blocking: bool = False,
                progress_cb=None) -> dict:
    """
    Run every stage up to and including `target_stage`, in order. Each stage is
    individually cache-aware, so unnecessary work is skipped.

    Returns a dict summarising the run.
    """
    if target_stage not in STAGE_FUNCS:
        raise ValueError(f"Unknown stage '{target_stage}'. Valid: {STAGE_ORDER}")
    idx = STAGE_ORDER.index(target_stage)

    summary = {}
    for s in STAGE_ORDER[:idx + 1]:
        if s == 'preprocess':
            stage_preprocess(cache_dir, cands_path=cands_path, index_path=index_path,
                             seed=seed, apply_disaster=apply_disaster,
                             filter_shared_ids=filter_shared_ids, progress_cb=progress_cb)
        elif s == 'align':
            summary = stage_align(cache_dir, seed=seed, match_threshold=match_threshold,
                                  post_align_blocking=post_align_blocking,
                                  progress_cb=progress_cb)
        else:
            STAGE_FUNCS[s](cache_dir, progress_cb=progress_cb)

    summary['target_stage'] = target_stage
    summary['cache_dir'] = str(cache_dir)
    return summary


# ---------------------------------------------------------------------------- #
# Internal helpers
# ---------------------------------------------------------------------------- #

def post_align_knn_block(object_dict, aligner, pre_alignment_cand_centroids, cutoff_m):
    """
    Replace the BKAFI candidate pool with per-cand 1-NN against the full index
    in post-alignment coordinates. Score is a linear taper: 1 at d=0, 0 at
    d=cutoff_m, negative beyond.

    Snapshot of pre-alignment cand centroids must be passed in — RigidAligner.run
    mutates object_dict['cands'] centroids in place via _apply_transform_to_geometry.

    Returns
    -------
    new_scored : list of (cand_id, index_id, geometric_score)
        geometric_score is NaN because these pairs were never run through the
        classifier.
    new_rescored : list of (cand_id, index_id, final_score)
        final_score = 1 - d / cutoff_m  (not clamped — d > cutoff yields a
        negative score, so --match-threshold 0 accepts exactly d ≤ cutoff).
    """
    idx_ids = list(object_dict['index'].keys())
    idx_pts = np.asarray(
        [np.asarray(object_dict['index'][bid]['centroid'], dtype=np.float64) for bid in idx_ids],
        dtype=np.float64,
    )
    tree = KDTree(idx_pts)

    new_scored, new_rescored = [], []
    within_cutoff = 0
    for cid, qc in pre_alignment_cand_centroids.items():
        aligned = aligner.R @ qc + aligner.t
        d_arr, i_arr = tree.query(aligned.reshape(1, -1), k=1)
        d = float(d_arr[0])
        ii = int(i_arr[0])
        # Linear taper, NOT clamped: score = 1 at d=0, score = 0 at d=cutoff,
        # negative beyond. `--match-threshold 0` then accepts exactly d ≤ cutoff,
        # higher thresholds carve out tighter distance bands inside the cutoff.
        score = 1.0 - d / cutoff_m
        nearest_id = idx_ids[ii]
        new_scored.append((cid, nearest_id, float('nan')))
        new_rescored.append((cid, nearest_id, score))
        if d <= cutoff_m:
            within_cutoff += 1
    logger.info(f"[post_align_knn_block] {len(new_rescored)} cand→1-NN pairs, "
                f"{within_cutoff}/{len(new_rescored)} within {cutoff_m:.1f} m cutoff")
    return new_scored, new_rescored


def _load_ground_truth(cache_dir: Path):
    p = Path(cache_dir) / "disaster_log.json"
    if not p.exists():
        return None, None
    with open(p) as f:
        dl = json.load(f)
    R = np.array(dl["R_crs"]) if dl.get("R_crs") else None
    t = np.array(dl["t_crs"]) if dl.get("t_crs") else None
    return R, t


def _build_matches_tables(scored_pairs, rescored_pairs, match_threshold,
                          n_gt_override=None):
    """
    n_gt_override: if provided, overrides the recall denominator. Default is the
    count of same-ID pairs in `scored_pairs` (in-pool population). With
    post-align-blocking, callers pass the count of shared BAG IDs (the full
    recoverable population) so recall is measured fairly against everything the
    alignment could in principle find.
    """
    rescored_by_pair = {(c, i): s for c, i, s in rescored_pairs}
    rows = []
    for cid, iid, geo_score in scored_pairs:
        final = rescored_by_pair.get((cid, iid), geo_score)
        # NaN-safe: in post-align-blocking mode geometric_score is NaN
        # because these pairs were never scored by the classifier.
        geo_out = round(geo_score, 4) if (geo_score == geo_score) else float('nan')
        rows.append({
            'cand_id': cid,
            'index_id': iid,
            'geometric_score': geo_out,
            'final_score': round(final, 4),
            'predicted_match': int(final >= match_threshold),
            'same_id': int(cid == iid),
        })
    df = pd.DataFrame(rows).sort_values('final_score', ascending=False)
    n_pos = int(df['same_id'].sum()) if n_gt_override is None else int(n_gt_override)
    metrics = None
    if n_pos > 0:
        def pr_at(t):
            h = df[df['final_score'] >= t]
            tp = int(h['same_id'].sum())
            fp = int(len(h) - tp)
            fn = int(n_pos - tp)
            p = tp / max(tp + fp, 1)
            r = tp / max(tp + fn, 1)
            f1 = (2 * p * r) / max(p + r, 1e-9)
            return {'threshold': t, 'tp': tp, 'fp': fp, 'fn': fn,
                    'precision': round(p, 4), 'recall': round(r, 4), 'f1': round(f1, 4)}
        primary = pr_at(match_threshold)
        sweep = [pr_at(t) for t in [0.10, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70]]
        best = max(sweep, key=lambda d: d['f1'])
        metrics = {
            'n_ground_truth_positive': n_pos,
            'at_match_threshold': primary,
            'best_f1_in_sweep': best,
            'pr_sweep': sweep,
        }
    return df, metrics


def _group_matches_by_cand(df: pd.DataFrame, cands_filename: str) -> dict:
    """Group rows by cand_id in the schema the demo's frontend expects."""
    grouped = {}
    for cand_id, sub in df.groupby('cand_id'):
        grouped[str(cand_id)] = {'possible_matches': [
            {
                'index_id': str(r.index_id),
                'geometric_score': float(r.geometric_score),
                'final_score': float(r.final_score),
                'predicted_label': int(r.predicted_match),
                'true_label': int(r.same_id),
                # Keep `confidence` for backwards-compat with the existing demo's
                # match-display code paths.
                'confidence': float(r.final_score),
            }
            for r in sub.itertuples(index=False)
        ]}
    return {cands_filename: grouped}
