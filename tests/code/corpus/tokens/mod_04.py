"""Leaf module 04."""

from layer_4 import layer_4_gateway

def mod_04_op_0(value):
    return layer_4_gateway(value) + 0

def mod_04_op_1(value):
    return layer_4_gateway(value) + 1

def mod_04_op_2(value):
    return layer_4_gateway(value) + 2

def mod_04_op_3(value):
    return layer_4_gateway(value) + 3

def mod_04_op_4(value):
    return layer_4_gateway(value) + 4

def mod_04_op_5(value):
    return layer_4_gateway(value) + 5

def mod_04_run(value):
    total = 0
    total += mod_04_op_0(value)
    total += mod_04_op_1(value)
    total += mod_04_op_2(value)
    total += mod_04_op_3(value)
    total += mod_04_op_4(value)
    total += mod_04_op_5(value)
    return total
