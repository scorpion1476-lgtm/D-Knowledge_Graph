"""Offline HTML graph visualization.

Proves the rendered page is fully self-contained: no external script,
stylesheet, font, or image, and no network call is possible when it is opened.
Seeds the graph directly, so no optional extra is needed.
"""

from __future__ import annotations

from dkg.core.db import open_database
from dkg.export.graphdata import GraphData
from dkg.export.viz import export_html, render_html

HOSTILE_LABEL = '</script><script>alert(1)</script>'


def _seed(db):
    ents = [
        ("e1", "local", "code:class", "pkg::A", "A"),
        ("e2", "local", "code:function", "pkg::helper", HOSTILE_LABEL),
    ]
    for eid, ten, kind, canon, disp in ents:
        db.execute(
            "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) VALUES (?,?,?,?,?,?);",
            (eid, ten, kind, canon, disp, "{}"),
        )
    db.execute(
        "INSERT INTO relationships(relationship_id, tenant_id, subject_id, predicate, object_id, support, weight, evidence_json, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?);",
        ("r1", "local", "e1", "code:calls", "e2", "supports", 0.9, "{}", "{}"),
    )


def test_html_is_offline_and_self_contained(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        out = export_html(db, tmp_path / "g.html")
    html = out.read_text()
    assert html.startswith("<!doctype html>")
    # The node data is inlined.
    assert '"nodes"' in html
    # Strip the only permitted identifier (the SVG namespace, never fetched) and
    # assert no remaining external reference of any kind.
    stripped = html.replace("http://www.w3.org/2000/svg", "")
    for forbidden in (
        "http://",
        "https://",
        "src=",
        "<link",
        "@import",
        "url(",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "//cdn",
        "unpkg",
        "jsdelivr",
        "cloudflare",
        "d3.",
    ):
        assert forbidden not in stripped, f"HTML references an external resource: {forbidden!r}"


def test_html_escapes_hostile_label_no_script_breakout(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        html = render_html(_load(db))
    # The hostile label must not appear as live markup: its angle brackets are
    # unicode-escaped inside the JSON payload, so no injected <script> survives.
    assert "<script>alert(1)</script>" not in html
    assert "</script><script>" not in html.replace(
        '<script id="data" type="application/json">', ""
    ).replace("<script>\n(function", "")
    # The one real closing tag for the data script is the literal below; there
    # must be exactly the two script open tags we author (data + logic).
    assert html.count("<script") == 2
    assert "\\u003cscript\\u003ealert(1)" in html


def test_no_inline_event_handler_attributes(tmp_path):
    """Interaction is bound with addEventListener, never with an on* attribute.

    An on* attribute would be script in markup, which is exactly what a hostile
    label would need in order to become executable, and it is also what a strict
    content-security policy refuses.
    """
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        html = render_html(_load(db))
    for handler in ("onclick=", "onload=", "onerror=", "onmouseover=", "onfocus=", "javascript:"):
        assert handler not in html.lower(), f"the viewer uses an inline handler: {handler!r}"
    assert "addEventListener" in html


def test_export_is_byte_stable_across_runs(tmp_path):
    """The same graph must always write the same file, so exports can be diffed."""
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        first = export_html(db, tmp_path / "a.html").read_bytes()
        second = export_html(db, tmp_path / "b.html").read_bytes()
    assert first == second
    assert len(first) > 1000


def _load(db) -> GraphData:
    from dkg.export.graphdata import load_graph

    return load_graph(db)
