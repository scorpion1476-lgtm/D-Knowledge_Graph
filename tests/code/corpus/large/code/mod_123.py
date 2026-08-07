"""Leaf module 123."""

from layer_3 import layer_3_gateway

def mod_123_op_0(value):
    return layer_3_gateway(value) + 0

def mod_123_op_1(value):
    return layer_3_gateway(value) + 1

def mod_123_op_2(value):
    return layer_3_gateway(value) + 2

def mod_123_op_3(value):
    return layer_3_gateway(value) + 3

def mod_123_op_4(value):
    return layer_3_gateway(value) + 4

def mod_123_run(value):
    total = 0
    total += mod_123_op_0(value)
    total += mod_123_op_1(value)
    total += mod_123_op_2(value)
    total += mod_123_op_3(value)
    total += mod_123_op_4(value)
    return total
