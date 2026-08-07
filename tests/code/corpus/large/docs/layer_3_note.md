# Layer 3 operations note

Layer 3 routes leaf traffic through `layer_3_gateway`, which
calls `core_entry` and the layer steps.

The retry budget for layer 3 is 6 attempts before the
request is abandoned.

Ownership of layer 3 sits with the platform group.
