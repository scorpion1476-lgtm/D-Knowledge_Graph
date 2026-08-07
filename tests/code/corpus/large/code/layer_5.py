"""Layer 5, between the leaf modules and core."""

from core import core_entry, core_util_0

def layer_5_step_0(value):
    return core_util_0(value) + 0

def layer_5_step_1(value):
    return core_util_0(value) + 1

def layer_5_step_2(value):
    return core_util_0(value) + 2

def layer_5_gateway(value):
    total = core_entry(value)
    total += layer_5_step_0(value)
    total += layer_5_step_1(value)
    total += layer_5_step_2(value)
    return total

class Layer5Service:
    def handle(self, value):
        return layer_5_gateway(value)

    def describe(self):
        return "layer 5"
