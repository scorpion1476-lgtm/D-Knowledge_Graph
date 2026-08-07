"""Leaf module 394."""

from layer_2 import layer_2_gateway

def mod_394_op_0(value):
    return layer_2_gateway(value) + 0

def mod_394_op_1(value):
    return layer_2_gateway(value) + 1

def mod_394_op_2(value):
    return layer_2_gateway(value) + 2

def mod_394_op_3(value):
    return layer_2_gateway(value) + 3

def mod_394_op_4(value):
    return layer_2_gateway(value) + 4

def mod_394_run(value):
    total = 0
    total += mod_394_op_0(value)
    total += mod_394_op_1(value)
    total += mod_394_op_2(value)
    total += mod_394_op_3(value)
    total += mod_394_op_4(value)
    return total
