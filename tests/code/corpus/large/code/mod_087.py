"""Leaf module 087."""

from layer_7 import layer_7_gateway

def mod_087_op_0(value):
    return layer_7_gateway(value) + 0

def mod_087_op_1(value):
    return layer_7_gateway(value) + 1

def mod_087_op_2(value):
    return layer_7_gateway(value) + 2

def mod_087_op_3(value):
    return layer_7_gateway(value) + 3

def mod_087_op_4(value):
    return layer_7_gateway(value) + 4

def mod_087_run(value):
    total = 0
    total += mod_087_op_0(value)
    total += mod_087_op_1(value)
    total += mod_087_op_2(value)
    total += mod_087_op_3(value)
    total += mod_087_op_4(value)
    return total
