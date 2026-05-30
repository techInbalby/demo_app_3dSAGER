"""
inference.py — End-to-end 3dSAGER inference pipeline.

Runs:
  1. Preprocess two raw CityJSON files -> cands & index object dicts
  2. DisasterSimulator on cands (optional, default on)
  3. ObjectPropertiesProcessor on both sides -> 25 geometric features each
  4. BKAFI blocking using saved feature_importance + property_ratios -> candidate pairs
  5. PairProcessor -> per-pair feature vectors
  6. XGBClassifier from saved model -> score each pair
  7. RigidAligner (RANSAC) -> re-score and write aligned CityJSON
  8. Save matches.csv, matches.parquet, optional metrics_summary.json
"""

# Apply path/flag overrides BEFORE any project modules read config.
import config_demo  # noqa: F401  side-effect: mutates config + extends sys.path

import argparse
import glob
import json
import logging
import os

import joblib
import numpy as np
import pandas as pd

import config
from preprocess_single import preprocess_files
from disaster_simulation import DisasterSimulator
from object_properties import ObjectPropertiesProcessor
from blocking import Blocker
from process_pairs import PairProcessor
from alignment import RigidAligner


DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CANDS = os.path.join(DEMO_DIR, 'input_data', '10-248-580.city.json')
DEFAULT_INDEX = os.path.join(DEMO_DIR, 'input_data', 'TheHague3D_Batch_07_Loosduinen_2022-08-08.json')
INTERMEDIATE_DIR = config_demo.INTERMEDIATE_DIR


def parse_args():
    p = argparse.ArgumentParser(description="3dSAGER demo inference (raw CityJSON -> matches)")
    p.add_argument('--cands', default=DEFAULT_CANDS, help='CityJSON file used as candidate (Source B).')
    p.add_argument('--index', default=DEFAULT_INDEX, help='CityJSON file used as index (Source A).')
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--no-disaster', action='store_true', help='Skip DisasterSimulator.')
    p.add_argument('--use-cache', action='store_true', help='Reuse output/cache/*.joblib if present.')
    p.add_argument('--filter-shared-ids', action='store_true', help='Keep only cands whose ID is in index.')
    p.add_argument('--match-threshold', type=float, default=0.65,
                   help='Score threshold for predicted_match in matches.csv (default 0.65 — empirical '
                        'best on demo data with Gaussian spatial score σ=3 m and α=0.3; raise for '
                        'higher precision, lower for higher recall).')
    return p.parse_args()


def find_saved_artifact(pattern_substr, prefer_large=True):
    """Locate a saved joblib under saved_models/ matching the substring."""
    matches = sorted(glob.glob(os.path.join(config.FilePaths.saved_models_path,
                                            f'*{pattern_substr}*.joblib')))
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


def build_property_dict(object_dict, vector_normalization=True):
    print("[3/7] Computing geometric properties (multiprocessed)...")
    proc = ObjectPropertiesProcessor(object_dict, vector_normalization=vector_normalization)
    return proc.prop_vals_dict


def run_blocking(object_dict, property_dict, feature_importance_dict, property_ratios):
    """BKAFI blocking. Returns flat list of (cand_id, index_id) candidate pairs."""
    print("[4/7] Running BKAFI blocking (KDTree NN)...")
    # Blocker only needs the cands/index sub-dicts of object_dict, but accepts the full thing.
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
    # config_demo restricts both dim_list and cand_pairs_per_item_list to a single value.
    pos_dim = next(iter(blocker.pos_pairs_dict))
    pos_cpi = next(iter(blocker.pos_pairs_dict[pos_dim]))
    pos_pairs = blocker.pos_pairs_dict[pos_dim][pos_cpi]
    neg_pairs = blocker.neg_pairs_dict[pos_dim][pos_cpi]
    all_pairs = pos_pairs + neg_pairs
    print(f"  -> {len(all_pairs)} candidate pairs "
          f"({len(pos_pairs)} same-ID, {len(neg_pairs)} different-ID); "
          f"bkafi_dim={pos_dim}, neighbors_per_cand={pos_cpi}")
    return all_pairs


def build_feature_matrix(property_dict, pairs):
    """Run PairProcessor; drop pairs whose IDs aren't in property_dict."""
    print("[5/7] Building per-pair feature vectors...")
    any_attr = next(iter(property_dict))
    avail_cands = set(property_dict[any_attr]['cands'].keys())
    avail_index = set(property_dict[any_attr]['index'].keys())
    before = len(pairs)
    pairs = [p for p in pairs if p[0] in avail_cands and p[1] in avail_index]
    dropped = before - len(pairs)
    if dropped:
        print(f"  dropped {dropped}/{before} pairs whose IDs are not in property_dict")
    proc = PairProcessor(property_dict, pairs)
    X = np.asarray(proc.feature_vec, dtype=np.float64)
    return X, pairs, proc.feature_name_list


def classify(model_pkg, X, expected_feature_order):
    print("[6/7] Classifying pairs with XGBClassifier...")
    model = model_pkg['model']
    saved_features = model_pkg.get('feature_name_list')
    if saved_features is not None and list(saved_features) != list(expected_feature_order):
        raise RuntimeError(
            "Feature ordering mismatch between saved model and current build.\n"
            f"  saved (first 3): {list(saved_features)[:3]}\n"
            f"  built (first 3): {list(expected_feature_order)[:3]}"
        )
    proba = model.predict_proba(X)
    match_idx = list(model.classes_).index(1)
    return proba[:, match_idx]


def align(object_dict, scored_pairs, simulator, seed):
    print("[7/7] Running RANSAC rigid alignment...")
    aligner = RigidAligner(config.Alignment)
    gt_R = getattr(simulator, 'R_crs', None) if simulator is not None else None
    gt_t = getattr(simulator, 't_crs', None) if simulator is not None else None
    return aligner.run(object_dict, scored_pairs,
                       suffix=f'seed{seed}', ground_truth_R=gt_R, ground_truth_t=gt_t)


def save_results(scored_pairs, rescored_pairs, output_dir, match_threshold):
    rescored_by_pair = {(c, i): s for c, i, s in rescored_pairs}
    rows = []
    for cid, iid, geo_score in scored_pairs:
        final = rescored_by_pair.get((cid, iid), geo_score)
        rows.append({
            'cand_id': cid,
            'index_id': iid,
            'geometric_score': round(geo_score, 4),
            'final_score': round(final, 4),
            'predicted_match': int(final >= match_threshold),
            'same_id': int(cid == iid),
        })
    df = pd.DataFrame(rows).sort_values('final_score', ascending=False)
    csv_path = os.path.join(output_dir, 'matches.csv')
    parquet_path = os.path.join(output_dir, 'matches.parquet')
    df.to_csv(csv_path, index=False)
    print(f"  wrote {csv_path} ({len(df)} rows)")
    try:
        df.to_parquet(parquet_path, index=False)
        print(f"  wrote {parquet_path}")
    except ImportError as e:
        print(f"  parquet skipped ({e}); install pyarrow to enable")
    return df


def maybe_metrics(df, output_dir, match_threshold):
    """If any same-ID pairs exist, treat them as ground truth and write P/R/F1
    at the configured threshold plus a small precision-recall sweep so the user
    can pick a different operating point post-hoc."""
    n_pos = int(df['same_id'].sum())
    if n_pos == 0:
        print("  no same-ID pairs in blocking output -> skipping metrics_summary.json")
        return

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

    out = {
        'n_ground_truth_positive': n_pos,
        'at_match_threshold': primary,
        'best_f1_in_sweep':    best,
        'pr_sweep':            sweep,
    }
    path = os.path.join(output_dir, 'metrics_summary.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"  wrote {path}")
    print(f"    primary (thresh={match_threshold}): {primary}")
    print(f"    best F1 in sweep:                  {best}")


def main():
    args = parse_args()
    np.random.seed(args.seed)
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    print("=" * 72)
    print(f"3dSAGER demo inference  |  seed={args.seed}  |  "
          f"disaster={'no' if args.no_disaster else 'yes'}")
    print(f"  cands: {args.cands}")
    print(f"  index: {args.index}")
    print("=" * 72)

    # 1. Preprocess
    objects_cache = os.path.join(config.FilePaths.object_dict_path, 'objects.joblib')
    if args.use_cache and os.path.exists(objects_cache):
        print(f"[1/7] Loading cached object_dict: {objects_cache}")
        object_dict = joblib.load(objects_cache)
    else:
        print("[1/7] Preprocessing raw CityJSON...")
        object_dict = preprocess_files(
            args.cands, args.index, objects_cache,
            require_shared_ids=args.filter_shared_ids,
        )

    # 2. DisasterSimulator (on cands only)
    simulator = None
    if not args.no_disaster:
        print("[2/7] Applying DisasterSimulator to cands...")
        simulator = DisasterSimulator(config.DisasterSimulation, seed=args.seed)
        object_dict = simulator.apply(object_dict)
    else:
        print("[2/7] Skipping DisasterSimulator (--no-disaster).")

    # 3. Properties
    prop_cache = os.path.join(config.FilePaths.property_dict_path, 'property_dict.joblib')
    if args.use_cache and os.path.exists(prop_cache):
        print(f"[3/7] Loading cached property_dict: {prop_cache}")
        property_dict = joblib.load(prop_cache)
    else:
        property_dict = build_property_dict(object_dict)
        joblib.dump(property_dict, prop_cache, compress=3)
        print(f"  cached property_dict to {prop_cache}")

    # 4. Blocking
    fi_path = find_saved_artifact('feature_importance_dict')
    pr_path = find_saved_artifact('property_ratios')
    print(f"  BKAFI artifacts:\n    {os.path.basename(fi_path)}\n    {os.path.basename(pr_path)}")
    feature_importance_dict = joblib.load(fi_path)
    property_ratios = joblib.load(pr_path)
    all_pairs = run_blocking(object_dict, property_dict, feature_importance_dict, property_ratios)
    joblib.dump(all_pairs, os.path.join(INTERMEDIATE_DIR, 'blocking_pairs.joblib'))

    # 5. Feature vectors
    X, pairs, feature_names = build_feature_matrix(property_dict, all_pairs)

    # 6. Classify
    xgb_path = find_saved_artifact('XGBClassifier_matching')
    print(f"  loading model: {os.path.basename(xgb_path)}")
    model_pkg = joblib.load(xgb_path)
    geo_scores = classify(model_pkg, X, feature_names)
    scored_pairs = [(c, i, float(s)) for (c, i), s in zip(pairs, geo_scores)]
    joblib.dump(scored_pairs, os.path.join(INTERMEDIATE_DIR, 'scored_pairs.joblib'))

    # 7. Alignment (writes aligned_candidates_seedN.json under results_path)
    rescored_pairs = align(object_dict, scored_pairs, simulator, args.seed)

    # 8. Save final outputs
    results_dir = config.FilePaths.results_path
    df = save_results(scored_pairs, rescored_pairs, results_dir, args.match_threshold)
    maybe_metrics(df, results_dir, args.match_threshold)

    print("\nDONE.")


if __name__ == '__main__':
    main()
