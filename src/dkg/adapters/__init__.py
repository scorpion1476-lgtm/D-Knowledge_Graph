"""Adapter interfaces and the capability registry.

All optional integrations sit behind an adapter with a documented interface.
The capability registry keeps track of which adapters are currently available,
which are declared but missing, and why. The registry never lies about
availability.
"""
