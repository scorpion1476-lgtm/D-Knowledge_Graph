"""Ariadne: the refinement community detector.

A full, shipped part of the platform, covered by the repository licence at the
root like everything else. It runs by default as the refinement pass alongside
the Mnemosyne base pass, and the partition with the higher measured modularity
is the one returned.

It has no third-party dependency of its own. The platform still runs correctly
without it, because the base pass is self-sufficient, and that fallback is
capability-detected rather than assumed.
"""

from __future__ import annotations

from .detector import detect_communities_ariadne

__all__ = ["detect_communities_ariadne"]
