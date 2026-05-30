"""
alignment.py

Two-stage rigid 3D alignment of candidate buildings onto the index coordinate frame.

Stage 1 — Estimate transform (Arun et al. 1987):
    Selects high-confidence matched pairs as anchors, estimates rotation R and
    translation t via SVD least-squares, then validates the result by computing
    per-anchor residuals.  If the mean residual exceeds the configured threshold
    the alignment is rejected and the pipeline continues with geometric scores only.

Stage 2 — Re-score and output:
    Applies (R, t) to all cand centroids and re-scores every pair as:
        final_score = alpha * geometric_score + (1 - alpha) * spatial_score
    where spatial_score = 1 / (1 + distance_after_alignment).
    Then applies (R, t) to all cand geometry and writes an aligned CityJSON file.

Reference:
    K. S. Arun, T. S. Huang, and S. D. Blostein. 1987.
    Least-Squares Fitting of Two 3-D Point Sets.
    IEEE TPAMI 9(5):698-700. doi:10.1109/TPAMI.1987.4767965

Usage:
    from alignment import RigidAligner
    import config

    aligner = RigidAligner(config.Alignment, logger=logger)
    rescored_pairs = aligner.run(
        object_dict,
        scored_pairs,          # list of (cand_id, index_id, geometric_score)
        suffix="seed1",
        ground_truth_R=simulator.R_crs,   # optional, for evaluation only
        ground_truth_t=simulator.t_crs,
    )
    # rescored_pairs: list of (cand_id, index_id, final_score)
    # results/aligned_candidates_seed1.json written if alignment succeeded
"""

import json
import os
import logging
import numpy as np
from typing import List, Tuple, Optional
import config as cfg


class RigidAligner:
    """
    Estimates a rigid 3D transform from anchor pairs and re-scores all matches.

    Parameters
    ----------
    align_config : config.Alignment (class reference)
    logger : logging.Logger  (optional)
    """

    def __init__(self, align_config=None, logger=None):
        if align_config is None:
            align_config = cfg.Alignment
        self.enabled = align_config.enabled
        self.min_anchor_pairs = align_config.min_anchor_pairs
        self.confidence_threshold = align_config.confidence_threshold
        self.max_residual_threshold = align_config.max_residual_threshold
        self.alpha = align_config.alpha
        self.output_crs = align_config.output_crs
        self.use_ransac = getattr(align_config, 'use_ransac', True)
        self.ransac_iterations = getattr(align_config, 'ransac_iterations', 1000)
        self.ransac_inlier_threshold = getattr(align_config, 'ransac_inlier_threshold', 10.0)
        self.spatial_sigma = getattr(align_config, 'spatial_sigma', 3.0)
        self.logger = logger or logging.getLogger(__name__)

        # Set after a successful alignment
        self.R: Optional[np.ndarray] = None
        self.t: Optional[np.ndarray] = None
        self.mean_residual: Optional[float] = None
        self.n_anchors: int = 0
        self.alignment_succeeded: bool = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        object_dict: dict,
        scored_pairs: List[Tuple[str, str, float]],
        suffix: str = "",
        ground_truth_R: Optional[np.ndarray] = None,
        ground_truth_t: Optional[np.ndarray] = None,
    ) -> List[Tuple[str, str, float]]:
        """
        Full alignment pipeline: estimate transform → validate → re-score → output.

        Parameters
        ----------
        object_dict : dict
            Full object dict with 'cands' and 'index'.
        scored_pairs : list of (cand_id, index_id, geometric_score)
            All test pairs with their classifier probability scores.
        suffix : str
            Appended to the output filename.
        ground_truth_R, ground_truth_t : np.ndarray, optional
            If provided (from DisasterSimulator), the alignment error is logged
            for evaluation purposes.  Not used in the alignment itself.

        Returns
        -------
        list of (cand_id, index_id, final_score)
            If alignment succeeded: final_score = alpha*geometric + (1-alpha)*spatial.
            If alignment failed/skipped: final_score = geometric_score unchanged.
        """
        if not self.enabled:
            self.logger.info("[RigidAligner] Disabled — returning geometric scores.")
            return scored_pairs

        # --- Stage 1: Estimate transform ---
        anchors = self._select_anchors(scored_pairs, object_dict)
        self.n_anchors = len(anchors)

        if self.n_anchors < self.min_anchor_pairs:
            self.logger.warning(
                f"[RigidAligner] Only {self.n_anchors} anchor pairs found "
                f"(need {self.min_anchor_pairs}, threshold={self.confidence_threshold}). "
                f"Skipping alignment — returning geometric scores."
            )
            return scored_pairs

        P, Q = self._build_point_sets(anchors, object_dict)
        if self.use_ransac:
            R, t, n_inliers, inlier_mask = self._estimate_rigid_transform_ransac(P, Q)
            # Validate using INLIER mean residual when RANSAC found enough inliers.
            # The all-anchor mean is dominated by false-positive anchors (look-alike
            # buildings the classifier scored high) and unfairly rejects a correct
            # transform whenever anchor-pool precision is low.
            if n_inliers >= self.min_anchor_pairs and inlier_mask.any():
                self.mean_residual = self._compute_residual(P[inlier_mask], Q[inlier_mask], R, t)
                all_anchor_mean = self._compute_residual(P, Q, R, t)
                self.logger.info(
                    f"[RigidAligner] RANSAC: {self.n_anchors} anchors | "
                    f"{n_inliers} inliers | inlier mean residual = {self.mean_residual:.2f} m "
                    f"(all-anchor mean = {all_anchor_mean:.2f} m)"
                )
            else:
                self.mean_residual = self._compute_residual(P, Q, R, t)
                self.logger.info(
                    f"[RigidAligner] RANSAC: {self.n_anchors} anchors | "
                    f"only {n_inliers} inliers (< {self.min_anchor_pairs}) | "
                    f"all-anchor mean residual = {self.mean_residual:.2f} m"
                )
        else:
            R, t = self._estimate_rigid_transform(P, Q)
            self.mean_residual = self._compute_residual(P, Q, R, t)
            self.logger.info(
                f"[RigidAligner] {self.n_anchors} anchors | "
                f"mean residual = {self.mean_residual:.2f} m"
            )

        if self.mean_residual > self.max_residual_threshold:
            self.logger.warning(
                f"[RigidAligner] Mean residual {self.mean_residual:.2f} m exceeds "
                f"threshold {self.max_residual_threshold} m. "
                f"Alignment rejected — returning geometric scores."
            )
            return scored_pairs

        self.R, self.t = R, t
        self.alignment_succeeded = True

        # Optional: log error vs ground-truth transform
        if ground_truth_R is not None and ground_truth_t is not None:
            self._log_ground_truth_error(ground_truth_R, ground_truth_t)

        # --- Stage 2: Re-score using aligned positions ---
        rescored = self._rescore_pairs(scored_pairs, object_dict)

        # Apply transform to full cand geometry and write output
        self._apply_transform_to_geometry(object_dict['cands'])
        self._write_cityjson(object_dict['cands'], suffix)

        return rescored

    # ------------------------------------------------------------------ #
    # Stage 1 helpers
    # ------------------------------------------------------------------ #

    def _select_anchors(
        self,
        scored_pairs: List[Tuple[str, str, float]],
        object_dict: dict,
    ) -> List[Tuple[str, str]]:
        """Return (cand_id, index_id) pairs with score >= confidence_threshold."""
        cands_keys = set(object_dict['cands'].keys())
        index_keys = set(object_dict['index'].keys())
        return [
            (cid, iid)
            for cid, iid, score in scored_pairs
            if score >= self.confidence_threshold
            and cid in cands_keys
            and iid in index_keys
        ]

    @staticmethod
    def _build_point_sets(
        anchors: List[Tuple[str, str]],
        object_dict: dict,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Build point sets from anchor centroids.
        P = index centroids (target), Q = cand centroids (source).
        Goal: find R, t such that P_i ≈ R @ Q_i + t.
        """
        P = np.array([object_dict['index'][iid]['centroid'] for _, iid in anchors], dtype=np.float64)
        Q = np.array([object_dict['cands'][cid]['centroid'] for cid, _ in anchors], dtype=np.float64)
        return P, Q

    @staticmethod
    def _estimate_rigid_transform(
        P: np.ndarray,
        Q: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Arun et al. 1987 SVD least-squares rigid transform.

        Returns R (3x3) and t (3,) such that P_i ≈ R @ Q_i + t.
        """
        p_bar = P.mean(axis=0)   # (3,)
        q_bar = Q.mean(axis=0)   # (3,)
        P_prime = P - p_bar      # (N, 3) centered
        Q_prime = Q - q_bar      # (N, 3) centered

        H = Q_prime.T @ P_prime  # (3, 3) cross-covariance
        U, _, Vt = np.linalg.svd(H)
        V = Vt.T

        R = V @ U.T

        # Fix reflection (degenerate / coplanar case)
        if np.linalg.det(R) < 0:
            V[:, 2] *= -1
            R = V @ U.T

        t = p_bar - R @ q_bar
        return R, t

    def _estimate_rigid_transform_ransac(
        self,
        P: np.ndarray,
        Q: np.ndarray,
    ):
        """
        RANSAC-based rigid transform estimation.

        Each iteration samples 3 anchor pairs, estimates R and t via SVD,
        then counts how many of all anchors are consistent (inliers) under
        that transform.  The best transform (most inliers) is refitted on
        all its inliers via SVD for a final least-squares solution.

        Parameters
        ----------
        P : (N, 3) index centroids
        Q : (N, 3) cand centroids

        Returns
        -------
        R : (3, 3) rotation matrix
        t : (3,) translation vector
        n_inliers : int
        """
        n = len(P)
        best_inlier_mask = np.zeros(n, dtype=bool)
        best_n_inliers = 0
        best_R, best_t = self._estimate_rigid_transform(P, Q)  # fallback

        rng = np.random.default_rng(42)

        for _ in range(self.ransac_iterations):
            # Sample 3 unique anchor pairs
            idx = rng.choice(n, size=3, replace=False)
            P_sample, Q_sample = P[idx], Q[idx]

            # Skip degenerate (collinear) samples
            if np.linalg.matrix_rank(P_sample - P_sample.mean(axis=0)) < 2:
                continue

            R_cand, t_cand = self._estimate_rigid_transform(P_sample, Q_sample)

            # Count inliers: anchors whose residual < threshold under this transform
            P_hat = (R_cand @ Q.T).T + t_cand
            residuals = np.linalg.norm(P_hat - P, axis=1)
            inlier_mask = residuals < self.ransac_inlier_threshold
            n_inliers = inlier_mask.sum()

            if n_inliers > best_n_inliers:
                best_n_inliers = n_inliers
                best_inlier_mask = inlier_mask
                best_R, best_t = R_cand, t_cand

        # Refit on all inliers of the best solution
        if best_n_inliers >= 3:
            best_R, best_t = self._estimate_rigid_transform(
                P[best_inlier_mask], Q[best_inlier_mask]
            )
            # Recompute inlier mask under the refit transform so it reflects the
            # final R, t rather than the 3-sample one used to score iterations.
            P_hat = (best_R @ Q.T).T + best_t
            residuals = np.linalg.norm(P_hat - P, axis=1)
            best_inlier_mask = residuals < self.ransac_inlier_threshold
            best_n_inliers = int(best_inlier_mask.sum())
            self.logger.info(
                f"[RigidAligner] RANSAC refit on {best_n_inliers}/{n} inliers "
                f"(threshold={self.ransac_inlier_threshold} m, "
                f"iterations={self.ransac_iterations})"
            )
        else:
            self.logger.warning(
                f"[RigidAligner] RANSAC found only {best_n_inliers} inliers — "
                f"falling back to full SVD"
            )

        return best_R, best_t, best_n_inliers, best_inlier_mask

    @staticmethod
    def _compute_residual(
        P: np.ndarray,
        Q: np.ndarray,
        R: np.ndarray,
        t: np.ndarray,
    ) -> float:
        """Mean Euclidean residual ||R @ q_i + t - p_i|| over all anchor pairs."""
        P_hat = (R @ Q.T).T + t   # (N, 3)
        residuals = np.linalg.norm(P_hat - P, axis=1)
        return float(residuals.mean())

    def _log_ground_truth_error(
        self,
        gt_R: np.ndarray,
        gt_t: np.ndarray,
    ) -> None:
        """
        Log rotation and translation error vs the ground-truth CRS transform.

        The disaster simulator applies:  cand = R_crs @ original + t_crs
        So the aligner should recover the INVERSE transform:
            self.R ≈ R_crs^T   (so that R_crs^T @ R_crs = I)
            self.t ≈ -R_crs^T @ t_crs

        Rotation check: self.R @ gt_R should be close to I.
        Translation check: self.t should be close to -gt_R^T @ gt_t.
        """
        # Rotation error: R_recovered @ R_gt should equal I if perfect
        R_check = self.R @ gt_R
        angle_err = np.degrees(np.arccos(
            np.clip((np.trace(R_check) - 1.0) / 2.0, -1.0, 1.0)
        ))
        # Translation error: compare recovered t to the expected inverse translation
        expected_t = -gt_R.T @ gt_t
        t_err = np.linalg.norm(self.t - expected_t)
        self.logger.info(
            f"[RigidAligner] Ground-truth comparison: "
            f"rotation error = {angle_err:.2f}°, "
            f"translation error = {t_err:.1f} m"
        )

    # ------------------------------------------------------------------ #
    # Stage 2 helpers
    # ------------------------------------------------------------------ #

    def _rescore_pairs(
        self,
        scored_pairs: List[Tuple[str, str, float]],
        object_dict: dict,
    ) -> List[Tuple[str, str, float]]:
        """
        Re-score pairs combining geometric score with spatial proximity
        after aligning cand centroids to the index frame.

        final_score = alpha * geometric_score + (1 - alpha) * spatial_score
        spatial_score = exp(- d² / (2 · spatial_sigma²))     # Gaussian, σ default 3 m
        """
        # Apply R, t to cand centroids only (fast; full geometry updated later)
        aligned_cand_centroids = {
            bid: self.R @ np.asarray(data['centroid'], dtype=np.float64) + self.t
            for bid, data in object_dict['cands'].items()
        }
        index_centroids = {
            bid: np.asarray(data['centroid'], dtype=np.float64)
            for bid, data in object_dict['index'].items()
        }

        rescored = []
        for cid, iid, geo_score in scored_pairs:
            if cid in aligned_cand_centroids and iid in index_centroids:
                dist = float(np.linalg.norm(aligned_cand_centroids[cid] - index_centroids[iid]))
                # Gaussian decay with σ = spatial_sigma (default 3 m, ≈ median true-match
                # residual). Sharply suppresses look-alikes that land 5–10 m away while
                # giving high scores to genuine matches at d ≤ σ.
                spatial_score = float(np.exp(-(dist * dist) / (2.0 * self.spatial_sigma ** 2)))
                final_score = self.alpha * geo_score + (1.0 - self.alpha) * spatial_score
            else:
                final_score = geo_score   # fallback if ID missing
            rescored.append((cid, iid, final_score))

        # Log score distribution summary
        geo_scores = [s for _, _, s in scored_pairs]
        final_scores = [s for _, _, s in rescored]
        self.logger.info(
            f"[RigidAligner] Score re-scaling: "
            f"geometric mean={np.mean(geo_scores):.3f} → "
            f"final mean={np.mean(final_scores):.3f} "
            f"(alpha={self.alpha})"
        )
        return rescored

    def _apply_transform_to_geometry(self, cands: dict) -> None:
        """Apply (R, t) to all cand vertices, centroids, and polygon_mesh."""
        for building in cands.values():
            verts = building['vertices']
            building['vertices'] = (self.R @ verts.T).T + self.t
            building['centroid'] = self.R @ np.asarray(building['centroid'], dtype=np.float64) + self.t
            new_mesh = []
            for surface in building['polygon_mesh']:
                new_surface = [(self.R @ np.array(c, dtype=np.float64) + self.t).tolist() for c in surface]
                new_mesh.append(new_surface)
            building['polygon_mesh'] = new_mesh

    # ------------------------------------------------------------------ #
    # CityJSON output
    # ------------------------------------------------------------------ #

    def _write_cityjson(self, cands: dict, suffix: str) -> None:
        """
        Write aligned candidates to a CityJSON 1.1 file in the index CRS.

        Vertices are stored as floating-point world coordinates (no re-quantization).
        Each building is written as a Solid LOD2 geometry preserving the original
        surface structure.
        """
        results_dir = cfg.FilePaths.results_path
        os.makedirs(results_dir, exist_ok=True)
        out_path = os.path.join(results_dir, f"aligned_candidates_{suffix}.json")

        city_objects = {}
        all_vertices = []
        vertex_index = {}   # (x, y, z) rounded → global index

        epsg_code = self.output_crs.split(":")[-1]

        for bid, building in cands.items():
            verts = building['vertices']   # (N, 3) already aligned

            # Build local vertex index
            local_idx = {}
            for v in verts:
                key = (round(float(v[0]), 6), round(float(v[1]), 6), round(float(v[2]), 6))
                if key not in vertex_index:
                    vertex_index[key] = len(all_vertices)
                    all_vertices.append(list(key))
                local_idx[key] = vertex_index[key]

            # Encode polygon_mesh as surface boundary index lists
            boundaries = []
            for surface in building['polygon_mesh']:
                ring = []
                for coord in surface:
                    key = (round(float(coord[0]), 6), round(float(coord[1]), 6), round(float(coord[2]), 6))
                    # Find the nearest stored key (handles float drift)
                    if key not in vertex_index:
                        key = min(
                            local_idx.keys(),
                            key=lambda k: (k[0]-key[0])**2 + (k[1]-key[1])**2 + (k[2]-key[2])**2
                        )
                    ring.append(vertex_index[key])
                boundaries.append([ring])

            city_objects[f"bag_{bid}"] = {
                "type": "Building",
                "geometry": [{
                    "type": "Solid",
                    "lod": "2",
                    "boundaries": [boundaries]
                }],
                "attributes": building.get("attributes", {})
            }

        cityjson = {
            "type": "CityJSON",
            "version": "1.1",
            "metadata": {
                "referenceSystem": f"https://www.opengis.net/def/crs/EPSG/0/{epsg_code}"
            },
            "CityObjects": city_objects,
            "vertices": all_vertices,
            "alignment_info": {
                "mean_residual_m": round(self.mean_residual, 3),
                "n_anchor_pairs": self.n_anchors,
                "alpha": self.alpha
            }
        }

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(cityjson, f, indent=2)

        size_mb = os.path.getsize(out_path) / 1e6
        self.logger.info(
            f"[RigidAligner] Aligned CityJSON written: {out_path} "
            f"({len(city_objects)} buildings, {size_mb:.1f} MB)"
        )
