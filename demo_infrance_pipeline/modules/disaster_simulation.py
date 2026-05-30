"""
disaster_simulation.py

Simulates a post-disaster scenario on the candidate building set.

Two independent simulations are applied in order (CRS first, then damage):

  1. CRS simulation (global):
     All cand buildings are rotated by a random angle around the Z-axis and shifted
     by a large random translation, simulating a dataset with no absolute coordinate
     reference.  The internal geometry of each building is preserved exactly; only
     the global frame changes.  This forces the model to rely on rotation-invariant
     features (aligned BB, volume, area, compactness) rather than axis-aligned ones.

  2. Damage simulation (per-building):
     A random subset of cand buildings have their height reduced, simulating partial
     collapse.  Each damaged building keeps its ground footprint but loses height
     according to a random damage factor.

Only 'cands' are ever modified.  The 'index' (reference dataset) is never touched.

Usage:
    from disaster_simulation import DisasterSimulator
    import config

    simulator = DisasterSimulator(config.DisasterSimulation, seed=1)
    object_dict = simulator.apply(object_dict)
    # simulator.R_crs, simulator.t_crs  — ground-truth transform for evaluation
    # simulator.damage_log              — per-building damage factors for inspection
"""

import numpy as np
import config as cfg


class DisasterSimulator:
    """
    Applies CRS simulation and damage simulation to the candidate set.

    Parameters
    ----------
    sim_config : config.DisasterSimulation (class reference)
    seed : int
        Controls randomness for both simulations.  Different seeds per pipeline
        run ensure the model trains on varied scenarios.
    """

    _Z_EPSILON = 1e-4   # threshold to distinguish above-ground vertices from ground

    def __init__(self, sim_config=None, seed=42):
        if sim_config is None:
            sim_config = cfg.DisasterSimulation
        self.enabled = sim_config.enabled
        self.crs_simulation = sim_config.crs_simulation
        self.damage_probability = sim_config.damage_probability
        self.min_damage_factor = sim_config.min_damage_factor
        self.max_damage_factor = sim_config.max_damage_factor
        self._rng = np.random.default_rng(seed)

        # Set after apply() — expose for external evaluation
        self.R_crs = None      # (3,3) rotation matrix applied to all cands
        self.t_crs = None      # (3,) translation vector applied to all cands
        self.damage_log = {}   # {building_id: damage_factor}  (1.0 = undamaged)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def apply(self, object_dict: dict) -> dict:
        """
        Apply CRS simulation then damage simulation to cands in-place.

        Parameters
        ----------
        object_dict : dict
            Full object dict with keys 'cands', 'index', 'mapping_dict', etc.

        Returns
        -------
        dict
            Same object_dict with modified cands.
        """
        if not self.enabled:
            return object_dict

        if self.crs_simulation:
            object_dict = self._apply_crs_simulation(object_dict)

        object_dict = self._apply_damage_simulation(object_dict)

        self._print_summary(object_dict)
        return object_dict

    # ------------------------------------------------------------------ #
    # CRS simulation
    # ------------------------------------------------------------------ #

    def _apply_crs_simulation(self, object_dict: dict) -> dict:
        """
        Apply a single random rotation (around Z) + large translation to ALL cands.

        The same (R_crs, t_crs) is applied to every building so internal
        relative geometry is preserved — only the global frame changes.
        """
        # Random rotation angle in [0, 2π)
        theta = self._rng.uniform(0.0, 2.0 * np.pi)
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        self.R_crs = np.array([
            [ cos_t, -sin_t, 0.0],
            [ sin_t,  cos_t, 0.0],
            [   0.0,    0.0, 1.0]
        ])

        # Large random translation — no absolute reference. Magnitude is
        # configurable so the demo can use a small offset (~hundreds of m)
        # that stays inside the projection's valid extent for the WGS84
        # transform in the viewer. Falls back to the original ±100 km when
        # `crs_translation_max` is not set on the config.
        translation_max = float(getattr(cfg.DisasterSimulation, 'crs_translation_max', 100_000.0))
        tx = self._rng.uniform(-translation_max, translation_max)
        ty = self._rng.uniform(-translation_max, translation_max)
        self.t_crs = np.array([tx, ty, 0.0])

        for bid, building in object_dict['cands'].items():
            self._transform_building(building, self.R_crs, self.t_crs)

        print(f"[DisasterSimulator] CRS simulation applied: "
              f"rotation={np.degrees(theta):.1f}°, "
              f"translation=({tx:.0f}, {ty:.0f}) m")
        return object_dict

    @staticmethod
    def _transform_building(building: dict, R: np.ndarray, t: np.ndarray) -> None:
        """Apply rigid transform (R, t) to all geometry of one building in-place."""
        # vertices: (N, 3)
        verts = building['vertices']
        building['vertices'] = (R @ verts.T).T + t

        # centroid: (3,)
        building['centroid'] = R @ np.asarray(building['centroid'], dtype=np.float64) + t

        # polygon_mesh: list of surfaces, each surface = list of [x, y, z]
        new_mesh = []
        for surface in building['polygon_mesh']:
            new_surface = []
            for coord in surface:
                v = np.array(coord, dtype=np.float64)
                new_surface.append((R @ v + t).tolist())
            new_mesh.append(new_surface)
        building['polygon_mesh'] = new_mesh

    # ------------------------------------------------------------------ #
    # Damage simulation
    # ------------------------------------------------------------------ #

    def _apply_damage_simulation(self, object_dict: dict) -> dict:
        """
        Randomly reduce height of a fraction of cand buildings.

        Each damaged building keeps its ground footprint but all vertices
        above z_min are scaled: z_new = z_min + (z - z_min) * damage_factor
        """
        cands = object_dict['cands']
        cand_ids = list(cands.keys())
        n_to_damage = int(round(self.damage_probability * len(cand_ids)))

        # Select which buildings to damage
        damaged_indices = self._rng.choice(len(cand_ids), size=n_to_damage, replace=False)
        damaged_ids = [cand_ids[i] for i in damaged_indices]

        # One damage factor per building
        damage_factors = self._rng.uniform(
            self.min_damage_factor, self.max_damage_factor, size=n_to_damage
        )

        for bid, factor in zip(damaged_ids, damage_factors):
            self._damage_building(cands[bid], factor)
            self.damage_log[bid] = round(float(factor), 4)

        # Undamaged buildings recorded as 1.0
        for bid in cand_ids:
            if bid not in self.damage_log:
                self.damage_log[bid] = 1.0

        print(f"[DisasterSimulator] Damage simulation: "
              f"{n_to_damage}/{len(cand_ids)} buildings damaged "
              f"(factor range [{self.min_damage_factor}, {self.max_damage_factor}])")
        return object_dict

    def _damage_building(self, building: dict, damage_factor: float) -> None:
        """Reduce height of a single building in-place."""
        verts = building['vertices']  # (N, 3)
        z_min = float(verts[:, 2].min())

        # Update vertices array
        mask = verts[:, 2] > (z_min + self._Z_EPSILON)
        verts[mask, 2] = z_min + (verts[mask, 2] - z_min) * damage_factor
        building['vertices'] = verts

        # Update polygon_mesh
        new_mesh = []
        for surface in building['polygon_mesh']:
            new_surface = []
            for coord in surface:
                x, y, z = coord[0], coord[1], coord[2]
                if z > z_min + self._Z_EPSILON:
                    z = z_min + (z - z_min) * damage_factor
                new_surface.append([x, y, z])
            new_mesh.append(new_surface)
        building['polygon_mesh'] = new_mesh

        # Update centroid z
        new_z_max = float(verts[:, 2].max())
        c = np.asarray(building['centroid'], dtype=np.float64)
        c[2] = (z_min + new_z_max) / 2.0
        building['centroid'] = c

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #

    @staticmethod
    def _print_summary(object_dict: dict) -> None:
        print(f"[DisasterSimulator] Done. "
              f"cands: {len(object_dict['cands'])}, "
              f"index: {len(object_dict['index'])} (unchanged)")
