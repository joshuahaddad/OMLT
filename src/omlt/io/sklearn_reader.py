from skl2onnx.common.data_types import FloatTensorType
from skl2onnx import convert_sklearn
from sklearn.preprocessing import MinMaxScaler, MaxAbsScaler, StandardScaler
from sklearn.preprocessing import RobustScaler
from omlt.scaling import OffsetScaling
from omlt.io.onnx_reader import load_onnx_neural_network
import onnx

def parse_sklearn_scaler(sklearn_scaler):

    if isinstance(sklearn_scaler, StandardScaler):
        offset = sklearn_scaler.mean_
        factor = sklearn_scaler.scale_

    elif isinstance(sklearn_scaler, MaxAbsScaler):
        factor = sklearn_scaler.scale_
        offset = factor*0

    elif isinstance(sklearn_scaler, MinMaxScaler):
        factor = sklearn_scaler.data_max_ - sklearn_scaler.data_min_
        offset = sklearn_scaler.data_min_

    elif isinstance(sklearn_scaler, RobustScaler):
        factor = sklearn_scaler.scale_
        offset = sklearn_scaler.center_

    else:
        raise(ValueError("Scaling object provided is not currently supported. Only linear scalers are supported."
                         "Supported scalers include StandardScaler, MinMaxScaler, MaxAbsScaler, and RobustScaler"))

    return offset, factor

def convert_sklearn_scalers(sklearn_input_scaler, sklearn_output_scaler):

    #Todo: support only scaling input or output?

    offset_inputs, factor_inputs = parse_sklearn_scaler(sklearn_input_scaler)
    offset_outputs, factor_ouputs = parse_sklearn_scaler(sklearn_output_scaler)

    return OffsetScaling(offset_inputs=offset_inputs, factor_inputs=factor_inputs,
                         offset_outputs=offset_outputs, factor_outputs=factor_ouputs)

def load_sklearn_MLP(model, scaling_object=None, input_bounds=None, initial_types=None):

    # Assume float inputs if no types are supplied to the model
    if initial_types is None:
        initial_types = [('float_input', FloatTensorType([None, model.n_features_in_]))]

    onx = convert_sklearn(model, initial_types=initial_types, target_opset=12)

    # Remove initial cast layer created by sklearn2onnx
    graph = onx.graph
    node1 = graph.node[0]
    graph.node.remove(node1)
    new_node = onnx.helper.make_node(
        'MatMul',
        name="MatMul",
        inputs=['float_input', 'coefficient'],
        outputs=['mul_result']
    )
    graph.node.insert(0, new_node)

    # Replace old MatMul node with new node with correct input name
    node2 = graph.node[1]
    graph.node.remove(node2)

    return load_onnx_neural_network(onx, scaling_object, input_bounds)