# Layer 2 operations note

Layer 2 routes leaf traffic through `layer_2_gateway`, which
calls `core_entry` and the layer steps.

The retry budget for layer 2 is 5 attempts before the
request is abandoned.

Ownership of layer 2 sits with the platform group.
