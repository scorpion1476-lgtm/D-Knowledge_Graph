# Layer 1 operations note

Layer 1 routes leaf traffic through `layer_1_gateway`, which
calls `core_entry` and the layer steps.

The retry budget for layer 1 is 4 attempts before the
request is abandoned.

Ownership of layer 1 sits with the platform group.
