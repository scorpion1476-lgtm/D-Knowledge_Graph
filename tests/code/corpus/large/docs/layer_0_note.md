# Layer 0 operations note

Layer 0 routes leaf traffic through `layer_0_gateway`, which
calls `core_entry` and the layer steps.

The retry budget for layer 0 is 3 attempts before the
request is abandoned.

Ownership of layer 0 sits with the platform group.
