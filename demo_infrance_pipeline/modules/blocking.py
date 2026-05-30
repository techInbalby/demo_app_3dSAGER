import config
from utils import *
import faiss
import numpy as np
from collections import defaultdict
from time import time
from scipy.spatial import KDTree
import tqdm
from sklearn.preprocessing import RobustScaler


class Blocker:
    def __init__(self, dataset_name, object_dict, property_dict, feature_importance_scores, property_ratios,
                 blocking_method, sdr_factor, bkafi_criterion, train_or_test):
        self.dataset_name = dataset_name
        self.blocking_method = blocking_method
        self.nn_param = config.Blocking.nn_param
        self.cand_pairs_per_item_list = config.Blocking.cand_pairs_per_item_list
        self.bkafi_dim_list = config.Blocking.bkafi_dim_list
        self.bkafi_criterion = bkafi_criterion
        self.nbits = config.Blocking.nbits
        self.object_dict = object_dict
        self.property_dict = property_dict
        self.feature_importance_scores = feature_importance_scores
        self.property_ratios = property_ratios
        self.sdr_factor = sdr_factor
        self.train_or_test = train_or_test
        self.centroids_dict = self._get_centroids()
        self.cands_mapping = {ind: orig_ind for ind, orig_ind in enumerate(self.object_dict['cands'].keys())}
        self.index_mapping = {ind: orig_ind for ind, orig_ind in enumerate(self.object_dict['index'].keys())}
        self.blocking_method_dict = self._get_blocking_method_dict()
        self.nn_dict, self.dist_dict, self.blocking_execution_time = self._run_blocking()
        self.pos_pairs_dict, self.neg_pairs_dict = self._get_candidate_pairs()

    def _get_centroids(self):
        centroids_dict = defaultdict(dict)
        for objects_type in ['cands', 'index']:
            centroids_dict[objects_type] = {obj_ind: obj_data['centroid'] for obj_ind, obj_data in
                                            self.object_dict[objects_type].items()}
            centroids_dict[objects_type] = dict(sorted(centroids_dict[objects_type].items()))
        return centroids_dict

    @staticmethod
    def _get_ind_mapping(centroids):
        return {ind: orig_ind for ind, orig_ind in enumerate(sorted(centroids))}

    def _get_blocking_method_dict(self):
        blocking_method_dict = {'bkafi': self._run_bkafi,
                                'ViT-B_32': self._run_vit,
                                'ViT-L_14': self._run_vit,
                                'centroid': self._run_exhaustive,
                                'centroid_with_transform': self._run_exhaustive,
                                # 'lsh': self._run_lsh,
                                # 'kdtree': self._run_kdtree,
                                }
        return blocking_method_dict

    def _run_blocking(self):
        nn_dict, dists_dict, blocking_execution_time = self.blocking_method_dict[self.blocking_method]()
        return nn_dict, dists_dict, blocking_execution_time

    def _run_exhaustive(self):
        nn_dict, dists_dict = {}, {}
        cands_centroids_np = np.array(list(self.centroids_dict['cands'].values()), dtype=np.float32)
        index_centroids_np = np.array(list(self.centroids_dict['index'].values()), dtype=np.float32)
        if self.blocking_method == 'centroid_with_transform':
            cands_centroids_np = self._transform_centroids(cands_centroids_np, index_centroids_np)
        index = faiss.IndexFlatL2(index_centroids_np.shape[1])
        index.add(index_centroids_np)
        start = time()
        for i, query_centroid in enumerate(cands_centroids_np):
            dists, neighbors = index.search(np.array([query_centroid]), self.nn_param)
            nn_dict[self.cands_mapping[i]] = [self.index_mapping[ind] for ind in neighbors.flatten()]
            dists_dict[self.cands_mapping[i]] = [dist for dist in dists.flatten()]
        end = time()
        return nn_dict, dists_dict, round(end - start, 3)

    def _transform_centroids(self, cands_centroids_np, index_centroids_np):
        index_mean = np.mean(index_centroids_np, axis=0)
        cands_mean = np.mean(cands_centroids_np, axis=0)
        index_centered = index_centroids_np - index_mean
        cands_centered = cands_centroids_np - cands_mean
        H = np.dot(index_centered, cands_centered.T)
        U, S, Vt = np.linalg.svd(H)
        rotation_matrix = np.dot(Vt.T, U.T)
        if np.linalg.det(rotation_matrix) < 0:
            Vt[-1, :] *= -1
            rotation_matrix = np.dot(Vt.T, U.T)
        translation_vector = cands_mean - np.dot(index_mean, rotation_matrix)
        scaling_factor = np.linalg.norm(cands_centered) / np.linalg.norm(index_centered)
        cands_centroids_np = scaling_factor * np.dot(index_centroids_np, rotation_matrix) + translation_vector
        return cands_centroids_np

    def _run_lsh(self):
        nn_dict, dists_dict = {}, {}
        cands_centroids_np = np.array(list(self.centroids_dict['cands'].values()), dtype=np.float32)
        index_centroids_np = np.array(list(self.centroids_dict['index'].values()), dtype=np.float32)
        index = faiss.IndexLSH(index_centroids_np.shape[1], self.nbits)
        index.add(index_centroids_np)
        for i, query_centroid in enumerate(cands_centroids_np):
            dists, neighbors = index.search(np.array([query_centroid]), self.nn_param)
            nn_dict[self.cands_mapping[i]] = [self.index_mapping[ind] for ind in neighbors.flatten()]
            dists_dict[self.cands_mapping[i]] = [dist for dist in dists.flatten()]
        return nn_dict, dists_dict

    def _run_kdtree(self, search_dict):
        robust_scaler = RobustScaler()
        nn_dict, dists_dict = {}, {}
        cands_vectors_np = np.array(list(search_dict['cands'].values()), dtype=np.float32)
        index_vectors_np = np.array(list(search_dict['index'].values()), dtype=np.float32)
        cands_vectors_np = robust_scaler.fit_transform(cands_vectors_np)
        index_vectors_np = robust_scaler.transform(index_vectors_np)
        index = KDTree(index_vectors_np)
        dists, neighbors = index.query(cands_vectors_np, self.nn_param)
        for i, query_centroid in enumerate(cands_vectors_np):
            nn_dict[self.cands_mapping[i]] = [self.index_mapping[ind] for ind in neighbors[i]]
            dists_dict[self.cands_mapping[i]] = [round(dist, 3) for dist in dists[i]]
        return nn_dict, dists_dict

    def _run_bkafi(self):
        if self.train_or_test == 'train':
            return self._run_bkafi_train()
        nn_dict, dists_dict = {}, {}
        model_name = config.Models.blocking_model
        execution_time_dict = {}
        for bkafi_dim in self.bkafi_dim_list:
            target_blocking_features = self._get_target_blocking_features(model_name, bkafi_dim)
            bkafi_dict = self._get_bkafi_dict(target_blocking_features)
            start_time = time()
            nn_dict[bkafi_dim], dists_dict[bkafi_dim] = self._run_kdtree(bkafi_dict)
            end_time = time()
            execution_time_dict[bkafi_dim] = round(end_time - start_time, 3)
        return nn_dict, dists_dict, execution_time_dict

    def _get_target_blocking_features(self, model_name, bkafi_dim):
        if self.bkafi_criterion == 'std':
            target_blocking_features = {prop: prop_information for prop, prop_information in
                                        list(self.property_ratios.items())[:bkafi_dim]}
        else:  # feature_importance
            target_blocking_features = {feature: self.property_ratios[feature.split('_ratio')[0]]
                                        for feature, _ in self.feature_importance_scores[model_name][:bkafi_dim]}
        return target_blocking_features

    def _run_bkafi_train(self):
        nn_dict, dists_dict = {}, {}
        model_name = config.Models.blocking_model
        bkafi_dim = len(self.feature_importance_scores[model_name])
        target_blocking_features = {feature: self.property_ratios[feature.split('_ratio')[0]]
                                    for feature, _ in self.feature_importance_scores[model_name][:bkafi_dim]}
        bkafi_dict = self._get_bkafi_dict(target_blocking_features)
        nn_dict[bkafi_dim], dists_dict[bkafi_dim] = self._run_kdtree(bkafi_dict)
        return nn_dict, dists_dict, None

    def _get_bkafi_dict(self, target_blocking_features):
        bkafi_dict = defaultdict(dict)
        factor_dict = self._get_bkafi_factor_dict(target_blocking_features)
        for obj_type in ['cands', 'index']:
            for obj_ind in self.object_dict[obj_type].keys():
                bkafi_dict[obj_type][obj_ind] = []
                for feature in target_blocking_features:
                    property_val = self.property_dict[feature.split('_ratio')[0]][obj_type][obj_ind]
                    bkafi_dict[obj_type][obj_ind].append(property_val * factor_dict[obj_type][feature])
                bkafi_dict[obj_type][obj_ind] = np.array(bkafi_dict[obj_type][obj_ind])
        return bkafi_dict

    def _get_bkafi_factor_dict(self, target_blocking_features):
        factor_dict = defaultdict(dict)
        if self.sdr_factor:
            factor_dict['cands'] = {feature: target_blocking_features[feature]['mean']
                                    for feature in target_blocking_features}
        else:
            factor_dict['cands'] = {feature: 1.0 for feature in target_blocking_features}
        factor_dict['index'] = {feature: 1.0 for feature in target_blocking_features}
        return factor_dict

    def _run_vit(self):
        vit_model_name = self.blocking_method
        embeddings_dict = get_embeddings_wrapper(self.dataset_name, self.object_dict, vit_model_name)
        faiss_embds_dict, mapping_dict = get_faiss_embeddings(embeddings_dict)
        dim = faiss_embds_dict['index'].shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(faiss_embds_dict['index'])
        nn_dict, dists_dict = {}, {}
        start_time = time()
        for i, cand_query in enumerate(faiss_embds_dict['cands']):
            query = cand_query.reshape(1, -1)
            dists, neighbors = index.search(query, self.nn_param)
            nn_dict[mapping_dict['cands'][i]] = [mapping_dict['index'][ind] for ind in neighbors[0]]
            dists_dict[mapping_dict['cands'][i]] = [dist for dist in dists[0]]
        end_time = time()
        return nn_dict, dists_dict, round(end_time - start_time, 3)


    @staticmethod
    def _get_start_ind4nn(nn_inds, cand_ind):
        if nn_inds[0] + 1 == cand_ind:
            return 1
        else:
            return 0

    def _get_candidate_pairs(self):
        if self.train_or_test == 'train':
            cand_pairs_per_item_list = [self.cand_pairs_per_item_list[0]]
        else:
            cand_pairs_per_item_list = self.cand_pairs_per_item_list
        if 'bkafi' in self.blocking_method:
            return self._get_candidate_pairs_bkafi(cand_pairs_per_item_list)
        else:
            return self._get_candidate_pairs_not_bkafi(cand_pairs_per_item_list)

    def _get_candidate_pairs_bkafi(self, cand_pairs_per_item_list):
        pos_pairs_dict, neg_pairs_dict = defaultdict(dict), defaultdict(dict)
        for bkafi_dim in self.nn_dict.keys():
            for list_ind, cand_pairs_per_item in enumerate(cand_pairs_per_item_list):
                if list_ind == 0:
                    pos_pairs_dict[bkafi_dim][cand_pairs_per_item] = []
                    neg_pairs_dict[bkafi_dim][cand_pairs_per_item] = []
                else:
                    previous_val = self.cand_pairs_per_item_list[list_ind - 1]
                    pos_pairs_dict[bkafi_dim][cand_pairs_per_item] = pos_pairs_dict[bkafi_dim][previous_val].copy()
                    neg_pairs_dict[bkafi_dim][cand_pairs_per_item] = neg_pairs_dict[bkafi_dim][previous_val].copy()
                for cand_ind, nn_inds in self.nn_dict[bkafi_dim].items():
                    start_ind = self.cand_pairs_per_item_list[list_ind - 1] if list_ind > 0 else 0
                    # cand_ind = str(cand_ind)
                    for nn_ind in nn_inds[start_ind:cand_pairs_per_item]:
                        if cand_ind == nn_ind:
                            pos_pairs_dict[bkafi_dim][cand_pairs_per_item].append((cand_ind, nn_ind))
                        else:
                            neg_pairs_dict[bkafi_dim][cand_pairs_per_item].append((cand_ind, nn_ind))
        return pos_pairs_dict, neg_pairs_dict


    def _get_candidate_pairs_not_bkafi(self, cand_pairs_per_item_list):
        pos_pairs_dict, neg_pairs_dict = dict(), dict()
        for list_ind, cand_pairs_per_item in enumerate(cand_pairs_per_item_list):
            if list_ind == 0:
                pos_pairs_dict[cand_pairs_per_item] = []
                neg_pairs_dict[cand_pairs_per_item] = []
            else:
                previous_val = self.cand_pairs_per_item_list[list_ind - 1]
                pos_pairs_dict[cand_pairs_per_item] = pos_pairs_dict[previous_val].copy()
                neg_pairs_dict[cand_pairs_per_item] = neg_pairs_dict[previous_val].copy()
            for cand_ind, nn_inds in self.nn_dict.items():
                start_ind = self.cand_pairs_per_item_list[list_ind - 1] if list_ind > 0 else 0
                cand_ind = str(cand_ind)
                for nn_ind in nn_inds[start_ind:cand_pairs_per_item]:
                    if cand_ind == nn_ind:
                        pos_pairs_dict[cand_pairs_per_item].append((cand_ind, nn_ind))
                    else:
                        neg_pairs_dict[cand_pairs_per_item].append((cand_ind, nn_ind))
        return pos_pairs_dict, neg_pairs_dict

    def _get_local_mapping_dict(self):
        """
        This function returns the mapping dictionary of the objects in the current object_dict.
        The mapping is required because in some datasets object file names are recognized as integers and in some
        datasets as uids
        """
        local_mapping_dict = defaultdict(dict)
        if 'mapping_dict' not in self.object_dict.keys():
            local_mapping_dict['cands'] = {ind: ind for ind in self.object_dict['cands'].keys()}
            local_mapping_dict['index'] = {ind: ind for ind in self.object_dict['index'].keys()}
        else:
            local_mapping_dict['cands'] = self.object_dict['mapping_dict']['cands']
            local_mapping_dict['index'] = self.object_dict['mapping_dict']['index']
        return local_mapping_dict

