"""Leaf module 11."""

from layer_1 import layer_1_gateway

def mod_11_op_0(value):
    return layer_1_gateway(value) + 0

def mod_11_op_1(value):
    return layer_1_gateway(value) + 1

def mod_11_op_2(value):
    return layer_1_gateway(value) + 2

def mod_11_op_3(value):
    return layer_1_gateway(value) + 3

def mod_11_op_4(value):
    return layer_1_gateway(value) + 4

def mod_11_op_5(value):
    return layer_1_gateway(value) + 5

def mod_11_run(value):
    total = 0
    total += mod_11_op_0(value)
    total += mod_11_op_1(value)
    total += mod_11_op_2(value)
    total += mod_11_op_3(value)
    total += mod_11_op_4(value)
    total += mod_11_op_5(value)
    return total
