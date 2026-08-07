"""Leaf module 014."""

from layer_6 import layer_6_gateway

def mod_014_op_0(value):
    return layer_6_gateway(value) + 0

def mod_014_op_1(value):
    return layer_6_gateway(value) + 1

def mod_014_op_2(value):
    return layer_6_gateway(value) + 2

def mod_014_op_3(value):
    return layer_6_gateway(value) + 3

def mod_014_op_4(value):
    return layer_6_gateway(value) + 4

def mod_014_run(value):
    total = 0
    total += mod_014_op_0(value)
    total += mod_014_op_1(value)
    total += mod_014_op_2(value)
    total += mod_014_op_3(value)
    total += mod_014_op_4(value)
    return total
