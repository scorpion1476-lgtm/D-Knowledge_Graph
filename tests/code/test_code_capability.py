"""Code plane capability detection. Pure; always runs."""

from __future__ import annotations

from dkg.code.capability import probe, tree_sitter_available


def test_probe_dict():
    p = probe()
    for key in ("tree_sitter", "languages", "code_ready"):
        assert key in p
    assert isinstance(p["tree_sitter"], bool)
    if p["code_ready"]:
        assert p["tree_sitter"] and any(p["languages"].values())


def test_tree_sitter_available_is_bool():
    assert isinstance(tree_sitter_available(), bool)
