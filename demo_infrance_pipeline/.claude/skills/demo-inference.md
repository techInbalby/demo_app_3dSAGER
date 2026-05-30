---
name: demo-inference
description: How to run the 3dSAGER demo inference pipeline and customize what each row of the output contains. Use when the user asks to add/remove columns in matches.csv, expose new per-building info to the demo app, change the score threshold, or debug a stage of the inference pipeline.
---

# 3dSAGER demo inference pipeline

This bundle (`inference.py` + `modules/` + `saved_models/` + `input_data/`) takes two CityJSON files and produces match results end-to-end without depending on the training repo.

## Running

```bash
python inference.py                              # defaults: bundled demo files, seed=1, disaster sim on, threshold 0.30
python inference.py --use-cache                  # reuse output/cache/ from a previous run
python inference.py --no-disaster                # skip DisasterSimulator (raw geometry match)
python inference.py --match-threshold 0.40       # raise for precision, lower for recall
python inference.py --cands FILE --index FILE    # override input CityJSON files
```

## Outputs (under `output/results/`)

| File | Schema | Used for |
|---|---|---|
| `matches.csv` | one row per candidate pair | tabular display in the demo app |
| `matches.parquet` | same as CSV | fast load for large tables (needs pyarrow) |
| `aligned_candidates_seed1.json` | CityJSON, full geometry | 3D visualization of cands after rigid alignment |
| `metrics_summary.json` | PR sweep + primary point | evaluation summary (only when same-ID pairs exist) |

Default columns in `matches.csv` / `matches.parquet`:

| Column | Type | Meaning |
|---|---|---|
| `cand_id` | str | Candidate building ID (BAG numeric) |
| `index_id` | str | Index building ID |
| `geometric_score` | float | Classifier P(match) before alignment |
| `final_score` | float | `alpha * geometric + (1-alpha) * spatial` after alignment; equals geometric if alignment rejected |
| `predicted_match` | int | 1 if `final_score >= --match-threshold` |
| `same_id` | int | 1 if `cand_id == index_id` (ground truth for entity resolution) |

## Pipeline stages

`inference.py` calls them in order; outputs of each stage are reusable via `--use-cache`.

1. **Preprocess** (`modules/preprocess_single.preprocess_files`) — CityJSON → `object_dict` with `vertices`, `centroid`, `grid_cell`. Cache: `output/cache/objects.joblib`.
2. **DisasterSimulator** (`modules/disaster_simulation.DisasterSimulator`) — applies a random rotation + translation + height damage to cands. Records `simulator.R_crs`, `t_crs` (the ground-truth transform) and `damage_log` (per-building damage factor).
3. **ObjectPropertiesProcessor** (`modules/object_properties.py`) — 25 geometric features per building, multiprocessed. Cache: `output/cache/property_dict.joblib`.
4. **Blocker** (`modules/blocking.py`, BKAFI) — KDTree NN search over feature-importance-weighted property vectors. Surfaces `cand_pairs_per_item` neighbors per cand. Saved pairs: `output/intermediate/blocking_pairs.joblib`.
5. **PairProcessor** (`modules/process_pairs.py`) — per-pair feature vector (property ratios).
6. **XGBClassifier** (loaded from `saved_models/`) — `predict_proba` over the feature matrix. Saved: `output/intermediate/scored_pairs.joblib`.
7. **RigidAligner** (`modules/alignment.py`) — RANSAC rigid transform from high-confidence pairs (score ≥ `confidence_threshold`), then rescore: `final = alpha * geometric + (1 - alpha) * spatial`, where `spatial = 1 / (1 + distance_after_alignment_m)`.
8. **save_results / maybe_metrics** — write the four outputs.

## Adding columns to `matches.csv` / `matches.parquet`

The schema is defined by the dict literal inside `inference.py:save_results()`. Add a key to that dict; both formats pick it up automatically.

### Where the per-building data lives at that point

| Source | Indexing | What it gives you |
|---|---|---|
| `object_dict['cands'][bid]` | string ID | `centroid` (np.ndarray[3]), `polygon_mesh`, `vertices`, `grid_cell`. For cands, these reflect the **post-DisasterSimulator** geometry. |
| `object_dict['index'][bid]` | string ID | Same fields. Index is never modified. |
| `property_dict[attr]['cands'\|'index'][bid]` | string attr name + string ID | Log-normalized geometric attribute. 25 available: `bounding_box_width/length`, `area`, `perimeter`, `perimeter_ind`, `volume`, `convex_hull_area/volume`, `ave_centroid_distance`, `height_diff`, `num_floors`, `axes_symmetry`, `compactness_2d/3d`, `density`, `elongation`, `shape_ind`, `hemisphericality`, `fractality`, `cubeness`, `circumference`, `aligned_bounding_box_width/length/height`, `num_vertices`. |
| `simulator.damage_log[bid]` | string ID (cands only) | Float in [0.3, 0.95] — height-fraction after damage; absent if `--no-disaster`. |
| `aligner.R`, `aligner.t` | — | Recovered 3D rigid transform; only set if `aligner.alignment_succeeded`. |
| `aligner.alignment_succeeded` | — | True/False. |

To make those available inside `save_results()`, pass them in from `main()`. The current signature is `save_results(scored_pairs, rescored_pairs, output_dir, match_threshold)` — extend it.

### Example: add post-alignment centroid distance per pair

```python
# inference.py — modified save_results
def save_results(scored_pairs, rescored_pairs, output_dir, match_threshold,
                 object_dict, aligner):
    rescored_by_pair = {(c, i): s for c, i, s in rescored_pairs}
    R, t, aligned = aligner.R, aligner.t, aligner.alignment_succeeded
    rows = []
    for cid, iid, geo_score in scored_pairs:
        final = rescored_by_pair.get((cid, iid), geo_score)
        # Post-alignment centroid distance (None if alignment rejected)
        if aligned:
            cand_c = np.asarray(object_dict['cands'][cid]['centroid'])
            idx_c  = np.asarray(object_dict['index'][iid]['centroid'])
            dist_m = float(np.linalg.norm((R @ cand_c) + t - idx_c))
        else:
            dist_m = None
        rows.append({
            'cand_id': cid,
            'index_id': iid,
            'geometric_score': round(geo_score, 4),
            'final_score': round(final, 4),
            'distance_after_alignment_m': round(dist_m, 2) if dist_m is not None else None,
            'predicted_match': int(final >= match_threshold),
            'same_id': int(cid == iid),
        })
    ...
```

And in `main()`:

```python
df = save_results(scored_pairs, rescored_pairs, results_dir, args.match_threshold,
                  object_dict=object_dict, aligner=aligner)
```

`aligner` needs to be hoisted out of `align()` — the current code returns only `rescored_pairs`. Either return the aligner too, or keep it as a module-level reference, or inline `align()`.

### Example: add building volume + centroid for both sides

```python
rows.append({
    ...,
    'cand_volume_log':  float(property_dict['volume']['cands'][cid]),
    'index_volume_log': float(property_dict['volume']['index'][iid]),
    'cand_x':  float(object_dict['cands'][cid]['centroid'][0]),
    'cand_y':  float(object_dict['cands'][cid]['centroid'][1]),
    'index_x': float(object_dict['index'][iid]['centroid'][0]),
    'index_y': float(object_dict['index'][iid]['centroid'][1]),
})
```

(Pass `property_dict` and `object_dict` into `save_results`.)

### Example: add damage factor (cands only)

```python
damage = simulator.damage_log.get(cid) if simulator is not None else None
rows.append({..., 'cand_damage_factor': damage})
```

## Removing columns

Delete the key from the dict literal in `save_results()`. Both CSV and parquet stay consistent.

## Switching to one-row-per-cand (best match only)

Append in `save_results()` before `df.to_csv`:

```python
df = df.sort_values('final_score', ascending=False) \
       .groupby('cand_id', as_index=False).first()
```

This collapses the per-pair table to the best `index_id` per `cand_id`. Useful for an app that displays one matched index per cand.

## Tuning knobs (config_demo.py)

| Knob | Default | Effect |
|---|---|---|
| `DEMO_NN_COUNT` | 30 | Index neighbors per cand surfaced by BKAFI. Higher → better recall, more compute. |
| `DEMO_BKAFI_DIM` | 24 | Number of top-importance features used for KDTree search. Max = 25 (all features). |
| `config.Alignment.ransac_iterations` | 5000 | More iterations → better chance of recovering transform with noisy anchor pools. |
| `config.Alignment.alpha` | 0.3 | Final-score weighting. `1.0` = geometric only, `0.0` = spatial only. With Gaussian spatial σ=3 m, 0.3 is empirically best on demo data. |
| `config.Alignment.spatial_sigma` | 3.0 m | Decay length of the Gaussian spatial score: `spatial(d) = exp(-d²/(2σ²))`. σ should approximate the median true-match residual after alignment. |
| `config.Alignment.confidence_threshold` | 0.8 | Score cutoff for anchor selection in RigidAligner. |
| `config.Alignment.ransac_inlier_threshold` | 10.0 m | Distance threshold for inlier classification. |
| `--match-threshold` CLI flag | 0.65 | Cutoff for `predicted_match` column. Tune from `metrics_summary.json['pr_sweep']`. |

## Debugging cheat sheet

| Symptom | Likely cause | Fix |
|---|---|---|
| `Alignment rejected — returning geometric scores.` | RANSAC found few inliers OR inlier mean residual exceeds 50 m | Raise `confidence_threshold` to clean anchors; raise `ransac_iterations`; check there are enough true matches in the anchor pool. |
| All `final_score < 0.30` | DisasterSimulator translated cands far from index, alignment recovered the transform but spatial term is dominated by tiny similarity values | Lower `alpha` (more spatial weight) or lower `--match-threshold`. |
| `[process_prop] Non-finite value for ... -> replacing with 0.0` warnings | Degenerate building geometry (volume=0, etc.) | Already handled; if a flood appears, inspect the offending `obj_ind` in `object_dict`. |
| `KeyError` in `_get_ratio` | Pair has IDs not in property_dict | Filter in `build_feature_matrix()` (already done); investigate why the ID was in pairs but not properties. |
| Blocking returns 0 pairs | Saved BKAFI artifacts (`*feature_importance_dict*.joblib`, `*property_ratios*.joblib`) don't match | Re-train and re-save artifacts, or copy from the original `saved_model_files/Hague/`. |
| Parquet write fails | Missing pyarrow | `pip install pyarrow`. CSV still works. |

## Common UI-driven changes

- **Add a `match_rank` column** (1 = best per cand) — sort by `final_score` descending within each `cand_id`, assign `rank = range(1, n+1)`.
- **Include only top-K rows per cand** — `df.sort_values('final_score', ascending=False).groupby('cand_id').head(K)`.
- **Filter to predicted matches only** — `df = df[df.predicted_match == 1]` before saving.
- **GeoJSON footprints in output** — pull `object_dict[side][bid]['polygon_mesh']`, project to 2D, serialize as `shapely.Polygon(...).wkt` or GeoJSON dict.
