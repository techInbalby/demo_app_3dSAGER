import json
import os
import config
from utils import *
import pickle as pkl
import argparse
from time import time
from multiprocessing import Pool, cpu_count
import numpy as np
from scipy.spatial import KDTree


class DataPartitionGenerator:
    def __init__(self, args, min_surfaces_num=10):
        self.dataset_name = args.dataset_name
        self.min_surfaces_num = min_surfaces_num
        self.train_neg_samples_list = args.train_neg_samples_list
        self.train_size_ratio_list = args.train_size_ratio_list
        self.test_size_ratio_list = args.test_size_ratio_list
        self.test_negative_samples_list = args.test_negative_samples_list
        self.cands_ids, self.index_ids = self._get_cands_and_index_ids()
        # self.create_dataset_partition_dict()

    def _get_cands_and_index_ids(self):
        dataset_config = json.load(open('dataset_configs.json'))[self.dataset_name]
        main_object_dict = getattr(self, f'_read_objects_{self.dataset_name}')(dataset_config)
        cands_ids = set(main_object_dict['cands'].keys())
        index_ids = set(main_object_dict['index'].keys())
        # Store centroids for grid split and spatial negative sampling
        self.cands_centroids = {k: np.asarray(main_object_dict['cands'][k]['centroid'], dtype=np.float64)
                                for k in main_object_dict['cands']}
        self.index_centroids = {k: np.asarray(main_object_dict['index'][k]['centroid'], dtype=np.float64)
                                for k in main_object_dict['index']}
        # Store pre-computed grid cells if available (from preprocess_hague.py cache)
        self.cands_grid_cells = {k: main_object_dict['cands'][k]['grid_cell']
                                 for k in main_object_dict['cands']
                                 if 'grid_cell' in main_object_dict['cands'][k]}
        self.index_grid_cells = {k: main_object_dict['index'][k]['grid_cell']
                                 for k in main_object_dict['index']
                                 if 'grid_cell' in main_object_dict['index'][k]}
        self.grid_meta = main_object_dict.get('grid_meta', {})
        del main_object_dict
        return cands_ids, index_ids

    # ------------------------------------------------------------------
    # Spatial grid helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _assign_grid_cells(centroids_dict: dict, cell_size: float) -> dict:
        """
        Assign each building to a grid cell based on its 2D centroid.

        Parameters
        ----------
        centroids_dict : {id: np.ndarray([x, y, z])}
        cell_size : float  — cell side length in the same units as the CRS (meters)

        Returns
        -------
        {building_id: (cell_x, cell_y)}
        """
        if not centroids_dict:
            return {}
        coords = np.array([c[:2] for c in centroids_dict.values()])
        x_min, y_min = coords[:, 0].min(), coords[:, 1].min()
        cell_assignments = {}
        for bid, centroid in centroids_dict.items():
            cx = int(np.floor((centroid[0] - x_min) / cell_size))
            cy = int(np.floor((centroid[1] - y_min) / cell_size))
            cell_assignments[bid] = (cx, cy)
        return cell_assignments

    @staticmethod
    def _grid_split(cell_assignments: dict, train_ratio: float, seed: int,
                    contiguous_test: bool = True):
        """
        Assign grid cells to train or test and return per-building split.

        Each building belongs to exactly one cell (assigned by centroid).
        No building can appear in both splits.

        If contiguous_test=True (default): test cells are selected via BFS from the
        bottom-left corner of the grid, producing a single contiguous geographic block.
        This keeps the demo app's visible area coherent (train region) and ensures the
        test zone is a spatially distinct held-out area.

        If contiguous_test=False: cells are assigned randomly (original behaviour).

        Returns
        -------
        train_ids : set
        test_ids  : set
        """
        from collections import deque
        unique_cells = list(set(cell_assignments.values()))
        n_train_cells = max(1, int(round(train_ratio * len(unique_cells))))
        n_test_cells = len(unique_cells) - n_train_cells

        if contiguous_test and n_test_cells > 0:
            cell_set = set(map(tuple, unique_cells))
            # BFS starting from the bottom-left corner (min cx+cy) to grow a
            # contiguous test region
            start = min(cell_set, key=lambda c: (c[0] + c[1]))
            visited = {start}
            queue = deque([start])
            test_cells = []
            while queue and len(test_cells) < n_test_cells:
                cx, cy = queue.popleft()
                test_cells.append((cx, cy))
                for nc in [(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)]:
                    if nc in cell_set and nc not in visited:
                        visited.add(nc)
                        queue.append(nc)
            test_cells = set(test_cells)
            train_cells = cell_set - test_cells
            strategy = "contiguous BFS"
        else:
            rng = np.random.default_rng(seed)
            rng.shuffle(unique_cells)
            train_cells = set(map(tuple, unique_cells[:n_train_cells]))
            test_cells  = set(map(tuple, unique_cells[n_train_cells:]))
            strategy = "random"

        train_ids = {bid for bid, cell in cell_assignments.items() if cell in train_cells}
        test_ids  = {bid for bid, cell in cell_assignments.items() if cell in test_cells}
        print(f"[GridSplit] {len(train_cells)} train cells / {len(test_cells)} test cells | "
              f"{len(train_ids)} train buildings / {len(test_ids)} test buildings "
              f"({strategy})")
        return train_ids, test_ids

    def _build_spatial_index(self, train_index_ids: set) -> None:
        """
        Build a KDTree on train-split index building centroids for
        neighborhood-aware negative sampling.
        """
        ids_list = [bid for bid in train_index_ids if bid in self.index_centroids]
        if not ids_list:
            self._index_kdtree = None
            self._index_ids_list = []
            return
        xy = np.array([self.index_centroids[bid][:2] for bid in ids_list], dtype=np.float64)
        self._index_kdtree = KDTree(xy)
        self._index_ids_list = ids_list

    # ------------------------------------------------------------------

    def create_dataset_partition_dict(self, seed):
        self.seed = seed
        cands_ids = self.cands_ids
        index_ids = self.index_ids

        # --- Spatial grid split ---
        cell_size = config.DataPartition.grid_cell_size
        train_ratio = config.DataPartition.train_ratio
        intersection_ids = cands_ids.intersection(index_ids)
        # Use pre-computed grid cells from the preprocessed cache when available
        # (real-world EPSG:7415 coordinates → correct spatial splits)
        if self.cands_grid_cells:
            intersection_cell_assignments = {bid: self.cands_grid_cells[bid]
                                             for bid in intersection_ids
                                             if bid in self.cands_grid_cells}
            print(f"[GridSplit] Using pre-computed grid cells "
                  f"(cell_size={self.grid_meta.get('cell_size', cell_size):.0f} m, "
                  f"{len(intersection_cell_assignments)} buildings)")
        else:
            intersection_centroids = {bid: self.index_centroids[bid]
                                      for bid in intersection_ids if bid in self.index_centroids}
            intersection_cell_assignments = self._assign_grid_cells(intersection_centroids, cell_size)
        train_intersection_ids, test_intersection_ids = self._grid_split(
            intersection_cell_assignments, train_ratio, seed,
            contiguous_test=config.DataPartition.contiguous_test
        )
        # Build KDTree on train-split index buildings for neighborhood negative sampling
        self._build_spatial_index(train_intersection_ids)

        train_negative_sampling_dict = self._get_train_negative_sampling_dict(
            train_intersection_ids, index_ids
        )
        test_dict = self._get_test_ids_dict(
            test_intersection_ids, index_ids, train_negative_sampling_dict
        )
        dataset_partition_dict = {'train': {'negative_sampling': train_negative_sampling_dict}, 'test': test_dict}
        self._save_dataset_partition_dict(dataset_partition_dict)

    def _get_train_negative_sampling_dict(self, cands_ids, index_ids):
        np.random.seed(self.seed)
        train_ids_dict = {}
        intersection_set = cands_ids.intersection(index_ids)
        for train_size, ratio_val in self.train_size_ratio_list.items():
            print(f"Creating training data ({train_size})")
            train_ids_dict[train_size] = {}
            train_ids_size_num = int(ratio_val * len(intersection_set))
            train_ids_for_curr_size = set(np.random.choice(list(intersection_set), train_ids_size_num, replace=False))
            for neg_samples_num in self.train_neg_samples_list:
                train_ids_dict[train_size][neg_samples_num] = self._get_pairs_per_neg_samples(train_ids_for_curr_size,
                                                                                              index_ids,
                                                                                              neg_samples_num)
        return train_ids_dict

    def _generate_pairs_for_id(self, args):
        """
        Generate one positive pair + neg_samples_num negative pairs for cand_id.

        Negative sampling strategy (config.Blocking.neighborhood_neg_ratio):
          - Up to `neighborhood_neg_ratio` fraction drawn from buildings within
            `neighborhood_radius` meters (same neighborhood, hard negatives).
          - Remainder filled randomly from the full index pool.
        Falls back to fully random sampling if the KDTree is unavailable.
        """
        cand_id, index_ids_list, neg_samples_num, seed = args
        rng = np.random.default_rng(seed + hash(cand_id) % 1_000_000)

        neighborhood_radius = config.Blocking.neighborhood_radius
        neighborhood_neg_ratio = config.Blocking.neighborhood_neg_ratio
        n_neighborhood = int(round(neg_samples_num * neighborhood_neg_ratio))
        n_random = neg_samples_num - n_neighborhood

        spatial_neg_ids = []
        # Spatial candidates from KDTree
        if (n_neighborhood > 0
                and self._index_kdtree is not None
                and cand_id in self.cands_centroids):
            cand_xy = self.cands_centroids[cand_id][:2]
            neighbor_rows = self._index_kdtree.query_ball_point(cand_xy, r=neighborhood_radius)
            neighbor_ids = [self._index_ids_list[i] for i in neighbor_rows
                            if self._index_ids_list[i] != cand_id]
            if len(neighbor_ids) >= n_neighborhood:
                chosen = rng.choice(neighbor_ids, n_neighborhood, replace=False)
                spatial_neg_ids = list(chosen)
            else:
                spatial_neg_ids = neighbor_ids          # take all available
                n_random = neg_samples_num - len(spatial_neg_ids)

        # Random candidates to fill the remainder
        exclude = set(spatial_neg_ids) | {cand_id}
        remaining = [bid for bid in index_ids_list if bid not in exclude]
        if n_random > 0 and remaining:
            n_draw = min(n_random, len(remaining))
            random_neg_ids = list(rng.choice(remaining, n_draw, replace=False))
        else:
            random_neg_ids = []

        all_neg_ids = spatial_neg_ids + random_neg_ids
        neg_pairs = [(cand_id, neg_id) for neg_id in all_neg_ids]
        return [(cand_id, cand_id)] + neg_pairs

    # def _get_pairs_per_neg_samples(self, ids_for_curr_size, index_ids, neg_samples_num):
    #     np.random.seed(self.seed)
    #     pos_pairs = [(cand_id, cand_id) for cand_id in ids_for_curr_size]
    #     neg_pairs = []
    #     for cand_id in ids_for_curr_size:
    #         neg_samples = set(np.random.choice(list(index_ids), neg_samples_num, replace=False))
    #         neg_pairs.extend([(cand_id, neg_sample) for neg_sample in neg_samples if neg_sample != cand_id])
    #     all_pairs = pos_pairs + neg_pairs
    #     np.random.shuffle(all_pairs)
    #     return all_pairs

    def _init_seed(self):
        np.random.seed(self.seed)

    def _get_pairs_per_neg_samples(self, ids_for_curr_size, index_ids, neg_samples_num):
        ids_for_curr_size_list = list(ids_for_curr_size)
        index_ids_list = list(index_ids)
        args = [
            (cand_id, index_ids_list, neg_samples_num, self.seed)
            for cand_id in ids_for_curr_size_list
        ]
        with Pool(cpu_count(), initializer=self._init_seed, initargs=()) as pool:
            results = pool.map(self._generate_pairs_for_id, args)
        all_pairs = [pair for sublist in results for pair in sublist]
        np.random.seed(self.seed)
        np.random.shuffle(all_pairs)
        return all_pairs

    def _get_test_ids_dict(self, cands_ids, index_ids, train_ids_dict):
        test_ids_dict = {}
        intersection_set = cands_ids.intersection(index_ids)
        test_ids_dict['matching'] = self._get_test_pairs_for_matching(index_ids, intersection_set, train_ids_dict)
        test_ids_dict['blocking'] = self._get_test_data_for_blocking(cands_ids, index_ids,
                                                                     intersection_set,
                                                                     train_ids_dict)
        return test_ids_dict

    def _get_test_pairs_for_matching(self, index_ids, intersection_set, train_ids_dict):
        print("Creating test data for matching")
        test_matching_dict = {}
        test_matching_dict['negative_sampling'] = self._get_negative_sampling_test_ids_dict(index_ids,
                                                                                            intersection_set,
                                                                                            train_ids_dict)
        return test_matching_dict

    def _get_negative_sampling_test_ids_dict(self, index_ids, intersection_set, train_ids_dict):
        np.random.seed(self.seed)
        local_test_ids_dict = {}
        for test_size, ratio_val in self.test_size_ratio_list.items():
            print(f"Creating test data for matching ({test_size})")
            local_test_ids_dict[test_size] = {}
            corresponding_train_cands_ids = set \
                ([pair[0] for pair in train_ids_dict[test_size][self.train_neg_samples_list[0]]])
            potential_test_ids = intersection_set - corresponding_train_cands_ids
            test_ids_size_num = int(ratio_val * len(potential_test_ids))
            test_ids_for_curr_size = set(np.random.choice(list(potential_test_ids), test_ids_size_num, replace=False))
            for test_neg_samples in self.test_negative_samples_list:
                local_test_ids_dict[test_size][test_neg_samples] = self._get_pairs_per_neg_samples \
                    (test_ids_for_curr_size, index_ids, test_neg_samples)
        return local_test_ids_dict

    def _get_test_data_for_blocking(self, cands_ids, index_ids, intersection_set, train_ids_dict, non_matched_rat=0.2):
        test_blocking_dict = defaultdict(dict)
        for test_size, ratio_val in self.test_size_ratio_list.items():
            print(f"Creating test data for blocking ({test_size})")
            corresponding_train_cands_ids = set([pair[0] for pair in
                                                 train_ids_dict[test_size][self.train_neg_samples_list[0]]])
            potential_cands_test_ids = intersection_set - corresponding_train_cands_ids
            # non_matched_cands_ids = cands_ids - intersection_set
            cands_test_ids = set(np.random.choice(list(potential_cands_test_ids),
                                                  int(ratio_val * len(potential_cands_test_ids)), replace=False))
            # select non-matched_ratio of the intersection_set and remove them from index_ids
            index_ids_to_remove = set(np.random.choice(list(cands_test_ids),
                                                       int(non_matched_rat * len(cands_test_ids)), replace=False))
            # non_matched_cands_ids = set(np.random.choice(list(non_matched_cands_ids),
            #                                 int(non_matched_rat * len(non_matched_cands_ids)), replace=False))
            # index_test_ids = cands_test_ids.copy()
            # cands_test_ids.update(non_matched_cands_ids)
            # remove from index_ids the ids that are in index_ids_to_remove
            index_test_ids = index_ids - index_ids_to_remove
            index_test_ids = set(np.random.choice(list(index_test_ids),
                                                   int(ratio_val * len(index_test_ids)), replace=False))
            # index_test_ids.update(set(np.random.choice(list(index_ids),
            #                                            int(ratio_val * len(index_ids)),
            #                                            replace=False)))
            test_blocking_dict[test_size] = {'cands': cands_test_ids, 'index': index_test_ids}
        return test_blocking_dict

    def _read_objects_bo_em(self, dataset_config):
        objects_path_dict = read_object_path_dict(dataset_config)
        object_dict = defaultdict(dict)
        for objects_type, objects_path in objects_path_dict.items():
            for filename in os.listdir(objects_path):
                file_ind = int(filename.split('.')[0])
                json_data = read_json(objects_path, file_ind)
                object_dict = self._insert_polygon_mesh(object_dict, objects_type, json_data, file_ind)
            object_dict[objects_type] = dict(sorted(object_dict[objects_type].items()))
        return object_dict

    @staticmethod
    def _remove_train_objects_from_object_dict(object_dict, train_ids):
        for objects_type in object_dict.keys():
            object_dict[objects_type] = {
                object_id: object_data
                for object_id, object_data in object_dict[objects_type].items()
                if object_id not in train_ids
            }
        return object_dict

    def _read_objects_gpkg(self, dataset_config):
        objects_path_dict = read_object_path_dict(dataset_config)
        object_dict = defaultdict(dict)
        for objects_type, objects_path in objects_path_dict.items():
            for filename in os.listdir(objects_path):
                file_ind = int(filename.split('.')[0])
                json_data = read_json(objects_path, file_ind)
                json_data = json.loads(json_data)
                object_dict = self._insert_polygon_mesh(object_dict, objects_type, json_data, file_ind)
            object_dict[objects_type] = dict(sorted(object_dict[objects_type].items()))
        return object_dict

    def _read_objects_delivery3(self, dataset_config):
        objects_path_dict = read_object_path_dict(dataset_config)
        object_dict = defaultdict(dict)
        mapping_dict = defaultdict(dict)
        inv_mapping_dict = defaultdict(dict)
        for objects_type, objects_path in objects_path_dict.items():
            for file_ind, file_name in enumerate(os.listdir(objects_path)):
                file_name = file_name.split('.')[0]
                json_data = read_json(objects_path, file_name)
                object_dict = self._insert_polygon_mesh(object_dict, objects_type, json_data, file_ind)
                mapping_dict[objects_type][file_ind] = file_name
                inv_mapping_dict[objects_type][file_name] = file_ind
            object_dict[objects_type] = dict(sorted(object_dict[objects_type].items()))
        object_dict['mapping_dict'] = mapping_dict
        object_dict['inv_mapping_dict'] = inv_mapping_dict
        return object_dict

    @staticmethod
    def _process_cityjson_file(args):
        file_path, objects_type, standardize_obj_key_fn, get_polygon_mesh_fn = args
        local_object_dict = {}
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            vertices = data['vertices']
            for obj_key in data['CityObjects']:
                try:
                    new_obj_key = standardize_obj_key_fn(obj_key, objects_type)
                    polygon_mesh_data = get_polygon_mesh_fn(data, obj_key, vertices)
                    if polygon_mesh_data is not None:
                        local_object_dict[new_obj_key] = polygon_mesh_data
                except:
                    continue
        except:
            pass
        return objects_type, local_object_dict

    def _read_objects_Hague(self, dataset_config):
        """Load from preprocessed cache (produced by preprocess_hague.py).

        The cache contains real-world EPSG:7415 coordinates and pre-computed
        grid_cell assignments, so grid splits are spatially correct.
        Run `python preprocess_hague.py` once before calling this.
        """
        raw_cache_path = (f"{config.FilePaths.object_dict_path}"
                          f"{self.dataset_name}_raw.joblib")
        if not os.path.exists(raw_cache_path):
            raise FileNotFoundError(
                f"Preprocessed cache not found: {raw_cache_path}\n"
                f"Run:  python preprocess_hague.py"
            )
        print(f"Loading preprocessed cache: {raw_cache_path}")
        return joblib.load(raw_cache_path)

    @staticmethod
    def _generate_object_dict(results):
        object_dict = defaultdict(dict)
        for objects_type, obj_dict in results:
            object_dict[objects_type].update(obj_dict)
        intersection_keys = set(object_dict['cands'].keys()).intersection(object_dict['index'].keys())
        object_dict['cands'] = {k: object_dict['cands'][k] for k in intersection_keys}
        return object_dict, intersection_keys


    @staticmethod
    def _print_object_dict_info(object_dict, intersection_keys):
        print(f"Number of overlapping objects: {len(intersection_keys)}")
        print(f"Number of cands: {len(object_dict['cands'])}")
        print(f"Number of index: {len(object_dict['index'])}")
        return

    @staticmethod
    def _update_object_dict_mapping(object_dict, objects_path_dict):
        for objects_type in objects_path_dict:
            keys = list(object_dict[objects_type].keys())
            object_dict['mapping_dict'][objects_type] = {i: k for i, k in enumerate(keys)}
            object_dict['inv_mapping_dict'][objects_type] = {k: i for i, k in enumerate(keys)}
        return object_dict


    # def _read_objects_Hague(self, dataset_config):
    #     objects_path_dict = read_object_path_dict(dataset_config)
    #     object_dict = defaultdict(dict)
    #     for objects_type, objects_path in objects_path_dict.items():
    #         print(f"Reading {objects_type} objects")
    #         file_list = [f for f in os.listdir(objects_path) if f.endswith('.json')]
    #         for file_ind, file_name in enumerate(file_list):
    #             print(f"File number {file_ind + 1} out of {len(file_list)}")
    #             file_path = ''.join([objects_path, file_name])
    #             with open(file_path, 'r') as f:
    #                 data = json.load(f)
    #             vertices = data['vertices']
    #             for obj_key in data['CityObjects'].keys():
    #                 try:
    #                     new_obj_key = self.standardize_obj_key(obj_key, objects_type)
    #                     polygon_mesh_data = self._get_polygon_mesh(data, obj_key, vertices)
    #                     if polygon_mesh_data is not None:
    #                         object_dict[objects_type][new_obj_key] = polygon_mesh_data
    #                 except:
    #                     continue
    #     intersection_keys = set(object_dict['cands'].keys()).intersection(set(object_dict['index'].keys()))
    #     object_dict['cands'] = {obj_key: object_dict['cands'][obj_key] for obj_key in intersection_keys}
    #     print(f"Number of overlapping objects: {len(intersection_keys)}")
    #     print(f"Number of cands: {len(object_dict['cands'])}")
    #     print(f"Number of index: {len(object_dict['index'])}")
    #     for objects_type in objects_path_dict.keys():
    #         object_dict['mapping_dict'][objects_type] = {ind: obj_key for ind, obj_key in
    #                                                      enumerate(object_dict[objects_type].keys())}
    #         object_dict['inv_mapping_dict'][objects_type] = {obj_key: ind for ind, obj_key in
    #                                                          enumerate(object_dict[objects_type].keys())}
    #     return object_dict

    @staticmethod
    def standardize_obj_key(obj_key, object_type):
        if object_type == 'cands':
            return obj_key.split('bag_')[1]
        elif object_type == 'index':
            return obj_key.split('NL.IMBAG.Pand.')[1].split('-0')[0]
        else:
            raise ValueError('Invalid source')

    def _insert_polygon_mesh(self, object_dict, obj_type, obj_data, obj_ind=None):
        vertices = obj_data['vertices']
        obj_key = list(obj_data['CityObjects'].keys())[0]
        polygon_mesh = self._get_polygon_mesh(obj_data, obj_key, vertices)
        if polygon_mesh is not None:
            object_dict[obj_type][obj_ind] = polygon_mesh
        return object_dict

    def _get_polygon_mesh(self, obj_data, obj_key, vertices):
        boundaries = obj_data['CityObjects'][obj_key]['geometry'][0]['boundaries'][0]
        if len(boundaries) < self.min_surfaces_num:
            return None
        polygon_mesh = []
        for surface in boundaries:
            polygon_mesh.append([vertices[i] for sub_surface_list in surface for i in sub_surface_list])
        vertices = self._get_vertices(polygon_mesh)
        centroid = self._compute_object_centroid(vertices)
        return {'polygon_mesh': polygon_mesh, 'vertices': vertices, 'centroid': centroid}

    @staticmethod
    def _compute_object_centroid(vertices):
        unique_vertices = np.array(vertices)
        return unique_vertices.mean(axis=0)

    @staticmethod
    def _get_vertices(polygon_mesh):
        return np.unique(np.array([coord for surface in polygon_mesh for coord in surface]), axis=0)

    def _save_dataset_partition_dict(self, dataset_partition_dict):
        if not os.path.exists(config.FilePaths.dataset_partition_path):
            os.makedirs(config.FilePaths.dataset_partition_path)
        path = f"{config.FilePaths.dataset_partition_path}{self.dataset_name}_seed{self.seed}.pkl"
        pkl.dump(dataset_partition_dict, open(path, 'wb'))
        print(f"Saved the dataset partition dict to {path}")
        return


def generate_partition_dicts(args):
    partition_dict_obj = DataPartitionGenerator(args)
    for seed in range(1, args.seeds_num + 1):
        print(f"Creating dataset partition dict for seed {seed}")
        start_time = time()
        partition_dict_obj.create_dataset_partition_dict(seed)
        end_time = time()
        print(f"Elapsed time for seed {seed}: {end_time - start_time}")
        print(3 * '--------------------------')
    print("Done!")


def get_potnetial_neg_pairs(dataset_size_version, bkafi_dim, train_or_test, seed):
    file_name = get_file_name()
    file_name.replace('concatenation', 'division')
    # if train_or_test == 'train':
    #     file_name = file_name.replace('Operator', 'Train_Operator')
    blocking_results_dir = config.FilePaths.results_path + 'blocking_output/'
    blocking_results_path = (f"{blocking_results_dir}{file_name}_"
                             f"{dataset_size_version}_neg_samples_num2_vector_normalization_True_sdr_factor_False_"
                             f"bkafi_criterion=feature_importance_seed={seed}.joblib")
    print(f"\nLoading blocking results from {blocking_results_path}")
    blocking_dict = joblib.load(blocking_results_path)
    print(f"Loaded blocking results from {blocking_results_path}")
    neg_pairs = blocking_dict['neg_pairs'][bkafi_dim]
    return neg_pairs


def process_blocking_based_pairs(seed, neg_samples_num, cands_with_match_ids, potential_neg_pairs):
    np.random.seed(seed)
    pos_pairs = [(cand_id, cand_id) for cand_id in cands_with_match_ids]
    neg_pairs = potential_neg_pairs[neg_samples_num + 1]
    all_pairs = pos_pairs + neg_pairs
    np.random.shuffle(all_pairs)
    return all_pairs


def get_blocking_based_pairs(args, seed, train_or_test, dataset_partition_dict):
    local_test_ids_dict = {}
    neg_samples_list = args.train_neg_samples_list if train_or_test == 'train' else args.test_negative_samples_list
    # Only inject pairs for the dataset_size we actually ran blocking for.
    # If dataset_size_version is not set on args, fall back to all sizes.
    sizes_to_process = (
        [args.dataset_size_version]
        if hasattr(args, 'dataset_size_version') and args.dataset_size_version
        else ['small', 'medium', 'large']
    )
    for set_size in sizes_to_process:
        local_test_ids_dict[set_size] = {}
        if train_or_test == 'train':
            negative_sampling_pair_set = dataset_partition_dict['train']['negative_sampling'][set_size][2]
        else:
            negative_sampling_pair_set = dataset_partition_dict['test']['matching']['negative_sampling'][set_size][2]
        cands_with_match_ids = set([pair[0] for pair in negative_sampling_pair_set if pair[0] == pair[1]])
        potential_neg_pairs = get_potnetial_neg_pairs(set_size, args.bkafi_dim, train_or_test, seed)
        for neg_samples_num in neg_samples_list:
            local_test_ids_dict[set_size][neg_samples_num] = process_blocking_based_pairs(seed, neg_samples_num,
                                                                                          cands_with_match_ids,
                                                                                          potential_neg_pairs)
    return local_test_ids_dict


def add_blocking_based_mode_pairs(args):
    for seed in range(1, args.seeds_num + 1):
        # read the existing dataset partition dict

        dataset_partition_dict = pkl.load(open(f"data/dataset_partitions/{args.dataset_name}_seed{seed}.pkl", 'rb'))
        print(f"Loaded dataset partition dict for seed {seed} with blocking-based pairs")
        dataset_partition_dict['train']['blocking-based'] = get_blocking_based_pairs(args, seed, 'train',
                                                                                     dataset_partition_dict)
        dataset_partition_dict['test']['matching']['blocking-based'] = get_blocking_based_pairs(args, seed, 'test',
                                                                                                dataset_partition_dict)
        # save the updated dataset partition dict
        pkl.dump(dataset_partition_dict, open(f"data/dataset_partitions/{args.dataset_name}_seed{seed}.pkl", 'wb'))
        print(f"Updated dataset partition dict for seed {seed} with blocking-based pairs")
        print(3 * '--------------------------')
    return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset_name', type=str, default=config.Constants.dataset_name)
    parser.add_argument('--blocking_based_mode', type=bool, default=True)
    parser.add_argument('--seeds_num', type=int, default=config.Constants.seeds_num)
    parser.add_argument('--train_neg_samples_list', type=list, default=[2, 5])
    parser.add_argument('--test_negative_samples_list', type=list, default=[2, 5])
    parser.add_argument('--train_size_ratio_list', type=dict, default={"small": 0.1, "medium": 0.4, "large": 0.6})
    parser.add_argument('--test_size_ratio_list', type=dict, default={"small": 0.1, "medium": 0.5, "large": 1.0})
    parser.add_argument('--bkafi_dim', type=int, default=3)

    args = parser.parse_args()

    if args.blocking_based_mode:  # load existing partitions and add blocking-based partitions for the matching mode
        add_blocking_based_mode_pairs(args)
    else:
        generate_partition_dicts(args)

