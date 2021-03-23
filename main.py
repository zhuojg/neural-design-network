import tensorflow as tf
import numpy as np
import os
import json
import random
import math
from PIL import Image, ImageDraw
import argparse
import configparser
import datetime

from tensorflow.python.autograph.utils.ag_logging import _output_to_stdout

from models.pipeline import NeuralDesignNetwork


parser = argparse.ArgumentParser()
parser.add_argument('--save', action='store_true')
parser.add_argument('--train', action='store_true')
parser.add_argument('--test', action='store_true')
parser.add_argument('--part', choices=['relation', 'generation', 'refinement', 'all'])
parser.add_argument('--checkpoint_path', default=None)
parser.add_argument('--output_dir', default=None)
args = parser.parse_args()

config_parser = configparser.ConfigParser()
config_parser.read('config.ini')
config = config_parser['NDN']

category_list = ['image', 'text', 'background', 'header', 'text over image', 'header over image']
pos_relation_list = ['surrounding', 'inside', 'left of', 'above', 'right of', 'below', 'unknown']
size_relation_list = ['bigger', 'smaller', 'same', 'unknown']


if __name__ == '__main__':
    current_time = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')

    if args.train:
        checkpoint_dir = os.path.join(config['checkpoint_dir'], current_time)
        sample_dir = os.path.join(config['sample_dir'], current_time)
        train_sample_dir = os.path.join(sample_dir, 'train')
        test_sample_dir = os.path.join(sample_dir, 'test')

        log_dir = os.path.join(config['log_dir'], current_time)
        
        if args.save:
            if not os.path.exists(checkpoint_dir):
                os.makedirs(checkpoint_dir)

            if not os.path.exists(sample_dir):
                os.makedirs(train_sample_dir)
                os.makedirs(test_sample_dir)
                
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
        
        training_config = {
            'checkpoint_dir': checkpoint_dir,
            'data_dir': config['data_dir'],
            'test_data_dir': config['test_data_dir'],
            'log_dir': log_dir,
            'batch_size': config.getint('batch_size'),
            'learning_rate': config.getfloat('learning_rate'),
            'beta_1': config.getfloat('beta_1'),
            'beta_2': config.getfloat('beta_2'),
            'lambda_cls': config.getfloat('lambda_cls'),
            'lambda_kl_2': config.getfloat('lambda_kl_2'),
            'mask_rate': config.getfloat('mask_rate'),
            'max_iteration_number': int(config.getfloat('max_iteration_number')),
            'checkpoint_every': int(config.getfloat('checkpoint_every')), 
            'checkpoint_max_to_keep': config.getint('checkpoint_max_to_keep')
        }
        
        model = NeuralDesignNetwork(
            category_list=category_list, 
            pos_relation_list=pos_relation_list,
            size_relation_list=size_relation_list,
            config=training_config,
            save=args.save, 
            training=True)

        if args.part == 'relation':
            model.train_relation(config=training_config)
        
        if args.part == 'generation':
            pass

        if args.part == 'refinement':
            pass

    if args.test:
        # assert args.checkpoint_path and args.output_dir

        # if not os.path.exists(args.output_dir):
        #     os.makedirs(args.output_dir)

        assert args.checkpoint_path
        
        model = NeuralDesignNetwork(
            category_list=category_list,
            pos_relation_list=pos_relation_list,
            size_relation_list=size_relation_list,
            config={},
            save=args.save, 
            training=False
        )

        model.ckpt.restore(args.checkpoint_path)
        model.test_relation(config={})
