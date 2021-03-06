from uuid import NAMESPACE_X500
from models.refinement import NDNRefinement
import tensorflow as tf
from tensorflow import keras

import tensorflow_probability as tfp

from models.relation import NDNRelation
from models.generation import NDNGeneration
from models.refinement import NDNRefinement

import os
import json
import math
import random
from PIL import Image, ImageDraw


class NeuralDesignNetwork:
    def __init__(self, category_list, pos_relation_list, size_relation_list, config, save=False, training=True):
        super(NeuralDesignNetwork, self).__init__()

        self.save = save
        self.training = training
        self.config = config
        self.category_list = category_list
        self.pos_relation_list = pos_relation_list
        self.size_relation_list = size_relation_list

        # construct vocab
        self.vocab = {
            'object_name_to_idx': {},
            'pos_pred_name_to_idx': {},
            # 'size_pred_name_to_idx': {}
        }

        self.vocab['object_name_to_idx']['__image__'] = 0
        self.vocab['pos_pred_name_to_idx']['__in_image__'] = 0
        # self.vocab['size_pred_name_to_idx']['__in_image__'] = 0

        for idx, item in enumerate(category_list):
            self.vocab['object_name_to_idx'][item] = idx + 1
        
        for idx, item in enumerate(pos_relation_list):
            self.vocab['pos_pred_name_to_idx'][item] = idx + 1
        
        # for idx, item in enumerate(size_relation_list):
        #     self.vocab['size_pred_name_to_idx'][item] = idx + 1

        # build GCN as described in supplementary material
        self.pos_relation = NDNRelation(category_list=self.vocab['object_name_to_idx'].keys(), relation_list=self.vocab['pos_pred_name_to_idx'].keys())
        # self.size_relation = NDNRelation(category_list=self.vocab['object_name_to_idx'].keys(), relation_list=self.vocab['size_pred_name_to_idx'])

        self.generation = NDNGeneration()
        self.refinement = NDNRefinement()

        self.obj_embedding = keras.layers.Embedding(input_dim=len(self.vocab['object_name_to_idx']), output_dim=64)
        self.pos_pred_embedding = keras.layers.Embedding(input_dim=len(self.vocab['pos_pred_name_to_idx']), output_dim=64)
        # self.size_pred_embedding = keras.layers.Embedding(input_dim=len(self.vocab['size_pred_name_to_idx']), output_dim=64)
        # define optimizer
        self.relation_optimizer = keras.optimizers.Adam(learning_rate=config['learning_rate'], beta_1=config['beta_1'], beta_2=config['beta_2'])
        self.generation_optimizer = keras.optimizers.Adam(learning_rate=config['learning_rate'], beta_1=config['beta_1'], beta_2=config['beta_2'])
        self.refinement_optimizer = keras.optimizers.Adam(learning_rate=config['learning_rate'], beta_1=config['beta_1'], beta_2=config['beta_2'])

        # define ckpt manger
        self.ckpt = tf.train.Checkpoint(
            obj_embedding=self.obj_embedding,
            pos_pred_embedding=self.pos_pred_embedding,
            # size_pred_embedding=self.size_pred_embedding,
            pos_relation=self.pos_relation,
            # size_relation=self.size_relation,
            generation=self.generation,
            refinement=self.refinement,
            relation_optimizer=self.relation_optimizer,
            generation_optimizer=self.generation_optimizer,
            refinement_optimizer=self.refinement_optimizer
        )

        # define training parameters
        self.iter_cnt = 0

    @staticmethod
    def KL_divergence(mu, var, mu_prior, var_prior):
        mu = tf.concat(mu, axis=0)
        var = tf.concat(var, axis=0)
        mu_prior = tf.zeros_like(mu)
        var_prior = tf.ones_like(var)
        sigma = tf.math.exp(.5 * var)
        sigma_prior = tf.math.exp(.5 * var_prior)
        KL_loss = tf.math.log(sigma_prior / (sigma + 1e-8)) + (tf.math.exp(sigma) + tf.math.pow(mu - mu_prior, 2)) / (2 * tf.math.exp(sigma_prior)) - .5
        KL_loss = tf.math.reduce_sum(KL_loss)

        return KL_loss
    
    # define loss
    def relation_loss(self, pred_cls_gt, pred_cls_predicted, mu, var):
        cls_loss = keras.losses.CategoricalCrossentropy()(pred_cls_gt, pred_cls_predicted)
        KL_loss = self.KL_divergence(mu, var, 0, 1)

        return self.config['lambda_cls'] * cls_loss + self.config['lambda_kl_2'] * KL_loss

    def generation_loss(self, bb_gt, bb_predicted, mu, var, mu_prior, var_prior):
        # reconstruction loss is L1 loss
        recon_loss = tf.keras.losses.MeanAbsoluteError()(bb_gt, bb_predicted)

        # KL loss can be calculated according to mu and var
        KL_loss = self.KL_divergence(mu, var, mu_prior, var_prior)

        return self.config['lambda_recon'] * recon_loss * bb_gt.shape[0] + self.config['lambda_kl_1'] * KL_loss

    def refinement_loss(self, bb_gt, bb_predicted):
        recon_loss = tf.keras.losses.MeanAbsoluteError()(bb_gt, bb_predicted)

        return recon_loss * bb_gt.shape[0] # return the sum, not mean

    @staticmethod
    def split_graph(objs, triples):
        # split triples, s, p and o all have size (T, 1)
        s, p, o = tf.split(triples, num_or_size_splits=3, axis=1)
        # squeeze, so the result size is (T,)
        s, p, o = [tf.squeeze(x, axis=1) for x in [s, p ,o]]

        return s, p, o


    def fetch_one_data(self, dataset):
        """[summary]

        Args:
            dataset ([type]): [description]
        
        Returns:
            objs (): 
            pos_triples ():
            size_triples ():
        """
        batch_json = dataset.take(1).as_numpy_iterator()

        for item in batch_json:
            batch_data = item

        '''
        combine the objects in one batch into a big graph
        '''
        obj_offset = 0
        all_obj = []
        all_boxes = []

        layout_height = 64.
        layout_width = 64.

        # triple: [s, p, o]
        # s: index in all_obj
        # p: index of relationship
        # o: index in all_obj
        all_pos_triples = []
        # all_size_triples = []

        for item in batch_data:
            layout = json.load(open(item))
            cur_obj = []
            cur_boxes = []
            for category in layout.keys():
                for obj in layout[category]:
                    all_obj.append(self.vocab['object_name_to_idx'][category])
                    cur_obj.append(all_obj[-1])

                    x0, y0, x1, y1 = obj
                    x0 /= layout_width
                    y0 /= layout_height
                    x1 /= layout_width
                    y1 /= layout_height
                    w = x1 - x0
                    h = y1 - y0
                    all_boxes.append(tf.convert_to_tensor([x0, y0, w, h], dtype=tf.float32))
                    cur_boxes.append(all_boxes[-1])
            
            # at the end of one layout add __image__ item
            all_obj.append(self.vocab['object_name_to_idx']['__image__'])
            cur_obj.append(all_obj[-1])
            all_boxes.append(tf.convert_to_tensor([0, 0, 1, 1], dtype=tf.float32))
            cur_boxes.append(all_boxes[-1])

            # compute centers of layout in current layout
            obj_centers = []
            for box in cur_boxes:
                x0, y0, w, h = box
                x1, y1 = x0 + w, y1 + h
                obj_centers.append([(x0 + x1) / 2, (y0 + y1) / 2])

            # calculate triples
            whole_image_idx = self.vocab['object_name_to_idx']['__image__']
            for obj_index, obj in enumerate(cur_obj):
                if obj == whole_image_idx:
                    continue

                # create a complete graph
                other_obj = [obj_idx for obj_idx, obj in enumerate(cur_obj) if (
                    obj_idx != obj_index and obj != whole_image_idx)]
                
                if len(other_obj) == 0:
                    continue

                for other in other_obj:
                    s = obj_index
                    o = other

                    # sx0, sy0, sx1, sy1 = cur_boxes[s]
                    # ox0, oy0, ox1, oy1 = cur_boxes[o]
                    sx0, sy0, sw, sh = cur_boxes[s]
                    ox0, oy0, ow, oh = cur_boxes[o]

                    sx1, sy1 = sx0 + sw, sy0 + sh
                    ox1, oy1 = ox0 + ow, oy0 + oh

                    d0 = obj_centers[s][0] - obj_centers[o][0]
                    d1 = obj_centers[s][1] - obj_centers[o][1]
                    theta = math.atan2(d1, d0)

                    # calculate position relationship
                    # now we have 6 kinds of position relationship
                    if sx0 < ox0 and sx1 > ox1 and sy0 < oy0 and sy1 > oy1:
                        p = 'surrounding'
                    elif sx0 > ox0 and sx1 < ox1 and sy0 > oy0 and sy1 < oy1:
                        p = 'inside'
                    elif theta >= 3 * math.pi / 4 or theta <= -3 * math.pi / 4:
                        p = 'left of'
                    elif -3 * math.pi / 4 <= theta < -math.pi / 4:
                        p = 'above'
                    elif -math.pi / 4 <= theta < math.pi / 4:
                        p = 'right of'
                    elif math.pi / 4 <= theta < 3 * math.pi / 4:
                        p = 'below'
                    p = self.vocab['pos_pred_name_to_idx'][p]

                    all_pos_triples.append([s + obj_offset, p, o + obj_offset])

                    # calculate size relationship
                    # now we have 3 kinds of size relationship
                    # if sw > ow and sh > oh:
                    #     p = 'bigger'
                    # elif sw < ow and sh < oh:
                    #     p = 'smaller'
                    # elif sw * sh > ow * oh:
                    #     p = 'bigger'
                    # elif sw * sh < ow * oh:
                    #     p = 'smaller'
                    # else:
                    #     p = 'same'
                    # p = self.vocab['size_pred_name_to_idx'][p]
                    # all_size_triples.append([s + obj_offset, p, o + obj_offset])

            # add __in_image__ triples
            O = len(cur_obj)
            pos_in_image = self.vocab['pos_pred_name_to_idx']['__in_image__']
            # size_in_image = self.vocab['size_pred_name_to_idx']['__in_image__']
            for i in range(O - 1):
                all_pos_triples.append([i + obj_offset, pos_in_image, O - 1 + obj_offset])
                # all_size_triples.append([i + obj_offset, size_in_image, O - 1 + obj_offset])

            obj_offset += len(cur_obj)
        
        all_obj = tf.convert_to_tensor(all_obj)
        all_boxes = tf.convert_to_tensor(all_boxes)
        all_pos_triples = tf.convert_to_tensor(all_pos_triples)
        # all_size_triples = tf.convert_to_tensor(all_size_triples)
            
        # return all_obj, all_boxes, all_pos_triples, all_size_triples
        return all_obj, all_boxes, all_pos_triples

    def test(self, config, checkpoint_path, output_dir):
        sample_dataset = tf.data.Dataset.list_files(os.path.join(config['data_dir'], '*.json'))
        sample_dataset = sample_dataset.repeat().batch(batch_size=1)

        self.ckpt.restore(checkpoint_path)

        for idx in range(10):
            # objs, boxes, pos_triples_gt, size_triples_gt = self.fetch_one_data(dataset=sample_dataset)
            objs, boxes, pos_triples_gt = self.fetch_one_data(dataset=sample_dataset)

            result = self.run_step(config, objs, boxes, pos_triples_gt, config['part'], training=False)
            
            # this 2 results, use the predicted relation to generate
            # self.draw_boxes(objs, result['pred_boxes_use_predicted'], os.path.join(output_dir, 'test_%d_predicted_with_relation.png' % idx))
            # self.draw_boxes(objs, result['pred_boxes_refine_use_predicted'], os.path.join(output_dir, 'test_%d_refine_with_relaton.png' % idx))
            
            # this 2 results, use the gt relation to generate
            self.draw_boxes(objs, result['pred_boxes'], os.path.join(output_dir, 'test_%d_predicted.png' % idx))
            self.draw_boxes(objs, result['pred_boxes_refine'], os.path.join(output_dir, 'test_%d_refine.png' % idx))
            self.draw_boxes(objs, boxes, os.path.join(output_dir, 'test_%d_gt.png' % idx))


    def run(self, config):
        train_dataset = tf.data.Dataset.list_files(os.path.join(config['data_dir'], '*.json'))
        train_dataset = train_dataset.repeat().shuffle(buffer_size=100).batch(batch_size=config['batch_size'])
        test_dataset = tf.data.Dataset.list_files(os.path.join(config['test_data_dir'], '*.json'))
        test_dataset = test_dataset.repeat().shuffle(buffer_size=100).batch(batch_size=config['batch_size'])
        sample_dataset = tf.data.Dataset.list_files(os.path.join(config['sample_data_dir'], '*.json'))
        sample_dataset = sample_dataset.repeat().batch(batch_size=1)
        
        if self.save:
            ckpt_manager = tf.train.CheckpointManager(
                self.ckpt,
                config['checkpoint_dir'],
                max_to_keep=config['checkpoint_max_to_keep']
            )

            # init tensorboard writer
            train_log_dir = os.path.join(config['log_dir'], 'train')
            test_log_dir = os.path.join(config['log_dir'], 'test')
            train_summary_writer = tf.summary.create_file_writer(train_log_dir)
            test_summary_writer = tf.summary.create_file_writer(test_log_dir)

        # define metrics
        pos_relation_acc = keras.metrics.CategoricalAccuracy()
        # size_relation_acc = keras.metrics.CategoricalAccuracy()
        recon_loss = keras.metrics.MeanAbsoluteError()

        # start training
        while self.iter_cnt < config['max_iteration_number']:
            # objs, boxes, pos_triples_gt, size_triples_gt = self.fetch_one_data(dataset=train_dataset)
            objs, boxes, pos_triples_gt = self.fetch_one_data(dataset=train_dataset)

            result = self.run_step(config, objs, boxes, pos_triples_gt, part=config['part'], training=True)

            if config['part'] == 'relation':
                pos_loss = result['pos_loss']
                gt_pos_cls = result['gt_pos_cls']
                pred_pos_cls = result['pred_pos_cls']
                pos_relation_acc.update_state(gt_pos_cls, pred_pos_cls)
                print('Step: %d. Pos Loss: %f. Position Classification Acc: %f.' 
                % 
                (self.iter_cnt, pos_loss.numpy(), pos_relation_acc.result().numpy()))
            
            if config['part'] == 'generation':
                gen_loss = result['gen_loss']
                refine_loss = result['refine_loss']
                pred_boxes = result['pred_boxes']
                pred_boxes_refine = result['pred_boxes_refine']
                recon_loss.update_state(boxes, pred_boxes_refine)

                print('Step: %d. Gen Loss: %f. Recon Loss: %f.'
                %
                (self.iter_cnt, gen_loss.numpy(), recon_loss.result().numpy()))

                print('Step: %d. Refine Loss: %f.' % (self.iter_cnt, refine_loss.numpy()))
            
            if self.save:
                with train_summary_writer.as_default():
                    if config['part'] == 'relation':
                        tf.summary.scalar('pos_loss', pos_loss, step=self.iter_cnt)
                        tf.summary.scalar('pos_relation_acc', pos_relation_acc.result(), step=self.iter_cnt)

                        pos_relation_acc.reset_states()

                    if config['part'] == 'generation':
                        tf.summary.scalar('gen_loss', gen_loss, step=self.iter_cnt)
                        tf.summary.scalar('recon_loss', recon_loss.result(), step=self.iter_cnt)
                        tf.summary.scalar('refine_loss', refine_loss, step=self.iter_cnt)

                        recon_loss.reset_states()
            
            
            if self.save and (self.iter_cnt + 1) % config['checkpoint_every'] == 0:
                ckpt_manager.save()
                print('Checkpoint saved.')
            
            """
            start testing
            """
            objs, boxes, pos_triples_gt = self.fetch_one_data(dataset=test_dataset)

            result = self.run_step(config, objs, boxes, pos_triples_gt, part=config['part'], training=False)
            
            if config['part'] == 'relation':
                gt_pos_cls = result['gt_pos_cls']
                pred_pos_cls = result['pred_pos_cls']
                pos_relation_acc.update_state(gt_pos_cls, pred_pos_cls)
            
            if config['part'] == 'generation':
                pred_boxes = result['pred_boxes']
                pred_boxes_refine = result['pred_boxes_refine']
            
                recon_loss.update_state(boxes, pred_boxes_refine)

            if self.save:
                with test_summary_writer.as_default():
                    if config['part'] == 'relation':
                        tf.summary.scalar('pos_relation_acc', pos_relation_acc.result(), step=self.iter_cnt)
                        pos_relation_acc.reset_states()

                    if config['part'] == 'generation':
                        tf.summary.scalar('recon_loss', recon_loss.result(), step=self.iter_cnt)
                        recon_loss.reset_states()
            

            """
            start sampling
            """
            if self.iter_cnt % int(config['sample_every']) == 0:
                for idx in range(4):
                    objs, boxes, pos_triples_gt = self.fetch_one_data(dataset=sample_dataset)

                    result = self.run_step(config, objs, boxes, pos_triples_gt, part=config['part'], training=False)
                    
                    if config['part'] == 'relation':
                        gt_pos_cls = result['gt_pos_cls']
                        pred_pos_cls = result['pred_pos_cls']
                    
                    if config['part'] == 'generation':
                        pred_boxes = result['pred_boxes']
                        pred_boxes_refine = result['pred_boxes_refine']

                        self.draw_boxes(
                            objs,
                            pred_boxes, 
                            os.path.join(
                                self.config['train_sample_dir'], 'train_%d_%d_predicted.png' 
                                % (self.iter_cnt, idx)
                            )
                        )

                        self.draw_boxes(
                            objs,
                            pred_boxes_refine,
                            os.path.join(
                                self.config['train_sample_dir'], 'train_%d_%d_refined.png' 
                                % (self.iter_cnt, idx)
                            )
                        )

                        self.draw_boxes(
                            objs,
                            boxes,
                            os.path.join(
                                self.config['train_sample_dir'], 'train_%d_%d_gt.png' 
                                % (self.iter_cnt, idx)
                            )
                        )


            self.iter_cnt += 1

    def run_step(self, config, objs, boxes, pos_triples_gt, part, training=True):
        step_result = {}

        s, pos_pred_gt, o = self.split_graph(objs, pos_triples_gt)

        # randomly mask to generate training data
        pos_pred = pos_pred_gt.numpy()
        for idx, _ in enumerate(pos_pred):
            if pos_pred[idx] != 0:
                if random.random() <= config['mask_rate']:
                    pos_pred[idx] = len(self.vocab['pos_pred_name_to_idx']) - 1

        pos_pred = tf.convert_to_tensor(pos_pred)

        # train relation
        if training and config['part'] == 'relation':
            # train pos relation
            with tf.GradientTape() as tape:
                # get embedding of obj and pred
                obj_vecs = self.obj_embedding(objs, training=True)
                pred_vecs = self.pos_pred_embedding(pos_pred, training=True)
                pred_gt_vecs = self.pos_pred_embedding(pos_pred_gt, training=True)

                result = self.pos_relation(obj_vecs, pred_gt_vecs, s, o, pred_vecs=pred_vecs, training=True)

                # embedding pred with one_hot, to calculate cross entropy loss
                pred_gt_one_hot = tf.one_hot(pos_pred_gt, depth=len(self.vocab['pos_pred_name_to_idx']))
                step_result['gt_pos_cls'] = pred_gt_one_hot
                # get latent variable of G_gt
                # z = tf.concat([result['obj_vecs_with_gt'], result['pred_vecs_with_gt']], axis=0)
                pos_loss = self.relation_loss(pred_gt_one_hot, result['pred_cls'], result['z_mu'], result['z_var'])
                step_result['pos_loss'] = pos_loss
                step_result['pred_pos_cls'] = result['pred_cls']

            train_var = self.pos_relation.trainable_variables \
                        + self.obj_embedding.trainable_variables + self.pos_pred_embedding.trainable_variables
            gradients = tape.gradient(pos_loss, train_var)

            self.relation_optimizer.apply_gradients(
                zip(gradients, train_var)
            )
        
        elif config['part'] == 'relation':
            pos_pred = tf.ones_like(pos_pred_gt) * (len(self.vocab['pos_pred_name_to_idx']) - 1)

            obj_vecs = self.obj_embedding(objs, training=False)

            pred_vecs = self.pos_pred_embedding(pos_pred, training=False)
            result = self.pos_relation(obj_vecs, pred_vecs, s, o, training=False)
            pred_gt_one_hot = tf.one_hot(pos_pred_gt, depth=len(self.vocab['pos_pred_name_to_idx']))
            step_result['gt_pos_cls'] = pred_gt_one_hot
            step_result['pred_pos_cls'] = result['pred_cls']

        # train generation
        s, pos_pred, o = self.split_graph(objs, pos_triples_gt)
        
        if training and config['part'] == 'generation':
            with tf.GradientTape() as tape:
                obj_vecs = self.obj_embedding(objs, training=True)
                pos_pred_vecs = self.pos_pred_embedding(pos_pred, training=True)

                result = self.generation(objs, obj_vecs, pos_pred_vecs, boxes, s, o, training=True)
                step_result['pred_boxes'] = result['pred_boxes']
                # `result` contains output of k iterations
                gen_loss = self.generation_loss(
                    bb_gt=boxes, 
                    bb_predicted=result['pred_boxes'],
                    mu=result['mu'],
                    var=result['var'],
                    mu_prior=result['mu_prior'],
                    var_prior=result['var_prior']
                )
                step_result['gen_loss'] = gen_loss
            
            
            train_var = self.generation.trainable_variables
            gradients = tape.gradient(gen_loss, train_var)
            self.generation_optimizer.apply_gradients(
                zip(gradients, train_var)
            )

            # sample training results
            if self.iter_cnt % int(config['sample_every']) == 0:
                layout_size_list = []
                temp_layout = []
                for item in objs.numpy():
                    if item != 0:
                        temp_layout.append(item)
                    else:
                        layout_size_list.append(len(temp_layout) + 1)
                        temp_layout = []
                
                offset = 0
                for k_idx, layout_size in enumerate(layout_size_list):
                    temp_objs = objs[offset : layout_size + offset]
                    temp_boxes = step_result['pred_boxes'][offset : layout_size + offset]
                    self.draw_boxes(temp_objs, temp_boxes, os.path.join(
                        self.config['train_sample_dir'], 'training_%d_%d_predicted.png' % (self.iter_cnt, k_idx)
                    ))

                    temp_boxes = boxes[offset : layout_size + offset]
                    self.draw_boxes(temp_objs, temp_boxes, os.path.join(
                        self.config['train_sample_dir'], 'training_%d_%d_gt.png' % (self.iter_cnt, k_idx)
                    ))

                    offset += layout_size

                    if k_idx >= 5:
                        break
        
        elif config['part'] == 'generation':
            # not using predicted relation
            obj_vecs = self.obj_embedding(objs, training=False)
            pos_pred_vecs = self.pos_pred_embedding(pos_pred, training=False)

            result = self.generation(objs, obj_vecs, pos_pred_vecs, boxes, s, o, training=False)
            step_result['pred_boxes'] = tf.convert_to_tensor(result['pred_boxes'])

            # when not training
            # use the relationship predicted by previous model
            # pos_pred = tf.math.argmax(step_result['pred_pos_cls'], axis=1)
            # pos_pred_vecs = self.pos_pred_embedding(pos_pred, training=False)
            # result = self.generation(objs, obj_vecs, pos_pred_vecs, boxes, s, o, training=False)
            # step_result['pred_boxes_use_predicted'] = tf.convert_to_tensor(result['pred_boxes'])

        # refinement part
        if training and config['part'] == 'generation':
            with tf.GradientTape() as tape:
                obj_vecs = self.obj_embedding(objs, training=True)
                pos_pred_vecs = self.pos_pred_embedding(pos_pred, training=True)

                boxes_adjust = boxes + tf.random.uniform(shape=boxes.shape, minval=-0.05, maxval=0.05)
                result = self.refinement(obj_vecs, pos_pred_vecs, boxes_adjust, s, o, training=True)

                step_result['pred_boxes_refine'] = result['bb_predicted']

                refine_loss = self.refinement_loss(boxes, result['bb_predicted'])
                step_result['refine_loss'] = refine_loss
            
            train_var = self.refinement.trainable_variables
            gradients = tape.gradient(refine_loss, train_var)

            self.refinement_optimizer.apply_gradients(
                zip(gradients, train_var)
            )

        elif config['part'] == 'generation':
            obj_vecs = self.obj_embedding(objs, training=False)

            # not using predicted relation
            pos_pred_vecs = self.pos_pred_embedding(pos_pred, training=False)
            result = self.refinement(obj_vecs, pos_pred_vecs, step_result['pred_boxes'], s, o, training=False)
            step_result['pred_boxes_refine'] = result['bb_predicted']

            # when not training
            # use the relationship predicted by previous model
            # pos_pred = tf.math.argmax(step_result['pred_pos_cls'], axis=1)
            # pos_pred_vecs = self.pos_pred_embedding(pos_pred, training=False)

            # result = self.refinement(obj_vecs, pos_pred_vecs, step_result['pred_boxes_use_predicted'], s, o, training=False)
            # step_result['pred_boxes_refine_use_predicted'] = result['bb_predicted']

        return step_result
    
    def draw_boxes(self, obj_cls, boxes, output_path):
        assert len(obj_cls) == len(boxes)

        colormap = ['#aaaaaa','#0000ff', '#00ff00', '#00ffff', '#ff0000', '#ff00ff', '#ffff00', '#ffffff']
        
        CANVA_SIZE = 640
        canva = Image.new('RGB', (CANVA_SIZE, CANVA_SIZE), (64, 64, 64))
        draw = ImageDraw.Draw(canva)

        for idx in range(len(obj_cls)):
            if obj_cls[idx] == 0:
                continue

            temp_cls = obj_cls[idx]
            x, y, w, h = boxes[idx]

            x0 = x * CANVA_SIZE
            y0 = y * CANVA_SIZE
            x1 = x0 + w * CANVA_SIZE
            y1 = y0 + h * CANVA_SIZE
            
            draw.rectangle([x0, y0, x1, y1], outline=colormap[temp_cls])

        canva.save(output_path)
