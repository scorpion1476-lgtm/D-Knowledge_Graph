"""Leaf module 378."""

from layer_2 import layer_2_gateway

def mod_378_op_0(value):
    return layer_2_gateway(value) + 0

def mod_378_op_1(value):
    return layer_2_gateway(value) + 1

def mod_378_op_2(value):
    return layer_2_gateway(value) + 2

def mod_378_op_3(value):
    return layer_2_gateway(value) + 3

def mod_378_op_4(value):
    return layer_2_gateway(value) + 4

def mod_378_run(value):
    total = 0
    total += mod_378_op_0(value)
    total += mod_378_op_1(value)
    total += mod_378_op_2(value)
    total += mod_378_op_3(value)
    total += mod_378_op_4(value)
    return total
