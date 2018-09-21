"""An implementation of SegNet auto-encoder for semantic segmentation."""
from keras.applications.vgg16 import VGG16
from keras.layers import Activation
from keras.layers import BatchNormalization
from keras.layers import Conv2D
from keras.layers import Input
from keras.layers import Lambda
from keras.models import Model
from keras.optimizers import SGD
from keras.regularizers import l2
from .layers import ContrastNormalization
from .layers import MemorizedMaxPooling2D
from .layers import MemorizedUpsampling2D
from .losses import build_weighted_categorical_crossentropy
from .metrics import mean_iou
from .metrics import build_iou_for


def conv_bn_relu(x, num_filters: int):
    """
    Append a conv + batch normalization + relu block to an input tensor.

    Args:
        x: the input tensor to append this dense block to
        num_filters: the number of filters in the convolutional layer

    Returns:
        an updated graph with conv + batch normalization + relu block added

    """
    x = Conv2D(num_filters,
        kernel_size=(3, 3),
        padding='same',
        kernel_initializer='he_uniform',
        kernel_regularizer=l2(5e-4),
    )(x)
    x = BatchNormalization()(x)
    x = Activation('relu')(x)
    return x


def encode(x, nums_filters: list):
    """
    Append a encoder block with a given size and number of filters.

    Args:
        x: the input tensor to append this encoder block to
        num_filters: a list of the number of filters for each block

    Returns:
        a tuple of:
        - an updated graph with conv blocks followed by max pooling
        - the pooling layer to get indexes from for up-sampling

    """
    for num_filters in nums_filters:
        x = conv_bn_relu(x, num_filters)
    pool = MemorizedMaxPooling2D(pool_size=(2, 2), strides=(2, 2))
    x = pool(x)
    return x, pool


def decode(x, pool: MemorizedMaxPooling2D, nums_filters: list):
    """
    Append a decoder block with a given size and number of filters.

    Args:
        x: the input tensor to append this decoder block to
        pool: the corresponding memorized pooling layer to reference indexes
        num_filters: a list of the number of filters for each block

    Returns:
        an updated graph with up-sampling followed by conv blocks

    """
    x = MemorizedUpsampling2D(pool=pool)(x)
    for num_filters in nums_filters:
        x = conv_bn_relu(x, num_filters)
    return x


def classify(x, num_classes: int):
    """
    Add a Softmax classification block to an input CNN.

    Args:
        x: the input tensor to append this classification block to (CNN)
        num_classes: the number of classes to predict with Softmax

    Returns:
        an updated graph with dense convolution followed by Softmax activation

    """
    x = Conv2D(num_classes,
        kernel_size=(1, 1),
        kernel_initializer='he_uniform',
        kernel_regularizer=l2(5e-4),
    )(x)
    x = Activation('softmax')(x)
    return x


def transfer_vgg16_encoder(model):
    """
    Pre-train the encoder network of SegNet from VGG16.

    Args:
        model: the SegNet model to pre-train the encoder of with VGG19

    Returns:
        the model after replacing the encoder weights with VGG16's

    """
    # load the pre-trained VGG16 model using ImageNet weights
    vgg16 = VGG16(weights='imagenet', include_top=False)
    # extract all the convolutional layers (encoder layers) from VGG16
    vgg16_conv = [layer for layer in vgg16.layers if isinstance(layer, Conv2D)]
    # extract all convolutional layers from SegNet, the first len(vgg16_conv)
    # layers in this list are architecturally congruent with the layers in
    # vgg16_conv by index
    model_conv = [layer for layer in model.layers if isinstance(layer, Conv2D)]
    # iterate over the VGG16 layers and replace the SegNet encoder weights
    for idx, layer in enumerate(vgg16_conv):
        model_conv[idx].set_weights(layer.get_weights())


def build_segnet(
    image_shape: tuple,
    num_classes: int,
    label_names: dict=None,
    optimizer=SGD(lr=0.001, momentum=0.9),
    pretrain_encoder: bool=True,
    class_weights=None,
    contrast_norm: str='lcn'
) -> Model:
    """
    Build a SegNet model for the given image shape.

    Args:
        image_shape: the image shape to create the model for
        num_classes: the number of classes to segment for (e.g. c)
        label_names: a dictionary mapping discrete labels to names for IoU
        optimizer: the optimizer for training the network
        pretrain_encoder: whether to initialize the encoder from VGG16
        class_weights: the weights for each class
        contrast_norm: the method of contrast normalization for inputs

    Returns:
        a compiled model of SegNet

    """
    # the input block of the network
    inputs = Input(image_shape)
    # assume 8-bit inputs and convert to floats in [0,1]
    x = Lambda(lambda x: x / 255.0)(inputs)
    # apply contrast normalization if set
    if contrast_norm is not None:
        x = ContrastNormalization(method=contrast_norm)(x)
    # encoder
    x, pool_1 = encode(x, 2 * [64])
    x, pool_2 = encode(x, 2 * [128])
    x, pool_3 = encode(x, 3 * [256])
    x, pool_4 = encode(x, 3 * [512])
    x, pool_5 = encode(x, 3 * [512])
    # decoder
    x = decode(x, pool_5, 3 * [512])
    x = decode(x, pool_4, [512, 512, 256])
    x = decode(x, pool_3, [256, 256, 128])
    x = decode(x, pool_2, [128, 64])
    x = decode(x, pool_1, [64])
    # classifier
    x = classify(x, num_classes)
    # compile the graph
    model = Model(inputs=[inputs], outputs=[x])
    model.compile(
        optimizer=optimizer,
        loss=build_weighted_categorical_crossentropy(class_weights),
        metrics=[
            'accuracy',
            mean_iou,
            *build_iou_for(list(range(num_classes)), label_names),
        ],
    )
    # if transfer learning from ImageNet is enabled, pre-train from VGG16
    if pretrain_encoder:
        transfer_vgg16_encoder(model)

    return model


# explicitly define the outward facing API of this module
__all__ = [build_segnet.__name__]
