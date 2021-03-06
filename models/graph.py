import tensorflow as tf
from models.layers import build_mlp


class GraphTripleConv(tf.keras.Model):
    """
    A single layer of scene graph convolution
    """

    def __init__(self, input_dim, output_dim=None, hidden_dim=512, pooling='avg', mlp_normalization='none'):
        super(GraphTripleConv, self).__init__()
        if output_dim is None:
            output_dim = input_dim

        self.input_dim = input_dim
        self.output_dim = output_dim
        self.hidden_dim = hidden_dim

        assert pooling in ['sum', 'avg'], 'Invalid pooling "%s"' % pooling
        self.pooling = pooling
        net1_layers = [3 * input_dim, hidden_dim, 2 * hidden_dim + output_dim]

        self.net1 = build_mlp(net1_layers, activation='leaky_relu', batch_norm='batch')
        # TODO: add init of net1

        net2_layers = [hidden_dim, hidden_dim, output_dim]
        self.net2 = build_mlp(net2_layers, activation='leaky_relu', batch_norm='batch')
        # TODO: add init of net2

    def call(self, obj_vecs, pred_vecs, edges, training=True):
        O = obj_vecs.shape[0]
        T = pred_vecs.shape[0]

        Din, H, Dout = self.input_dim, self.hidden_dim, self.output_dim

        # (T, )
        s_idx = edges[:, 0]
        o_idx = edges[:, 1]

        # (T, D)
        # cur_s_vecs = obj_vecs[s_idx.numpy()]
        # cur_o_vecs = obj_vecs[o_idx.numpy()]
        cur_s_vecs = []
        cur_o_vecs = []

        for idx in s_idx.numpy():
            cur_s_vecs.append(obj_vecs[idx])
        
        for idx in o_idx.numpy():
            cur_o_vecs.append(obj_vecs[idx])
        
        cur_s_vecs = tf.convert_to_tensor(cur_s_vecs)
        cur_o_vecs = tf.convert_to_tensor(cur_o_vecs)

        # (T, 3 * D)
        cur_t_vecs = tf.concat([cur_s_vecs, pred_vecs, cur_o_vecs], axis=1)
        new_t_vecs = self.net1(cur_t_vecs, training=training)

        # (T, x)
        new_s_vecs = new_t_vecs[:, :H]
        new_p_vecs = new_t_vecs[:, H: (H + Dout)]
        new_o_vecs = new_t_vecs[:, (H + Dout): (2 * H + Dout)]

        # TODO: dtype should be determined by obj_vecs
        # (O, H)
        pooled_obj_vecs = tf.zeros(shape=(O, H), dtype=tf.float32)
        pooled_obj_vecs = tf.tensor_scatter_nd_add(
            pooled_obj_vecs, tf.reshape(s_idx, (-1, 1)), new_s_vecs)
        pooled_obj_vecs = tf.tensor_scatter_nd_add(
            pooled_obj_vecs, tf.reshape(o_idx, (-1, 1)), new_o_vecs)

        if self.pooling == 'avg':
            # (O, )
            obj_counts = tf.zeros(O, dtype=tf.float32)
            # (T, )
            ones = tf.ones(T, dtype=tf.float32)
            obj_counts = tf.tensor_scatter_nd_add(
                obj_counts, tf.reshape(s_idx, (-1, 1)), ones)
            obj_counts = tf.tensor_scatter_nd_add(
                obj_counts, tf.reshape(o_idx, (-1, 1)), ones)

            obj_counts = tf.clip_by_value(obj_counts, 1, O)
            pooled_obj_vecs = pooled_obj_vecs / tf.reshape(obj_counts, (-1, 1))

        new_obj_vecs = self.net2(pooled_obj_vecs, training=training)

        return new_obj_vecs, new_p_vecs


class GraphTripleConvNet(tf.keras.Model):
    """ A sequence of scene graph convolution layers  """

    def __init__(self, input_dim, num_layers=5, hidden_dim=512, pooling='avg', mlp_normalization='none'):
        super(GraphTripleConvNet, self).__init__()

        self.num_layers = num_layers
        self.gconvs = []
        gconv_kwargs = {
            'input_dim': input_dim,
            'hidden_dim': hidden_dim,
            'pooling': pooling,
            'mlp_normalization': mlp_normalization,
        }

        for _ in range(self.num_layers):
            self.gconvs.append(GraphTripleConv(**gconv_kwargs))

    def call(self, obj_vecs, pred_vecs, edges, training=True):
        for i in range(self.num_layers):
            gconv = self.gconvs[i]
            obj_vecs, pred_vecs = gconv(obj_vecs, pred_vecs, edges, training=training)
        
        return obj_vecs, pred_vecs


class GraphTripleConvStack(tf.keras.Model):
    def __init__(self, in_h_out_dim_list, pooling='avg', mlp_normalization='none'):
        super(GraphTripleConvStack, self).__init__()

        self.gconvs = []
        for dims in in_h_out_dim_list:
            d_in, d_hidden, d_out = dims
            gconv_kwargs = {
                'input_dim': d_in,
                'hidden_dim': d_hidden,
                'output_dim': d_out,
                'pooling': pooling,
                'mlp_normalization': mlp_normalization
            }
            self.gconvs.append(GraphTripleConv(**gconv_kwargs))

    def call(self, obj_vecs, pred_vecs, edges, training=True):
        for gconv in self.gconvs:
            obj_vecs, pred_vecs = gconv(obj_vecs, pred_vecs, edges, training=training)
        
        return obj_vecs, pred_vecs
