"""
config_demo.py

Path and runtime overrides for the standalone demo bundle.

Imports the base `config` from `modules/` and mutates it in place so the
inference pipeline reads from ./saved_models/ and writes to ./output/.

inference.py imports this module FIRST so the overrides land before any
project module reads from config.
"""

import os
import sys

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(DEMO_DIR, 'modules')
if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)

import config

CACHE_DIR = os.path.join(DEMO_DIR, 'output', 'cache') + os.sep
RESULTS_DIR = os.path.join(DEMO_DIR, 'output', 'results') + os.sep
INTERMEDIATE_DIR = os.path.join(DEMO_DIR, 'output', 'intermediate') + os.sep

config.FilePaths.results_path           = RESULTS_DIR
config.FilePaths.saved_models_path      = os.path.join(DEMO_DIR, 'saved_models') + os.sep
config.FilePaths.object_dict_path       = CACHE_DIR
config.FilePaths.dataset_dict_path      = CACHE_DIR
config.FilePaths.property_dict_path     = CACHE_DIR
config.FilePaths.dataset_partition_path = CACHE_DIR

# Restrict BKAFI to a single (dim, neighbors) configuration for inference.
# Training iterates over all combinations; inference needs just one.
DEMO_BKAFI_DIM = len(config.Features.object_properties) - 1  # use the top-(N-1) most-important features
DEMO_NN_COUNT  = 30                                          # candidate index buildings per cand
config.Blocking.bkafi_dim_list           = [DEMO_BKAFI_DIM]
config.Blocking.cand_pairs_per_item_list = [DEMO_NN_COUNT]
config.Blocking.nn_param                 = DEMO_NN_COUNT + 1

config.Constants.max_grid_cells     = None
config.Constants.save_object_dict   = False
config.Constants.save_property_dict = False
config.Constants.save_dataset_dict  = False

# Alignment tuning for the demo regime, where the classifier surfaces many
# geometric look-alikes as high-score anchors. With a low-precision anchor
# pool we need more RANSAC iterations to find a 3-sample of true positives,
# and we want the rescoring to lean on the spatial term (centroid distance
# after alignment) rather than the noisy geometric score.
config.Alignment.ransac_iterations = 5000
config.Alignment.alpha             = 0.3   # 0 = spatial only, 1 = geometric only. With Gaussian
                                            # spatial (σ=3 m), 0.30 is the empirically-best mix on
                                            # the demo data: spatial dominates ranking, classifier
                                            # provides a small bias toward shape-consistent pairs.

for d in (CACHE_DIR, RESULTS_DIR, INTERMEDIATE_DIR):
    os.makedirs(d, exist_ok=True)
