# Layer 7 operations note

Layer 7 routes leaf traffic through `layer_7_gateway`, which
calls `core_entry` and the layer steps.

The retry budget for layer 7 is 10 attempts before the
request is abandoned.

Ownership of layer 7 sits with the platform group.
