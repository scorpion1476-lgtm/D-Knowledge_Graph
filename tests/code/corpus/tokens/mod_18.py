"""Leaf module 18."""

from layer_3 import layer_3_gateway

def mod_18_op_0(value):
    return layer_3_gateway(value) + 0

def mod_18_op_1(value):
    return layer_3_gateway(value) + 1

def mod_18_op_2(value):
    return layer_3_gateway(value) + 2

def mod_18_op_3(value):
    return layer_3_gateway(value) + 3

def mod_18_op_4(value):
    return layer_3_gateway(value) + 4

def mod_18_op_5(value):
    return layer_3_gateway(value) + 5

def mod_18_run(value):
    total = 0
    total += mod_18_op_0(value)
    total += mod_18_op_1(value)
    total += mod_18_op_2(value)
    total += mod_18_op_3(value)
    total += mod_18_op_4(value)
    total += mod_18_op_5(value)
    return total
