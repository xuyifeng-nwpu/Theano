
from copy import copy
from collections import deque

import utils
import result, op


__all__ = ['inputs',
           'results_and_orphans', 'results', 'orphans',
           'ops',
           'clone', 'clone_get_equiv',
           'io_toposort',
           'default_leaf_formatter', 'default_node_formatter',
           'op_as_string',
           'as_string',
           'Graph']

is_result = lambda o: isinstance(o, result.Result)
is_op = lambda o: isinstance(o, op.Op)

def stack_search(start, expand, mode='bfs', build_inv = False):
    """Search through L{Result}s, either breadth- or depth-first

    @type start: deque
    @param start: search from these nodes
    @type explore: function
    @param explore: when we get to a node, add explore(node) to the list of
                    nodes to visit.  This function should return a list, or None

    @rtype: list of L{Result}
    @return: the list of L{Result}s in order of traversal.
    
    @note: a L{Result} will appear at most once in the return value, even if it
    appears multiple times in the start parameter.  

    @postcondition: every element of start is transferred to the returned list.
    
    @postcondition: start is empty.

    """
    if mode not in ('bfs', 'dfs'):
        raise ValueError('mode should be bfs or dfs', mode)
    rval_set = set()
    rval_list = list()
    start_pop = start.popleft if mode is 'bfs' else start.pop
    expand_inv = {}
    while start:
        l = start_pop()
        if id(l) not in rval_set:
            rval_list.append(l)
            rval_set.add(id(l))
            expand_l = expand(l)
            if expand_l:
                if build_inv:
                    for r in expand_l:
                        expand_inv.setdefault(r, []).append(l)
                start.extend(expand_l)
    assert len(rval_list) == len(rval_set)
    if build_inv:
        return rval_list, expand_inv
    return rval_list

def inputs(result_list):
    """
    @type result_list: list of L{Result}
    @param result_list: output L{Result}s (from which to search backward through owners)
    @returns: the list of L{Result}s with no owner, in the order found by a
    left-recursive depth-first search started at the L{Result}s in result_list.

    """
    def expand(r):
        if r.owner:
            l = list(r.owner.inputs)
            l.reverse()
            return l
    dfs_results = stack_search(deque(result_list), expand, 'dfs')
    rval = [r for r in dfs_results if r.owner is None]
    #print rval, _orig_inputs(o)
    return rval


@utils.deprecated('gof.graph', 'preserving only for review')
def _results_and_orphans(i, o, except_unreachable_input=False):
    """
    @type i: list
    @param i: input L{Result}s
    @type o: list
    @param o: output L{Result}s

    Returns the pair (results, orphans). The former is the set of
    L{Result}s that are involved in the subgraph that lies between i and
    o. This includes i, o, orphans(i, o) and all results of all
    intermediary steps from i to o. The second element of the returned
    pair is orphans(i, o).
    """
    results = set()
    i = set(i)
    results.update(i)
    incomplete_paths = []
    reached = set()

    def helper(r, path):
        if r in i:
            reached.add(r)
            results.update(path)
        elif r.owner is None:
            incomplete_paths.append(path)
        else:
            op = r.owner
            for r2 in op.inputs:
                helper(r2, path + [r2])

    for output in o:
        helper(output, [output])

    orphans = set()
    for path in incomplete_paths:
        for r in path:
            if r not in results:
                orphans.add(r)
                break

    if except_unreachable_input and len(i) != len(reached):
        raise Exception(results_and_orphans.E_unreached)

    results.update(orphans)

    return results, orphans

def results_and_orphans(r_in, r_out, except_unreachable_input=False):
    r_in_set = set(r_in)
    class Dummy(object): pass
    dummy = Dummy()
    dummy.inputs = r_out
    def expand_inputs(io):
        if io in r_in_set:
            return None
        try:
            return [io.owner] if io.owner != None else None
        except AttributeError:
            return io.inputs
    ops_and_results, dfsinv = stack_search(
            deque([dummy]),
            expand_inputs, 'dfs', True)

    if except_unreachable_input:
        for r in r_in:
            if r not in dfsinv:
                raise Exception(results_and_orphans.E_unreached)
    clients = stack_search(
            deque(r_in), 
            lambda io: dfsinv.get(io,None), 'dfs')
    
    ops_to_compute = [o for o in clients if is_op(o) and o is not dummy]

    results = []
    for o in ops_to_compute:
        results.extend(o.inputs)
    results.extend(r_out)

    op_set = set(ops_to_compute)
    assert len(ops_to_compute) == len(op_set)
    orphans = [r for r in results \
            if (r.owner not in op_set) and (r not in r_in_set)]
    return results, orphans

results_and_orphans.E_unreached = 'there were unreachable inputs'


def ops(i, o):
    """
    @type i: list
    @param i: input L{Result}s
    @type o: list
    @param o: output L{Result}s

    Returns the set of ops that are contained within the subgraph
    that lies between i and o, including the owners of the L{Result}s in
    o and intermediary ops between i and o, but not the owners of the
    L{Result}s in i.
    """
    ops = set()
    results, orphans = results_and_orphans(i, o)
    for r in results:
        if r not in i and r not in orphans:
            if r.owner is not None:
                ops.add(r.owner)
    return ops


def results(i, o):
    """
    @type i: list
    @param i: input L{Result}s
    @type o: list
    @param o: output L{Result}s

    Returns the set of Results that are involved in the subgraph
    that lies between i and o. This includes i, o, orphans(i, o)
    and all values of all intermediary steps from i to o.
    """
    return results_and_orphans(i, o)[0]


def orphans(i, o):
    """
    @type i: list
    @param i: input L{Result}s
    @type o: list
    @param o: output L{Result}s

    Returns the set of Results which one or more Results in o depend
    on but are neither in i nor in the subgraph that lies between
    i and o.

    e.g. orphans([x], [(x+y).out]) => [y]
    """
    return results_and_orphans(i, o)[1]


def clone(i, o, copy_inputs = False):
    """
    @type i: list
    @param i: input L{Result}s
    @type o: list
    @param o: output L{Result}s
    @type copy_inputs: bool
    @param copy_inputs: if True, the inputs will be copied (defaults to False)

    Copies the subgraph contained between i and o and returns the
    outputs of that copy (corresponding to o).
    """
    equiv = clone_get_equiv(i, o)
    return [equiv[output] for output in o]


def clone_get_equiv(i, o, copy_inputs_and_orphans = False):
    """
    @type i: list
    @param i: input L{Result}s
    @type o: list
    @param o: output L{Result}s
    @type copy_inputs_and_orphans: bool
    @param copy_inputs_and_orphans: if True, the inputs and the orphans
         will be replaced in the cloned graph by copies available
         in the equiv dictionary returned by the function
         (copy_inputs_and_orphans defaults to False)

    @rtype: a dictionary
    @return: equiv mapping each L{Result} and L{Op} in the
    graph delimited by i and o to a copy (akin to deepcopy's memo).
    """

    d = {}

    for input in i:
        if copy_inputs_and_orphans:
            d[input] = copy(input)
        else:
            d[input] = input

    def clone_helper(result):
        if result in d:
            return d[result]
        op = result.owner
        if not op: # result is an orphan
            if copy_inputs_and_orphans:
                d[result] = copy(result)
            else:
                d[result] = result
            return d[result]
        else:
            new_op = op.clone_with_new_inputs(*[clone_helper(input) for input in op.inputs])
            d[op] = new_op
            for output, new_output in zip(op.outputs, new_op.outputs):
                d[output] = new_output
            return d[result]

    for output in o:
        clone_helper(output)

    return d


def io_toposort(i, o, orderings = {}):
    """
    @type i: list
    @param i: input L{Result}s
    @type o: list
    @param o: output L{Result}s
    @param orderings: {op: [requirements for op]} (defaults to {})

    @rtype: ordered list
    @return: L{Op}s that belong in the subgraph between i and o which
    respects the following constraints:
     - all inputs in i are assumed to be already computed
     - the L{Op}s that compute an L{Op}'s inputs must be computed before it
     - the orderings specified in the optional orderings parameter must be satisfied

    Note that this function does not take into account ordering information
    related to destructive operations or other special behavior.
    """
    prereqs_d = copy(orderings)
    all = ops(i, o)
    for op in all:
        asdf = set([input.owner for input in op.inputs if input.owner and input.owner in all])
        prereqs_d.setdefault(op, set()).update(asdf)
    return utils.toposort(prereqs_d)


default_leaf_formatter = str
default_node_formatter = lambda op, argstrings: "%s(%s)" % (op.strdesc(),
                                                            ", ".join(argstrings))

def op_as_string(i, op,
                 leaf_formatter = default_leaf_formatter,
                 node_formatter = default_node_formatter):
    strs = as_string(i, op.inputs, leaf_formatter, node_formatter)
    return node_formatter(op, strs)


def as_string(i, o,
              leaf_formatter = default_leaf_formatter,
              node_formatter = default_node_formatter):
    """
    @type i: list
    @param i: input L{Result}s
    @type o: list
    @param o: output L{Result}s
    @type leaf_formatter: function
    @param leaf_formatter: takes a L{Result} and returns a string to describe it
    @type node_formatter: function
    @param node_formatter: takes an L{Op} and the list of strings
    corresponding to its arguments and returns a string to describe it

    Returns a string representation of the subgraph between i and o. If the same
    op is used by several other ops, the first occurrence will be marked as
    '*n -> description' and all subsequent occurrences will be marked as '*n',
    where n is an id number (ids are attributed in an unspecified order and only
    exist for viewing convenience).
    """

    i = set(i)

    orph = orphans(i, o)
    
    multi = set()
    seen = set()
    for output in o:
        op = output.owner
        if op in seen:
            multi.add(op)
        else:
            seen.add(op)
    for op in ops(i, o):
        for input in op.inputs:
            op2 = input.owner
            if input in i or input in orph or op2 is None:
                continue
            if op2 in seen:
                multi.add(op2)
            else:
                seen.add(input.owner)
    multi = [x for x in multi]
    done = set()

    def multi_index(x):
        return multi.index(x) + 1

    def describe(r):
        if r.owner is not None and r not in i and r not in orph:
            op = r.owner
            idx = op.outputs.index(r)
            if idx == op._default_output_idx:
                idxs = ""
            else:
                idxs = "::%i" % idx
            if op in done:
                return "*%i%s" % (multi_index(op), idxs)
            else:
                done.add(op)
                s = node_formatter(op, [describe(input) for input in op.inputs])
                if op in multi:
                    return "*%i -> %s" % (multi_index(op), s)
                else:
                    return s
        else:
            return leaf_formatter(r)

    return [describe(output) for output in o]




class Graph:
    """
    Object-oriented wrapper for all the functions in this module.
    """

    def __init__(self, inputs, outputs):
        self.inputs = inputs
        self.outputs = outputs

    def ops(self):
        return ops(self.inputs, self.outputs)

    def values(self):
        return values(self.inputs, self.outputs)

    def orphans(self):
        return orphans(self.inputs, self.outputs)

    def io_toposort(self):
        return io_toposort(self.inputs, self.outputs)

    def toposort(self):
        return self.io_toposort()

    def clone(self):
        o = clone(self.inputs, self.outputs)
        return Graph(self.inputs, o)

    def __str__(self):
        return as_string(self.inputs, self.outputs)








