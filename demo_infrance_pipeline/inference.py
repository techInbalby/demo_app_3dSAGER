"""
inference.py — End-to-end 3dSAGER inference pipeline (CLI wrapper).

Thin orchestrator around `pipeline_stages.run_through(...)`. All compute logic
lives in pipeline_stages.py + modules/. Outputs go to
`<cache_root>/<input_hash>/` so the demo's online flow and the CLI share the
same cache layout.

By default `--cache-root` is the standalone bundle's own output/cache; when the
demo app's Celery worker invokes the pipeline it points cache-root at
`results_demo/cache/` under the app repo root.
"""

# Apply path/flag overrides BEFORE any project modules read config.
import config_demo  # noqa: F401  side-effect: mutates config + extends sys.path

import argparse
import logging
import os

import numpy as np

from pipeline_stages import (
    compute_input_hash,
    ensure_cache_dir,
    run_through,
    DEFAULT_MATCH_THRESHOLD,
)


DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CANDS = os.path.join(DEMO_DIR, 'input_data', '10-248-580.city.json')
DEFAULT_INDEX = os.path.join(DEMO_DIR, 'input_data', 'TheHague3D_Batch_07_Loosduinen_2022-08-08.json')
DEFAULT_CACHE_ROOT = os.path.join(DEMO_DIR, 'output', 'cache_runs')


def parse_args():
    p = argparse.ArgumentParser(description="3dSAGER demo inference (raw CityJSON -> matches)")
    p.add_argument('--cands', default=DEFAULT_CANDS, help='CityJSON file used as candidate (Source A).')
    p.add_argument('--index', default=DEFAULT_INDEX, help='CityJSON file used as index (Source B).')
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--no-disaster', action='store_true', help='Skip DisasterSimulator.')
    p.add_argument('--filter-shared-ids', action='store_true', help='Keep only cands whose ID is in index.')
    p.add_argument('--post-align-blocking', action='store_true',
                   help='After RANSAC alignment is accepted, replace the BKAFI candidate pool with '
                        'per-cand 1-NN against the full index in post-alignment coordinates. Lifts '
                        'blocking recall from ~47%% (BKAFI) to ~100%% on the demo. Accept distance '
                        'cutoff comes from config.Alignment.post_align_knn_cutoff (default 7 m).')
    p.add_argument('--match-threshold', type=float, default=None,
                   help=f'Score threshold for predicted_match in matches.csv. Default '
                        f'{DEFAULT_MATCH_THRESHOLD} in hybrid mode (Gaussian spatial σ=3 m + α=0.3) '
                        f'or 0.0 in --post-align-blocking mode (linear-taper score, threshold 0 '
                        f'accepts every nearest within the cutoff).')
    p.add_argument('--cache-root', default=DEFAULT_CACHE_ROOT,
                   help='Root directory for per-input-hash cache dirs.')
    p.add_argument('--stage', default='align', choices=['preprocess', 'properties',
                                                         'blocking', 'classify', 'align'],
                   help='Run all stages up to and including this one. Default: full pipeline.')
    return p.parse_args()


def _print_progress(stage, message):
    print(f"[{stage}] {message}")


def main():
    args = parse_args()
    np.random.seed(args.seed)
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    # match-threshold default depends on mode: DEFAULT_MATCH_THRESHOLD for hybrid
    # (Gaussian + α blend), 0.0 for post-align-blocking (linear-taper score in
    # [0,1]; threshold 0 accepts every cand whose nearest index is within the
    # distance cutoff).
    if args.match_threshold is None:
        args.match_threshold = 0.0 if args.post_align_blocking else DEFAULT_MATCH_THRESHOLD

    input_hash = compute_input_hash(args.cands, args.index)
    cache_dir = ensure_cache_dir(args.cache_root, input_hash)

    mode = 'post-align-knn' if args.post_align_blocking else 'hybrid'
    print("=" * 72)
    print(f"3dSAGER inference  |  seed={args.seed}  |  disaster={'no' if args.no_disaster else 'yes'}  |  mode={mode}")
    print(f"  cands     : {args.cands}")
    print(f"  index     : {args.index}")
    print(f"  cache_dir : {cache_dir}")
    print(f"  stage     : up to '{args.stage}'")
    print(f"  threshold : {args.match_threshold}")
    print("=" * 72)

    summary = run_through(
        args.stage,
        cache_dir,
        cands_path=args.cands,
        index_path=args.index,
        seed=args.seed,
        match_threshold=args.match_threshold,
        apply_disaster=not args.no_disaster,
        filter_shared_ids=args.filter_shared_ids,
        post_align_blocking=args.post_align_blocking,
        progress_cb=_print_progress,
    )

    print("\nDONE.")
    print(f"  cache_dir: {summary.get('cache_dir')}")
    if 'mean_residual_m' in summary:
        print(f"  alignment: succeeded={summary.get('alignment_succeeded')}, "
              f"mean_residual_m={summary.get('mean_residual_m')}, "
              f"n_anchor_pairs={summary.get('n_anchor_pairs')}")


if __name__ == '__main__':
    main()
