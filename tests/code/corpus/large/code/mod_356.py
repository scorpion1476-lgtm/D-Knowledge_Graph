"""Leaf module 356."""

from layer_4 import layer_4_gateway

def mod_356_op_0(value):
    return layer_4_gateway(value) + 0

def mod_356_op_1(value):
    return layer_4_gateway(value) + 1

def mod_356_op_2(value):
    return layer_4_gateway(value) + 2

def mod_356_op_3(value):
    return layer_4_gateway(value) + 3

def mod_356_op_4(value):
    return layer_4_gateway(value) + 4

def mod_356_run(value):
    total = 0
    total += mod_356_op_0(value)
    total += mod_356_op_1(value)
    total += mod_356_op_2(value)
    total += mod_356_op_3(value)
    total += mod_356_op_4(value)
    return total
