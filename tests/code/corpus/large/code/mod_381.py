"""Leaf module 381."""

from layer_5 import layer_5_gateway

def mod_381_op_0(value):
    return layer_5_gateway(value) + 0

def mod_381_op_1(value):
    return layer_5_gateway(value) + 1

def mod_381_op_2(value):
    return layer_5_gateway(value) + 2

def mod_381_op_3(value):
    return layer_5_gateway(value) + 3

def mod_381_op_4(value):
    return layer_5_gateway(value) + 4

def mod_381_run(value):
    total = 0
    total += mod_381_op_0(value)
    total += mod_381_op_1(value)
    total += mod_381_op_2(value)
    total += mod_381_op_3(value)
    total += mod_381_op_4(value)
    return total
