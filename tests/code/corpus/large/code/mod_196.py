"""Leaf module 196."""

from layer_4 import layer_4_gateway

def mod_196_op_0(value):
    return layer_4_gateway(value) + 0

def mod_196_op_1(value):
    return layer_4_gateway(value) + 1

def mod_196_op_2(value):
    return layer_4_gateway(value) + 2

def mod_196_op_3(value):
    return layer_4_gateway(value) + 3

def mod_196_op_4(value):
    return layer_4_gateway(value) + 4

def mod_196_run(value):
    total = 0
    total += mod_196_op_0(value)
    total += mod_196_op_1(value)
    total += mod_196_op_2(value)
    total += mod_196_op_3(value)
    total += mod_196_op_4(value)
    return total
