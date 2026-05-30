

class FilePaths:
    results_path = "results/"
    saved_models_path = "saved_model_files/"
    object_dict_path = "data/object_dicts/"
    dataset_dict_path = "data/dataset_dicts/"
    property_dict_path = "data/property_dicts/"
    dataset_partition_path = "data/dataset_partitions/"


class Constants:
    dataset_name = "Hague"  # "Hague", "delivery3", "bo_em", "gpkg"
    synthetic_folder_name = "example"  # Relevant only if dataset_name is "synthetic"
    evaluation_mode = "matching"  # "blocking", "matching"
    dataset_size_version = 'medium'  # 'small', 'medium', 'large'
    matching_cands_generation = 'negative_sampling'  # 'negative_sampling', 'blocking-based'
    neg_samples_num = 2  # 2, 5
    seeds_num = 1
    train_ratio = 0.6
    val_ratio = 0.2
    test_ratio = 1 - train_ratio - val_ratio
    max_ratio_val = 1000  # Avoid infinity values
    load_object_dict = False  # Must be False when disaster simulation is active
    save_object_dict = True  # Cache object dict to avoid reloading from disk
    load_train_items = False  # Load existing preparatory items
    save_property_dict = True  # Cache property dict to avoid recomputing
    load_property_dict = False  # Load the properties dictionary
    save_dataset_dict = True  # Cache dataset dict to avoid recomputing
    load_dataset_dict = False  # load existing dataset dictionary
    file_name_suffix = 'allmodels_v1'  # Stable suffix so model files are reusable across runs
    max_grid_cells = 50  # None = all cells; 50 = medium run on 8GB RAM


class TrainingPhase:
    training_ratio = 0.5  # Number of positive samples
    neg_pairs_ratio = 4  # Number of negative samples per positive sample
    run_preparatory_phase = True  # If False, the preparatory phase will not be run


class Features:
    knn_buildings = 0  # Number of nearest buildings to consider
    knn_roads = 0  # Number of nearest roads to consider
    operator = 'division'  # 'division', 'concatenation'
    object_properties = ["bounding_box_width", "bounding_box_length", "area", "perimeter", "perimeter_ind",
                         "volume", "convex_hull_area", "convex_hull_volume", "ave_centroid_distance", "height_diff",
                         "num_floors", "axes_symmetry", "compactness_2d", "compactness_3d", "density",
                         "elongation", "shape_ind", "hemisphericality", "fractality", "cubeness", "circumference",
                         "aligned_bounding_box_width", "aligned_bounding_box_length", "aligned_bounding_box_height",
                         "num_vertices"]

    # object_properties = ["circumference", "density", "convex_hull_area"]
    normalization = 'log_transform'  # 'log_transform', None
    neighborhood = []
    roads = []


class Blocking:
    blocking_method = 'bkafi'  # 'bkafi', 'bkafi_without_SDR', 'ViT-B_32', 'ViT-L_14', 'centroid'
                                # 'coordinates', 'coordinates_transformed'
    cand_pairs_per_item_list = [i for i in range(1, 21)]  # total number of neighbors per each candidate object
    nn_param = cand_pairs_per_item_list[-1] + 1  # number of nearest neighbors to retrieve as candidates
    nbits = 10  # number of bits to use for LSH
    # bkafi_dim_list = [dim for dim in range(1, len(Features.object_properties))] # Number of important features to
    # use for blocking (for the bkafi method)
    bkafi_dim_list = [dim for dim in range(1, len(Features.object_properties))]  # Number of important features to use
    dist_threshold = None  # Define it as a hyperparameter or in a flexible manner
    sdr_factor = False  # If True, the SDR factor will be used in the blocking method
    bkafi_criterion = 'feature_importance'  # 'std', 'feature_importance'
    # Neighborhood-aware negative sampling
    neighborhood_radius = 500.0   # meters (EPSG:7415) — radius for spatial negative sampling
    neighborhood_neg_ratio = 0.7  # fraction of negatives drawn from within-radius neighbors vs. random


class DataPartition:
    grid_cell_size = 500.0   # meters (EPSG:7415) — side length of each spatial grid cell
    train_ratio = 0.6        # fraction of grid cells assigned to training
    contiguous_test = True   # If True, test cells form a spatially contiguous region (BFS from
                             # a corner) so the demo app covers a coherent train-only area.


class DisasterSimulation:
    enabled = True
    # CRS simulation — random rotation + large translation applied globally to all cands
    crs_simulation = True    # simulate unknown CRS (no absolute reference)
    # Damage simulation — per-building random height reduction
    damage_probability = 0.8    # fraction of cand buildings to damage
    min_damage_factor = 0.3     # minimum remaining height fraction (0.3 = 70% collapsed)
    max_damage_factor = 0.95    # maximum remaining height fraction (near-undamaged)


class Alignment:
    enabled = True
    min_anchor_pairs = 3          # minimum high-confidence matches required to attempt alignment
    confidence_threshold = 0.8    # geometric classifier score threshold for anchor selection
    max_residual_threshold = 50.0 # meters — reject alignment if mean anchor error exceeds this
    alpha = 0.5                   # weight: 1.0 = geometric score only, 0.0 = spatial score only
    output_crs = "EPSG:7415"      # index dataset CRS — output aligned CityJSON in this CRS
    use_ransac = True             # use RANSAC to find robust transform instead of plain SVD
    ransac_iterations = 1000      # number of RANSAC trials
    ransac_inlier_threshold = 10.0  # meters — anchor is inlier if residual < this after applying R, t
    spatial_sigma = 3.0           # meters — Gaussian decay length for post-alignment spatial score.
                                  # spatial(d) = exp(-d²/(2·σ²)). σ ≈ median true-match residual;
                                  # σ=3 m gives spatial(0)=1, spatial(3)=0.61, spatial(10)≈0.004.
    post_align_knn_cutoff = 7.0   # meters — for --post-align-blocking mode in demo/inference.py.
                                  # After alignment succeeds, replace BKAFI pool with per-cand 1-NN
                                  # against full index; accept iff post-alignment distance ≤ cutoff.


class Models:
    load_trained_models = False
    cv = 3
    model_to_use = 'XGBClassifier'  # Used only for predict.py and feature_importances.py
    model_list = ['XGBClassifier', 'GradientBoostingClassifier', 'BaggingClassifier',
                  'RandomForestClassifier', 'AdaBoostClassifier', 'MLPClassifier']
    blocking_model = 'RandomForestClassifier'  # Used only for blocking and for advanced evaluation
    params_dict = {
                    'RandomForestClassifier': {"n_estimators": [50],
                                               "max_depth": [5],
                                               "min_samples_split": [2],
                                               "max_features": ["sqrt"]},

                    'SVC': {'C': [0.1, 0.5],
                           'kernel': ['rbf'],
                           'gamma': ['scale'],
                           'degree': [2]
                            },

                    'LogisticRegression': {'solver': ['lbfgs', 'saga'],
                                          'multi_class': ['auto'],
                                          'C': [0.01, 0.1, 1]
                                           },

                    'MLPClassifier': {'hidden_layer_sizes': [(64, 32)],
                                      'activation': ['relu'],
                                      'solver': ['adam'],
                                      'batch_size': [16],
                                      'max_iter': [500],
                                      'early_stopping': [True],
                                      'n_iter_no_change': [20],
                                      },

                    'AdaBoostClassifier': {'n_estimators': [100],
                                           'learning_rate': [0.1],
                                           'algorithm': ['SAMME']
                                           },

                    'GradientBoostingClassifier': {'loss': ['log_loss'],
                                                   'learning_rate': [0.1],
                                                   'n_estimators': [100],
                                                   'max_depth': [3],
                                                   'min_samples_split': [3],
                                                   'max_features': ['sqrt']
                                                   },

                    'BaggingClassifier': {'n_estimators': [50],
                                          'max_samples': [0.8],
                                          'max_features': [0.8],
                                          'bootstrap': [True]
                                          },

                    'XGBClassifier': {'max_depth': [4],
                                      'objective': ['binary:logistic'],
                                      'learning_rate': [0.1],
                                      'n_estimators': [100],
                                      'gamma': [0],
                                      'tree_method': ['hist'],
                                      'n_jobs': [4],
                                      }
    }



