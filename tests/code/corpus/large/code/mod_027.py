"""Leaf module 027."""

from layer_3 import layer_3_gateway

def mod_027_op_0(value):
    return layer_3_gateway(value) + 0

def mod_027_op_1(value):
    return layer_3_gateway(value) + 1

def mod_027_op_2(value):
    return layer_3_gateway(value) + 2

def mod_027_op_3(value):
    return layer_3_gateway(value) + 3

def mod_027_op_4(value):
    return layer_3_gateway(value) + 4

def mod_027_run(value):
    total = 0
    total += mod_027_op_0(value)
    total += mod_027_op_1(value)
    total += mod_027_op_2(value)
    total += mod_027_op_3(value)
    total += mod_027_op_4(value)
    return total
