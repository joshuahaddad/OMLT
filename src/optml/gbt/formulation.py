import pyomo.environ as pe
import numpy as np



def add_formulation_to_block(block, gbt, input_vars):
    """
    References
    ----------

     * Misic, V. "Optimization of tree ensembles."
       Operations Research 68.5 (2020): 1605-1624.
     * Mistry, M., et al. "Mixed-integer convex nonlinear optimization with gradient-boosted trees embedded."
       INFORMS Journal on Computing (2020).
    """
    # Assume only one feature for now
    graph = gbt.graph

    root_node = graph.node[0]
    attr = _node_attributes(root_node)

    nodes_feature_ids = np.array(attr['nodes_featureids'].ints)
    nodes_values = np.array(attr['nodes_values'].floats)
    nodes_modes = np.array(attr['nodes_modes'].strings)
    nodes_tree_ids = np.array(attr['nodes_treeids'].ints)
    nodes_node_ids = np.array(attr['nodes_nodeids'].ints)
    nodes_false_node_ids = np.array(attr['nodes_falsenodeids'].ints)
    nodes_true_node_ids = np.array(attr['nodes_truenodeids'].ints)
    nodes_hitrates = np.array(attr['nodes_hitrates'].floats)
    nodes_missing_value_tracks_true = np.array(attr['nodes_missing_value_tracks_true'].ints)

    n_targets = attr['n_targets'].i
    target_ids = np.array(attr['target_ids'].ints)
    target_node_ids = np.array(attr['target_nodeids'].ints)
    target_tree_ids = np.array(attr['target_treeids'].ints)
    target_weights = np.array(attr['target_weights'].floats)

    # Compute derived data
    nodes_leaf_mask = nodes_modes == b'LEAF'
    nodes_branch_mask = nodes_modes == b'BRANCH_LEQ'

    tree_ids = set(nodes_tree_ids)
    feature_ids = set(nodes_feature_ids)

    branch_value_by_feature_id = dict()
    for f in feature_ids:
        nodes_feature_mask = nodes_feature_ids == f
        branch_values = nodes_values[nodes_feature_mask & nodes_branch_mask]
        branch_value_by_feature_id[f] = np.unique(np.sort(branch_values))

    block.z_l = pe.Var(
        zip(nodes_tree_ids[nodes_leaf_mask], nodes_node_ids[nodes_leaf_mask]),
        bounds=(0, None),
        domain=pe.Reals,
    )

    y_index = [(f, bi) for f in feature_ids for bi, _ in enumerate(branch_value_by_feature_id[f])]
    block.y = pe.Var(y_index, domain=pe.Binary)

    @block.Constraint(tree_ids)
    def single_leaf(b, tree_id):
        """Equation 2b, Misic."""
        tree_mask = nodes_tree_ids == tree_id
        return sum(b.z_l[tree_id, node_id] for node_id in nodes_node_ids[nodes_leaf_mask & tree_mask]) == 1

    nodes_tree_branch_ids = [(t, b) for t in tree_ids for b in nodes_node_ids[(nodes_tree_ids == t) & nodes_branch_mask]]

    def _branching_y(tree_id, branch_node_id):
        node_mask = (nodes_tree_ids == tree_id) & (nodes_node_ids == branch_node_id)
        feature_id = nodes_feature_ids[node_mask]
        branch_value = nodes_values[node_mask]
        assert len(feature_id) == 1 and len(branch_value) == 1
        feature_id = feature_id[0]
        branch_value = branch_value[0]
        branch_y_idx, = np.where(branch_value_by_feature_id[feature_id] == branch_value)
        assert len(branch_y_idx) == 1
        return block.y[feature_id, branch_y_idx[0]]

    def _sum_of_z_l(tree_id, start_node_id):
        tree_mask = nodes_tree_ids == tree_id
        local_false_node_ids = nodes_false_node_ids[tree_mask]
        local_true_node_ids = nodes_true_node_ids[tree_mask]
        local_mode = nodes_modes[tree_mask]
        visit_queue = [start_node_id]
        sum_of_z_l = 0.0
        while visit_queue:
            node_id = visit_queue.pop()
            if local_mode[node_id] == b'LEAF':
                sum_of_z_l += block.z_l[tree_id, node_id]
            else:
                # add left and right child to list of nodes to visit
                visit_queue.append(local_false_node_ids[node_id])
                visit_queue.append(local_true_node_ids[node_id])
        return sum_of_z_l

    @block.Constraint(nodes_tree_branch_ids)
    def left_split(b, tree_id, branch_node_id):
        """Equation 2c, Misic."""
        node_mask = (nodes_tree_ids == tree_id) & (nodes_node_ids == branch_node_id)
        y = _branching_y(tree_id, branch_node_id)

        subtree_root = nodes_false_node_ids[node_mask][0]
        return _sum_of_z_l(tree_id, subtree_root) <= y

    @block.Constraint(nodes_tree_branch_ids)
    def right_split(b, tree_id, branch_node_id):
        """Equation 2d, Misic."""
        node_mask = (nodes_tree_ids == tree_id) & (nodes_node_ids == branch_node_id)
        y = _branching_y(tree_id, branch_node_id)

        subtree_root = nodes_true_node_ids[node_mask][0]
        return _sum_of_z_l(tree_id, subtree_root) <= 1- - y

    # TODO: add 2e for cat variables

    @block.Constraint(y_index)
    def order_y(b, feature_id, branch_y_idx):
        """Equation 2f, Misic."""
        branch_values = branch_value_by_feature_id[feature_id]
        if branch_y_idx >= len(branch_values) - 1:
            return pe.Constraint.Skip
        return b.y[feature_id, branch_y_idx] <= b.y[feature_id, branch_y_idx + 1]

    @block.Constraint(y_index)
    def var_lower(b, feature_id, branch_y_idx):
        """Equation 4a, Mistry."""
        x = input_vars[feature_id]
        branch_value = branch_value_by_feature_id[feature_id][branch_y_idx]
        return x >= x.lb + (branch_value - x.lb) * (1 - b.y[feature_id, branch_y_idx])

    @block.Constraint(y_index)
    def var_upper(b, feature_id, branch_y_idx):
        """Equation 4b, Mistry."""
        x = input_vars[feature_id]
        branch_value = branch_value_by_feature_id[feature_id][branch_y_idx]
        return x <= x.ub + (branch_value - x.ub) * b.y[feature_id, branch_y_idx]

    @block.Objective()
    def tree_mean_value(b):
        return sum(
            weight * b.z_l[tree_id, node_id]
            for tree_id, node_id, weight in zip(target_tree_ids, target_node_ids, target_weights)
        )

def _node_attributes(node):
    attr = dict()
    for at in node.attribute:
        attr[at.name] = at
    return attr