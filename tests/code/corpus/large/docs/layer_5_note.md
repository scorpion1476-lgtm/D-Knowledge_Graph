# Layer 5 operations note

Layer 5 routes leaf traffic through `layer_5_gateway`, which
calls `core_entry` and the layer steps.

The retry budget for layer 5 is 8 attempts before the
request is abandoned.

Ownership of layer 5 sits with the platform group.
