"""Component-level architecture map, coupling warnings, and Mermaid rendering.

Gated on tree-sitter (the 'code' extra); skips honestly when absent.
"""

from __future__ import annotations

import pytest

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

from dkg.code.architecture import (  # noqa: E402
    ROOT_COMPONENT,
    _component_for,
    _percentile,
    architecture_map,
    render_markdown,
    render_mermaid,
)

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _ingest(db, files):
    parsed = [parse_source(p, t, language=lang) for p, t, lang in files]
    write_code_graph(db, parsed, {p: t for p, t, _ in files}, source_uri="test://arch")


# Two directories, with app depending on lib.
LAYERED = [
    ("lib/util.py", "def helper():\n    return 1\ndef shared():\n    return helper()\n", "python"),
    ("app/main.py", "def run():\n    return helper()\ndef start():\n    return run()\n", "python"),
]

# app and lib each reach into the other, which is a cycle at component level.
CYCLIC = [
    ("lib/core.py", "def low():\n    return 1\ndef back():\n    return topside()\n", "python"),
    ("app/top.py", "def topside():\n    return 2\ndef down():\n    return low()\n", "python"),
]


# -- component derivation ---------------------------------------------------


def test_component_is_the_containing_directory():
    assert _component_for("app/main.py") == "app"
    assert _component_for("a/b/c/deep.py") == "a/b/c"
    assert _component_for("top.py") == ROOT_COMPONENT
    assert _component_for("") == ROOT_COMPONENT


def test_percentile_returns_a_value_the_data_actually_holds():
    assert _percentile([], 0.9) == 0
    assert _percentile([5], 0.9) == 5
    got = _percentile([1, 2, 3, 10], 0.9)
    assert got in (1, 2, 3, 10)
    assert got == 10


# -- the map ----------------------------------------------------------------


@requires_ts
def test_components_are_grouped_and_edges_aggregated(db):
    _ingest(db, LAYERED)
    result = architecture_map(db)
    names = {c["component"] for c in result["components"]}
    assert {"app", "lib"} <= names
    app = next(c for c in result["components"] if c["component"] == "app")
    assert app["symbols"] >= 2
    assert app["languages"] == ["python"]
    # app calls into lib, so there is a component edge in that direction.
    pairs = {(e["from"], e["to"]) for e in result["edges"]}
    assert ("app", "lib") in pairs
    edge = next(e for e in result["edges"] if (e["from"], e["to"]) == ("app", "lib"))
    assert edge["count"] >= 1
    assert edge["cross_language"] == 0


@requires_ts
def test_internal_edges_are_not_counted_as_coupling(db):
    _ingest(db, LAYERED)
    result = architecture_map(db)
    # start -> run lives entirely inside app, so it is internal, not a
    # component edge from app to itself.
    assert all(e["from"] != e["to"] for e in result["edges"])
    app = next(c for c in result["components"] if c["component"] == "app")
    assert app["internal_edges"] >= 1


@requires_ts
def test_cohesion_is_normalized_by_possible_pairs(db):
    _ingest(db, LAYERED)
    result = architecture_map(db)
    for c in result["components"]:
        assert 0.0 <= c["cohesion"] <= 1.0
        if c["symbols"] < 2:
            assert c["cohesion"] == 0.0


@requires_ts
def test_dependency_cycle_is_detected_and_warned_at_high_severity(db):
    _ingest(db, CYCLIC)
    result = architecture_map(db)
    assert result["cycles"], "app and lib depend on each other"
    members = set(result["cycles"][0]["components"])
    assert {"app", "lib"} <= members
    cycle_warnings = [w for w in result["warnings"] if w["kind"] == "dependency_cycle"]
    assert cycle_warnings
    assert cycle_warnings[0]["severity"] == "high"
    # High severity sorts first so the worst thing is at the top of the list.
    assert result["warnings"][0]["severity"] == "high"


@requires_ts
def test_a_layered_graph_reports_no_cycle(db):
    _ingest(db, LAYERED)
    result = architecture_map(db)
    assert result["cycles"] == []
    assert not [w for w in result["warnings"] if w["kind"] == "dependency_cycle"]


@requires_ts
def test_cross_language_component_edge_is_flagged(db):
    _ingest(
        db,
        [
            ("lib/util.js", "function helper() { return 1; }\n", "javascript"),
            ("app/main.py", "def run():\n    return helper()\n", "python"),
        ],
    )
    result = architecture_map(db)
    flagged = [w for w in result["warnings"] if w["kind"] == "cross_language_edge"]
    assert flagged, "a python symbol resolving to a javascript one crosses a boundary"
    assert "name-based resolution" in flagged[0]["detail"]


@requires_ts
def test_totals_are_complete_even_when_lists_are_capped(db):
    _ingest(db, LAYERED)
    result = architecture_map(db, limit=1)
    assert len(result["components"]) == 1
    assert result["totals"]["components"] >= 2
    assert result["lists_capped"]["components_omitted"] >= 1


@requires_ts
def test_map_is_deterministic(db):
    _ingest(db, LAYERED)
    assert architecture_map(db) == architecture_map(db)


def test_empty_graph_produces_an_empty_map(db):
    result = architecture_map(db)
    assert result["components"] == []
    assert result["edges"] == []
    assert result["cycles"] == []
    assert result["warnings"] == []
    assert render_mermaid(result) == ""


# -- rendering --------------------------------------------------------------


@requires_ts
def test_markdown_render_includes_a_mermaid_block_and_tables(db):
    _ingest(db, LAYERED)
    text = render_markdown(architecture_map(db))
    assert "# Architecture overview" in text
    assert "```mermaid" in text
    assert "flowchart LR" in text
    assert "| Component |" in text
    assert "## Coupling warnings" in text
    # Advisory framing must survive into the rendered document.
    assert "advisory" in text


@requires_ts
def test_mermaid_edges_reference_declared_nodes_only(db):
    _ingest(db, LAYERED)
    diagram = render_mermaid(architecture_map(db))
    declared = {line.strip().split("[")[0] for line in diagram.splitlines() if "[\"" in line}
    for line in diagram.splitlines():
        if "-->" in line:
            left, right = line.strip().split("-->")
            assert left.strip() in declared
            assert right.split("|")[-1].strip() in declared


#: A synthetic map, so the palette of the generated diagram can be checked
#: without tree-sitter and without a database.
_RENDER_FIXTURE = {
    "components": [
        {"component": "app", "symbols": 3},
        {"component": "lib", "symbols": 2},
        {"component": "web", "symbols": 1},
    ],
    "edges": [
        {"from": "app", "to": "lib", "count": 2},
        {"from": "web", "to": "app", "count": 1},
    ],
    "cycles": [{"components": ["app", "lib"]}],
}


def test_generated_mermaid_is_greyscale_and_sets_a_global_theme():
    """The diagram the product emits is held to the same grey-only rule as the READMEs.

    Without the themeVariables block a node with no class of its own renders in
    the renderer's default hue, so the global theme is asserted, not just the
    per-class fills.
    """
    import re

    diagram = render_mermaid(_RENDER_FIXTURE)

    assert diagram.startswith("%%{init:"), "no global theme, unstyled shapes fall back to a default hue"
    assert '"themeVariables"' in diagram

    found: list[str] = []
    offenders: list[str] = []
    for token in re.findall(r"#([0-9a-fA-F]{3,8})\b", diagram):
        found.append(token)
        if len(token) in (3, 4):
            channels = tuple(int(c * 2, 16) for c in token[:3])
        elif len(token) in (6, 8):
            channels = tuple(int(token[i : i + 2], 16) for i in (0, 2, 4))
        else:
            offenders.append(f"unreadable hex #{token}")
            continue
        if len(set(channels)) != 1:
            offenders.append(f"#{token} is not grey: channels {channels}")

    assert found, "no colour found at all, so this check would be vacuous"
    assert not offenders, "generated diagram must be grey only: " + "; ".join(offenders)


def test_generated_mermaid_assigns_every_node_a_class():
    """An unassigned node would render in the default colour whatever the classDefs say."""
    diagram = render_mermaid(_RENDER_FIXTURE)
    declared = {
        line.strip().split("[")[0]
        for line in diagram.splitlines()
        if '["' in line and not line.strip().startswith("%%")
    }
    assigned: set[str] = set()
    for line in diagram.splitlines():
        stripped = line.strip()
        if stripped.startswith("class ") and not stripped.startswith("classDef "):
            assigned.update(stripped[len("class ") :].rsplit(" ", 1)[0].split(","))
    assert declared, "no nodes declared, so this check would be vacuous"
    assert declared == assigned, f"nodes with no class: {sorted(declared - assigned)}"


def test_generated_mermaid_node_ids_avoid_mermaid_keywords():
    """A node id equal to a keyword makes the whole diagram fail to render."""
    reserved = {"graph", "end", "subgraph", "style", "class", "classdef", "click",
                "linkstyle", "direction", "flowchart"}
    diagram = render_mermaid(_RENDER_FIXTURE)
    ids = {
        line.strip().split("[")[0]
        for line in diagram.splitlines()
        if '["' in line and not line.strip().startswith("%%")
    }
    assert ids, "no node ids found, so this check would be vacuous"
    assert not {i for i in ids if i.lower() in reserved}


def test_generated_mermaid_is_deterministic():
    """Determinism is a published property of the analysis output."""
    assert render_mermaid(_RENDER_FIXTURE) == render_mermaid(_RENDER_FIXTURE)


def test_mermaid_labels_are_escaped_against_syntax_breakout():
    from dkg.code.architecture import _mermaid_label

    got = _mermaid_label('we[ir]d"na|me{x}')
    for ch in ('"', "[", "]", "{", "}", "|"):
        assert ch not in got


@requires_ts
def test_markdown_states_when_there_are_no_warnings(db):
    _ingest(db, [("solo.py", "def only():\n    return 1\n", "python")])
    text = render_markdown(architecture_map(db))
    assert "None found at the current thresholds." in text
