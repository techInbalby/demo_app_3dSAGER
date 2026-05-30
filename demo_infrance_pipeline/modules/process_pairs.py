from scipy.spatial import ConvexHull
import numpy as np
import faiss
import config
from shapely import Polygon, LineString
from scipy.spatial import KDTree
from collections import defaultdict
from utils import get_feature_name_list


class PairProcessor:
    def __init__(self, property_dict, pairs_list):
        self.max_ratio_val = config.Constants.max_ratio_val
        self.operator = config.Features.operator
        self.property_dict = property_dict
        self.pairs_list = pairs_list
        self.feature_name_list = get_feature_name_list(self.operator)
        self.feature_funcs_dict = self._get_feature_dict()
        self.feature_to_att_dict = self._get_feature_to_att_dict()
        self.feature_vec = self._create_feature_vectors()

    def _get_feature_dict(self):
        if self.operator == 'division':
            return {feature: self._get_ratio for feature in self.feature_name_list}
        elif self.operator == 'concatenation':
            return {feature: self._get_feature_for_conc for feature in self.feature_name_list}
        else:
            raise ValueError(f"Operator {self.operator} is not supported")

    def _get_feature_to_att_dict(self):
        if self.operator == 'division':
            return {feature: feature.split('_ratio')[0] for feature in self.feature_name_list}
        elif self.operator == 'concatenation':
            feature_to_att_dict = {feature: feature.split('_cand')[0] for feature in
                                   self.feature_name_list if 'cand' in feature}
            feature_to_att_dict.update({feature: feature.split('_index')[0] for feature in
                                        self.feature_name_list if 'index' in feature})
            return feature_to_att_dict
        else:
            raise ValueError(f"Operator {self.operator} is not supported")

    def _get_feature_vec(self, pair):
        feature_vec = []
        for feature_name in self.feature_name_list:
            try:
                if self.operator == 'division':
                    feature_vec.append(min(self.max_ratio_val,
                                           self.feature_funcs_dict[feature_name](feature_name, pair)))
                elif self.operator == 'concatenation':
                    feature_vec.append(self.feature_funcs_dict[feature_name](feature_name, pair))
            except Exception as e:
                print(f'Error in feature {feature_name} for pair {pair}: {type(e).__name__}: {e}')
                feature_vec.append(0.0)
        return feature_vec

    def _create_feature_vectors(self):
        feature_vectors = []
        for pair in self.pairs_list:
            feature_vectors.append(self._get_feature_vec(pair))
        return feature_vectors

    def _get_ratio(self, feature_name, pair):
        att_name = self.feature_to_att_dict[feature_name]
        cand_att_val = self.property_dict[att_name]['cands'][pair[0]]
        index_att_val = self.property_dict[att_name]['index'][pair[1]]
        if index_att_val == 0:
            return self.max_ratio_val
        return round(cand_att_val / index_att_val, 3)

    def _get_feature_for_conc(self, feature_name, pair):
        att_name = self.feature_to_att_dict[feature_name]
        if 'cand' in feature_name:
            return self.property_dict[att_name]['cands'][pair[0]]
        elif 'index' in feature_name:
            return self.property_dict[att_name]['index'][pair[1]]
        else:
            raise ValueError(f"Feature {feature_name} is not supported")

