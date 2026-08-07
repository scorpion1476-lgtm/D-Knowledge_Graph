# Layer 4 operations note

Layer 4 routes leaf traffic through `layer_4_gateway`, which
calls `core_entry` and the layer steps.

The retry budget for layer 4 is 7 attempts before the
request is abandoned.

Ownership of layer 4 sits with the platform group.
