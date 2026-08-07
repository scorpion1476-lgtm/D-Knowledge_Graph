# Layer 6 operations note

Layer 6 routes leaf traffic through `layer_6_gateway`, which
calls `core_entry` and the layer steps.

The retry budget for layer 6 is 9 attempts before the
request is abandoned.

Ownership of layer 6 sits with the platform group.
