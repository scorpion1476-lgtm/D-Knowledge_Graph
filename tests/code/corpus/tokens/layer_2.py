"""Layer 2: sits between the leaf modules and core."""

from core import core_entry, core_util_0

def layer_2_step_0(value):
    return core_util_0(value) + 0

def layer_2_step_1(value):
    return core_util_0(value) + 1

def layer_2_step_2(value):
    return core_util_0(value) + 2

def layer_2_step_3(value):
    return core_util_0(value) + 3

def layer_2_gateway(value):
    total = core_entry(value)
    total += layer_2_step_0(value)
    total += layer_2_step_1(value)
    total += layer_2_step_2(value)
    total += layer_2_step_3(value)
    return total
