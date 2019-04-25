"""Hourglass Network for Keras.
# Reference paper
- [Objects as Points]
  (https://arxiv.org/pdf/1904.07850.pdf)
# Reference implementation
- [PyTorch CenterNet]
  (https://github.com/xingyizhou/CenterNet/blob/master/src/lib/models/networks/large_hourglass.py)
- [Keras Stacked_Hourglass_Network_Keras]
  (https://github.com/yuanyuanli85/Stacked_Hourglass_Network_Keras/blob/master/src/net/hourglass.py)
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from keras.models import Model
from keras.layers import Conv2D, Input, Activation, BatchNormalization, Add, UpSampling2D, ZeroPadding2D
import keras.backend as K


BASE_WEIGTHS_PATH = (
  'https://github.com/keras-team/keras-applications/'
  'releases/download/densenet/')


def create_hourglass_network(heads, num_stacks, cnv_dim, inres, dims=[256, 384, 384, 384, 512]):
  input_layer = Input(shape=(inres[0], inres[1], 3), name='HGInput')
  inter = pre(input_layer, cnv_dim)
  prev_inter = None
  outputs = []
  for i in range(num_stacks):
    prev_inter = inter
    _heads, inter = hourglass_module(heads, inter, cnv_dim, i, dims)
    outputs.extend(_heads)
    if i < num_stacks - 1:
      inter_ = Conv2D(cnv_dim, 1, use_bias=False, name='inter_.%d.0' % i)(prev_inter)
      inter_ = BatchNormalization(epsilon=1e-5, name='inter_.%d.1' % i)(inter_)

      cnv_ = Conv2D(cnv_dim, 1, use_bias=False, name='cnv_.%d.0' % i)(inter)
      cnv_ = BatchNormalization(epsilon=1e-5, name='cnv_.%d.1' % i)(cnv_)

      inter = Add(name='inters.%d.inters.add' % i)([inter_, cnv_])
      inter = Activation('relu', name='inters.%d.inters.relu' % i)(inter)
      inter = residual(inter, cnv_dim, 'inters.%d' % i)

  model = Model(inputs=input_layer, outputs=outputs)
  return model


def hourglass_module(heads, bottom, cnv_dim, hgid, dims):
  # create left features , f1, f2, f4, f8, f16 and f32
  lfs = left_features(bottom, hgid, dims)

  # create right features, connect with left features
  rf1 = right_features(lfs, hgid, dims)
  rf1 = convolution(rf1, 3, cnv_dim, name='cnvs.%d' % hgid)

  # add 1x1 conv with two heads, inter is sent to next stage
  # head_parts is used for intermediate supervision
  heads = create_heads(heads, rf1, hgid)
  return heads, rf1


def convolution(_x, k, out_dim, name, stride=1):
  padding = (k - 1) // 2
  _x = ZeroPadding2D(padding=padding, name=name + '.pad')(_x)
  _x = Conv2D(out_dim, k, strides=stride, use_bias=False, name=name + '.conv')(_x)
  _x = BatchNormalization(epsilon=1e-5, name=name + '.bn')(_x)
  _x = Activation('relu', name=name + '.relu')(_x)
  return _x


def residual(_x, out_dim, name, stride=1):
  shortcut = _x
  num_channels = K.int_shape(shortcut)[-1]
  _x = ZeroPadding2D(padding=1, name=name + '.pad1')(_x)
  _x = Conv2D(out_dim, 3, strides=stride, use_bias=False, name=name + '.conv1')(_x)
  _x = BatchNormalization(epsilon=1e-5, name=name + '.bn1')(_x)
  _x = Activation('relu', name=name + '.relu1')(_x)

  _x = Conv2D(out_dim, 3, padding='same', use_bias=False, name=name + '.conv2')(_x)
  _x = BatchNormalization(epsilon=1e-5, name=name + '.bn2')(_x)

  if num_channels != out_dim or stride != 1:
    shortcut = Conv2D(out_dim, 1, strides=stride, use_bias=False, name=name + '.shortcut.0')(
        shortcut)
    shortcut = BatchNormalization(epsilon=1e-5, name=name + '.shortcut.1')(shortcut)

  _x = Add(name=name + '.add')([_x, shortcut])
  _x = Activation('relu', name=name + '.relu')(_x)
  return _x


def pre(_x, num_channels):
  # front module, input to 1/4 resolution
  _x = convolution(_x, 7, 128, name='pre.0', stride=2)
  _x = residual(_x, num_channels, name='pre.1', stride=2)
  return _x


def left_features(bottom, hgid, dims):
  # create left half blocks for hourglass module
  # f1, f2, f4 , f8, f16, f32 : 1, 1/2, 1/4 1/8, 1/16, 1/32 resolution
  # 5 times reduce/increase: (256, 384, 384, 384, 512)
  features = [bottom]
  for kk, nh in enumerate(dims):
    pow_str = ''
    for _ in range(kk):
      pow_str += '.center'
    _x = residual(features[-1], nh, name='kps.%d%s.down.0' % (hgid, pow_str), stride=2)
    _x = residual(_x, nh, name='kps.%d%s.down.1' % (hgid, pow_str))
    features.append(_x)
  return features


def connect_left_right(left, right, num_channels, num_channels_next, name):
  # left: 2 residual modules
  left = residual(left, num_channels_next, name=name + 'skip.0')
  left = residual(left, num_channels_next, name=name + 'skip.1')

  # up: 2 times residual & nearest neighbour
  out = residual(right, num_channels, name=name + 'out.0')
  out = residual(out, num_channels_next, name=name + 'out.1')
  out = UpSampling2D(name=name + 'out.upsampleNN')(out)
  out = Add(name=name + 'out.add')([left, out])
  return out


def bottleneck_layer(_x, num_channels, hgid):
  # 4 residual blocks with 512 channels in the middle
  pow_str = 'center.' * 5
  _x = residual(_x, num_channels, name='kps.%d.%s0' % (hgid, pow_str))
  _x = residual(_x, num_channels, name='kps.%d.%s1' % (hgid, pow_str))
  _x = residual(_x, num_channels, name='kps.%d.%s2' % (hgid, pow_str))
  _x = residual(_x, num_channels, name='kps.%d.%s3' % (hgid, pow_str))
  return _x


def right_features(leftfeatures, hgid, dims):
  rf = bottleneck_layer(leftfeatures[-1], dims[-1], hgid)
  for kk in reversed(range(len(dims))):
    pow_str = ''
    for _ in range(kk):
      pow_str += '.center'
    rf = connect_left_right(leftfeatures[kk], rf, dims[kk], dims[max(kk - 1, 0)], name='kps.%d.%s' % (hgid, pow_str))
  return rf


def create_heads(heads, rf1, hgid):
  _heads = []
  for head in sorted(heads):
    num_channels = heads[head]
    _x = Conv2D(256, 3, use_bias=True, padding='same', name=head + '.%d.0.conv' % hgid)(rf1)
    _x = Activation('relu', name=head + '.%d.0.relu' % hgid)(_x)
    _x = Conv2D(num_channels, 1, use_bias=True, name=head + '.%d.1' % hgid)(_x)
    _heads.append(_x)
  return _heads


if __name__ == '__main__':
  kwargs = {
    'num_stacks': 2,
    'cnv_dim': 256,
    'inres': (512, 512),
  }
  heads = {
    'hm': 80,
    'reg': 2,
    'wh': 2
  }
  model = create_hourglass_network(heads=heads, **kwargs)
  print(model.summary())