"""Leaf module 003."""

from layer_3 import layer_3_gateway

def mod_003_op_0(value):
    return layer_3_gateway(value) + 0

def mod_003_op_1(value):
    return layer_3_gateway(value) + 1

def mod_003_op_2(value):
    return layer_3_gateway(value) + 2

def mod_003_op_3(value):
    return layer_3_gateway(value) + 3

def mod_003_op_4(value):
    return layer_3_gateway(value) + 4

def mod_003_run(value):
    total = 0
    total += mod_003_op_0(value)
    total += mod_003_op_1(value)
    total += mod_003_op_2(value)
    total += mod_003_op_3(value)
    total += mod_003_op_4(value)
    return total
