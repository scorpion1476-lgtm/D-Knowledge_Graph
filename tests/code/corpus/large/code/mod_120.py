"""Leaf module 120."""

from layer_0 import layer_0_gateway

def mod_120_op_0(value):
    return layer_0_gateway(value) + 0

def mod_120_op_1(value):
    return layer_0_gateway(value) + 1

def mod_120_op_2(value):
    return layer_0_gateway(value) + 2

def mod_120_op_3(value):
    return layer_0_gateway(value) + 3

def mod_120_op_4(value):
    return layer_0_gateway(value) + 4

def mod_120_run(value):
    total = 0
    total += mod_120_op_0(value)
    total += mod_120_op_1(value)
    total += mod_120_op_2(value)
    total += mod_120_op_3(value)
    total += mod_120_op_4(value)
    return total

def mod_120_orphan(value):
    return value
