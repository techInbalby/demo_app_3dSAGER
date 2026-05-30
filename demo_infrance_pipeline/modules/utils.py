import config
import os
import json
import datetime
import logging
import joblib
from pyproj import Proj, transform
from collections import defaultdict
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import tqdm
import clip
from PIL import Image
import torch
import pickle as pkl

device = "cuda" if torch.cuda.is_available() else "cpu"


def read_object_path_dict(dataset_config):
    return {'cands': dataset_config['cands_path'], 'index': dataset_config['index_path']}


def close_polygon(vertices):
    if vertices[0] != vertices[-1]:
        vertices.append(vertices[0])
    return vertices


def initialize_embedding_dict(cands_figs_path, index_figs_path, vit_model_name):
    embeddings_dict = {}
    for file_type, figs_path in zip(['cands', 'index'], [cands_figs_path, index_figs_path]):
        embeddings_path = os.path.join(figs_path, f'embeddings_{vit_model_name}.joblib')
        if os.path.exists(embeddings_path):
            print(f"Loading embeddings for {file_type} images")
            embeddings_dict[file_type] = joblib.load(embeddings_path)
        else:
            print(f"Initializing embeddings for {file_type} images. There is no embeddings file in {embeddings_path}")
            embeddings_dict[file_type] = {}
    return embeddings_dict


def get_clip_embedding(model, preprocess, image_path):
    image = preprocess(Image.open(image_path)).unsqueeze(0).to(device)
    with torch.no_grad():
        embedding = model.encode_image(image).float()
    return embedding.cpu().numpy()


def get_embedding_dict(embeddings_dict, object_dict, vit_model_name, cands_figs_path, index_figs_path):
    model, preprocess = clip.load(vit_model_name.replace('_', '/'), device=device)
    for file_type, figs_path in zip(['cands', 'index'], [cands_figs_path, index_figs_path]):
        images_to_embed = [obj_ind for obj_ind in object_dict[file_type].keys()
                           if obj_ind not in embeddings_dict[file_type].keys()]
        print(f"The number of objects in embeddings_dict: {len(embeddings_dict[file_type])}")
        print(f"The number of objects in object_dict: {len(object_dict[file_type])}")
        print(f"The number of objects in both: {len(set(embeddings_dict[file_type].keys()).intersection(set(object_dict[file_type].keys())))}")
        print(f"Generating embeddings for {len(images_to_embed)} {file_type} images")
        for obj_ind in tqdm.tqdm(images_to_embed):
            fig_path = os.path.join(figs_path, f'{obj_ind}.png')
            embeddings_dict[file_type][obj_ind] = get_clip_embedding(model, preprocess, fig_path)
        joblib.dump(embeddings_dict[file_type], os.path.join(figs_path, f'embeddings_{vit_model_name}.joblib'))
        print(f"The number of objects in embeddings_dict: {len(embeddings_dict[file_type])}")
        print(f"Saved embeddings {file_type} images in {os.path.join(figs_path, f'embeddings_{vit_model_name}.joblib')}")
    return embeddings_dict


def generate_png_figs_wrapper(object_dict, existing_images_dict, index_figs_path, cands_figs_path):
    new_generated_objects = {'cands': [], 'index': []}
    for file_type, figs_path in zip(['cands', 'index'], [cands_figs_path, index_figs_path]):
        for obj_ind, obj_data in tqdm.tqdm(object_dict[file_type].items()):
            if obj_ind in existing_images_dict[file_type]:
                continue
            generate_png_fig(obj_ind, obj_data['polygon_mesh'], figs_path)
            new_generated_objects[file_type].append(obj_ind)
        print(f"Generated {len(new_generated_objects[file_type])} {file_type} images")
    return new_generated_objects


def generate_figs_dir(dataset_name):
    dataset_config = json.load(open('dataset_configs.json'))[dataset_name]
    index_path = ''.join((dataset_config['index_path'], 'png_figs'))
    cands_path = ''.join((dataset_config['cands_path'], 'png_figs'))
    if not os.path.exists(index_path):
        os.makedirs(index_path)
    if not os.path.exists(cands_path):
        os.makedirs(cands_path)
    return index_path, cands_path


def get_existing_images_dict(index_path, cands_path):
    existing_images_dict = {}
    for file_type, dir_path in zip(['index', 'cands'], [index_path, cands_path]):
        existing_images_dict[file_type] = {f.split('.')[0] for f in os.listdir(dir_path) if f.endswith('.png')}
    return existing_images_dict


def get_embeddings_wrapper(dataset_name, object_dict, vit_model_name):
    index_figs_path, cands_figs_path = generate_figs_dir(dataset_name)
    existing_images_dict = get_existing_images_dict(index_figs_path, cands_figs_path)
    generate_png_figs_wrapper(object_dict, existing_images_dict, index_figs_path, cands_figs_path)
    embeddings_dict = initialize_embedding_dict(cands_figs_path, index_figs_path, vit_model_name)
    embeddings_dict = get_embedding_dict(embeddings_dict, object_dict, vit_model_name,
                                         cands_figs_path, index_figs_path)
    embeddings_dict = {file_type: {obj_ind: embeddings_dict[file_type][obj_ind]
                                   for obj_ind in object_dict[file_type].keys()}
                       for file_type in ['cands', 'index']}
    return embeddings_dict


def get_faiss_embeddings(embeddings_dict):
    mapping_dict = {'cands': {}, 'index': {}}
    faiss_embds = {}
    for file_type in ['cands', 'index']:
        embds = []
        for list_ind, obj_ind in enumerate(embeddings_dict[file_type].keys()):
            embds.append(embeddings_dict[file_type][obj_ind])
            mapping_dict[file_type][list_ind] = obj_ind
        embds = np.array(embds, dtype=np.float32)
        faiss_embds[file_type] = np.squeeze(embds, axis=1)
    return faiss_embds, mapping_dict


def find_min_coord(dim, polygon_mesh):
    return min([vertex[dim] for surface in polygon_mesh for vertex in surface])


def find_max_coord(dim, polygon_mesh):
    return max([vertex[dim] for surface in polygon_mesh for vertex in surface])


def generate_png_fig(obj_id, polygon_mesh, save_dir, margin=3):
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    for surface in polygon_mesh:
        poly = Poly3DCollection([surface], alpha=0.5, edgecolor='k')
        ax.add_collection3d(poly)

    x_min = find_min_coord(0, polygon_mesh) - margin
    y_min = find_min_coord(1, polygon_mesh) - margin
    z_min = find_min_coord(2, polygon_mesh) - margin
    x_max = find_max_coord(0, polygon_mesh) + margin
    y_max = find_max_coord(1, polygon_mesh) + margin
    z_max = find_max_coord(2, polygon_mesh) + margin

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_zlim(z_min, z_max)

    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_zticks([])
    ax.set_xlabel('')
    ax.set_ylabel('')
    ax.set_zlabel('')
    ax.grid(False)  # Disable grid
    ax.set_axis_off()  # Remove the background planes
    ax.set_rasterized(True)
    plt.savefig(os.path.join(save_dir, f'{obj_id}.png'), format='png', bbox_inches='tight',
                pad_inches=0, transparent=True)
    plt.close(fig)


def read_roads_from_json(roads_path):
    with open(roads_path, 'r', encoding="utf8") as f:
        data = json.load(f)
    return data


def convert_coords(coords):
    inProj = Proj(init='epsg:4326')
    outProj = Proj(init='epsg:32636')
    x, y = transform(inProj, outProj, coords[0], coords[1])
    return x, y


def str2bool(v):
    return v.lower() in ('true', '1', 'yes', 'y')


def get_file_name(blocking_method_arg=None):
    if config.Constants.file_name_suffix is not None:
        file_name_suffix = config.Constants.file_name_suffix
        operator = config.Features.operator
        blocking_method = blocking_method_arg if blocking_method_arg is not None else config.Blocking.blocking_method
        file_name_suffix = (f"{file_name_suffix}_Operator={operator}_"
                            f"Blocking={blocking_method}")
    else:
        file_name_suffix = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dataset_name = config.Constants.dataset_name
    dataset_config = json.load(open('dataset_configs.json'))[dataset_name]
    return f"{dataset_config['general_file_name']}_{file_name_suffix}"


def get_file_name_property_dict():
    if config.Constants.file_name_suffix is not None:
        file_name_suffix = config.Constants.file_name_suffix
    else:
        file_name_suffix = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dataset_name = config.Constants.dataset_name
    dataset_config = json.load(open('dataset_configs.json'))[dataset_name]
    return f"{dataset_config['general_file_name']}_{file_name_suffix}"


def define_logger():
    results_path = config.FilePaths.results_path
    file_name = get_file_name()
    if not os.path.exists(results_path[:-1]):
        os.makedirs(results_path[:-1])
    log_file_name = f"{results_path}Results_{file_name}.log"
    logging.basicConfig(filename=log_file_name, filemode="w", level=logging.INFO, format='%(levelname)s: %(message)s')
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.StreamHandler())
    return logger


def read_json(path, file_ind):
    file_path = ''.join([path, str(file_ind), '.city.json'])
    with open(file_path, 'r') as f:
        data = json.load(f)
    return data


def read_coordinates_from_file(data_path, is_roads=False):
    with open(data_path, encoding="utf8") as f:
        data = json.load(f)
    if is_roads:
        coordinate_lists = [elem['geometry']['coordinates'] for elem in data['features']]
    else:
        coordinate_lists = [elem['geometry']['coordinates'][0][0] for elem in data['features']]
    coordinate_lists = [coordinates[:-1] if coordinates[-1] == coordinates[0] else coordinates for
                        coordinates in coordinate_lists]
    return coordinate_lists


def load_object_dict(logger, path, object_name='object_dict'):
    logger.info(f"Loading object_dict from {path}")
    try:
        object_dict = joblib.load(path)
        logger.info(f"{object_name} was loaded successfully")
        return object_dict
    except Exception as e:
        logger.error(f"Error happened while loading {object_name}: {e}. Starting building object_dict and "
                     f"prep_object_dict...")
        return None


def print_config(logger, args):
    normalization = "True" if args.vector_normalization else "False"
    sdr_factor = "True" if args.sdr_factor else "False"

    logger.info(f"dataset: {args.dataset_name}")
    logger.info(f"seeds_num: {args.seeds_num}")
    logger.info(f"evaluation_mode: {args.evaluation_mode}")
    logger.info(f"blocking_method: {args.blocking_method}")
    logger.info(f"dataset_size_version: {args.dataset_size_version}")
    logger.info(f"vector_normalization: {normalization}")
    logger.info(f"sdr_factor: {sdr_factor}")
    logger.info(f"neg_samples_num: {args.neg_samples_num}")
    logger.info(f"bkafi_criterion: {args.bkafi_criterion}")
    logger.info('')
    logger.info(2*'===============================================')
    logger.info('')
    return

def get_feature_name_list(operator):
    feature_name_list = config.Features.object_properties
    if operator == 'division':
        return [f'{feature}_ratio' for feature in feature_name_list]
    elif operator == 'concatenation':
        final_feature_name_list = [f'{feature}_cand' for feature in feature_name_list]
        final_feature_name_list += [f'{feature}_index' for feature in feature_name_list]
        return final_feature_name_list
    else:
        raise ValueError(f"Operator {operator} is not supported")


def generate_final_result_csv(results_dict, args):
    evaluation_mode = args.evaluation_mode
    blocking_method = args.blocking_method
    dataset_size_version = args.dataset_size_version
    neg_samples_num = args.neg_samples_num
    vector_normalization = args.vector_normalization
    sdr_factor = args.sdr_factor
    bkafi_criterion = args.bkafi_criterion
    file_name = get_file_name(blocking_method)
    results_path = config.FilePaths.results_path + f"{evaluation_mode} csv files/"
    if not os.path.exists(results_path[:-1]):
        os.makedirs(results_path[:-1])
    if evaluation_mode == 'matching':
        generate_final_results_matching(results_dict, results_path, file_name, dataset_size_version, neg_samples_num,
                                        vector_normalization)
    elif evaluation_mode == 'blocking':
        generate_final_results_blocking(results_dict, results_path, file_name, blocking_method, dataset_size_version,
                                        neg_samples_num, vector_normalization, sdr_factor, bkafi_criterion)
    else:
        raise ValueError(f"Evaluation mode {evaluation_mode} is not supported")
    return


def generate_final_results_matching(results_dict, results_path, file_name, dataset_size_version, neg_samples_num,
                                    vector_normalization):
    vector_normalization_str = "True" if vector_normalization else "False"
    final_res_dict = defaultdict(dict)
    file_path = (f"{results_path}FinalResults_{file_name}_matching_{dataset_size_version}_"
                 f"neg_samples={neg_samples_num}_vector_normalization={vector_normalization_str}.csv")
    for model_name, model_dict in results_dict[1]['matching'].items():
        final_res_dict[model_name] = {}
        for metric in model_dict.keys():
            metric_res_list = []
            for seed in results_dict.keys():
                metric_res_list.append(results_dict[seed]['matching'][model_name][metric])
            final_res_dict[model_name][metric] = round(np.mean(metric_res_list), 3)
    df = pd.DataFrame.from_dict(final_res_dict, orient='index')
    df.to_csv(file_path)
    return


def generate_final_results_blocking(results_dict, results_path, file_name, blocking_method, dataset_size_version,
                                    neg_samples_num, vector_normalization, sdr_factor, bkafi_criterion):
    final_res_dict = defaultdict(dict)
    vector_normalization_str = "True" if vector_normalization else "False"
    sdr_factor_str = "True" if sdr_factor else "False"
    file_path = (f"{results_path}{file_name}_{dataset_size_version}_neg_samples_num{neg_samples_num}_"
                 f"vector_normalization={vector_normalization_str}_sdr_factor_{sdr_factor_str}_"
                 f"bkafi_criterion={bkafi_criterion}.csv")
    if "bkafi" in blocking_method:
        for bkafi_dim, bkafi_dict in results_dict[1]['blocking'].items():
            for cand_pairs_per_item, cand_dict in bkafi_dict.items():
                for metric, metric_val in cand_dict.items():
                    metric_res_list = []
                    for seed in results_dict.keys():
                        metric_res_list.append(results_dict[seed]['blocking'][bkafi_dim][cand_pairs_per_item][metric])
                    final_res_dict[f'{bkafi_dim}_{cand_pairs_per_item}'][metric] = round(np.mean(metric_res_list), 3)
    else:
        for cand_pairs_per_item in results_dict[1]['blocking'].keys():
            for metric in results_dict[1]['blocking'][cand_pairs_per_item].keys():
                metric_res_list = []
                for seed in results_dict.keys():
                    metric_res_list.append(results_dict[seed]['blocking'][cand_pairs_per_item][metric])
                final_res_dict[cand_pairs_per_item][metric] = round(np.mean(metric_res_list), 3)
    df = pd.DataFrame.from_dict(final_res_dict, orient='index')
    df.to_csv(file_path)
    return


def get_general_file_name(parser):
    if parser.general_file_name is None:
        general_file_name = config.Constants.file_name_suffix
    else:
        general_file_name = parser.general_file_name
    return general_file_name


def load_model(model_name, model_file_name):
    try:
        model = joblib.load(f'{model_file_name}')
        print(f"Model {model_name} was loaded successfully")
        print()
        return model['model'], model['feature_name_list']
    except Exception as e:
        print(f"Error happened while loading model {model_name}: {e}")
        return None


def load_feature_importance_dict(seed, logger):
    file_name = get_file_name()
    models_path = config.FilePaths.saved_models_path
    general_file_name = ''.join((file_name, '_feature_importance_dict'))
    feature_importance_file_name = f'{models_path}_{general_file_name}_seed={seed}.joblib'
    try:
        feature_importance_scores = joblib.load(feature_importance_file_name)
        logger.info(f"Feature importance scores were loaded successfully")
        return feature_importance_scores
    except Exception as e:
        logger.error(f"Error happened while loading feature importance scores: {e}")
        return None


def load_property_ratios(seed, logger):
    file_name = get_file_name()
    models_path = config.FilePaths.saved_models_path
    general_file_name = ''.join((file_name, '_property_ratios'))
    matching_pairs_property_ratios_file_name = f'{models_path}{general_file_name}_seed={seed}.joblib'
    try:
        matching_pairs_property_ratios = joblib.load(matching_pairs_property_ratios_file_name)
        logger.info(f"Matching pairs property ratios were loaded successfully")
        return matching_pairs_property_ratios
    except Exception as e:
        logger.error(f"Error happened while loading matching pairs property ratios: {e}")
        return None


def load_dataset_partition_dict(dataset_name, logger, seed):
    dir_path = config.FilePaths.dataset_partition_path
    logger.info(f"Loading dataset_partition_dict from {dir_path}{dataset_name}_seed{seed}.pkl")
    full_path = f"{dir_path}{dataset_name}_seed{seed}.pkl"
    dataset_partition_dict = pkl.load(open(full_path, 'rb'))
    logger.info(f"dataset_partition_dict was loaded successfully")
    return dataset_partition_dict


