"""Leaf module 07."""

from layer_2 import layer_2_gateway

def mod_07_op_0(value):
    return layer_2_gateway(value) + 0

def mod_07_op_1(value):
    return layer_2_gateway(value) + 1

def mod_07_op_2(value):
    return layer_2_gateway(value) + 2

def mod_07_op_3(value):
    return layer_2_gateway(value) + 3

def mod_07_op_4(value):
    return layer_2_gateway(value) + 4

def mod_07_op_5(value):
    return layer_2_gateway(value) + 5

def mod_07_run(value):
    total = 0
    total += mod_07_op_0(value)
    total += mod_07_op_1(value)
    total += mod_07_op_2(value)
    total += mod_07_op_3(value)
    total += mod_07_op_4(value)
    total += mod_07_op_5(value)
    return total
