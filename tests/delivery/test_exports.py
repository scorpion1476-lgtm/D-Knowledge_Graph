"""Interoperability graph exports: DOT, Cypher, SVG, Obsidian.

These are shared-core features, so the tests seed the graph tables directly and
need no optional extra.
"""

from __future__ import annotations

import json

from dkg.core.db import open_database
from dkg.export.cypher import export_cypher
from dkg.export.dot import export_dot
from dkg.export.obsidian import export_obsidian
from dkg.export.svg import export_svg

# A label that would break DOT, Cypher, SVG, and HTML if it were not escaped:
# it carries a double quote (DOT), a single quote (Cypher), and angle brackets
# and an ampersand (SVG and HTML).
HOSTILE = 'A"]; d\'ev<script>&\n</script>'


def _seed(db):
    ents = [
        ("e1", "local", "code:class", "pkg::A", HOSTILE),
        ("e2", "local", "code:function", "pkg::helper", "helper"),
        ("e3", "local", "code:method", "pkg::A.m", "m"),
    ]
    for eid, ten, kind, canon, disp in ents:
        db.execute(
            "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) VALUES (?,?,?,?,?,?);",
            (eid, ten, kind, canon, disp, "{}"),
        )
    rels = [
        ("r1", "local", "e3", "code:calls", "e2"),
        ("r2", "local", "e3", "code:inherits", "e1"),
    ]
    for rid, ten, subj, pred, obj in rels:
        db.execute(
            "INSERT INTO relationships(relationship_id, tenant_id, subject_id, predicate, object_id, support, weight, evidence_json, metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?);",
            (rid, ten, subj, pred, obj, "supports", 0.9, "{}", "{}"),
        )


def test_dot_export_wellformed_and_escaped(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        out = export_dot(db, tmp_path / "g.dot")
    text = out.read_text()
    assert text.startswith("digraph dkg {")
    assert text.rstrip().endswith("}")
    assert "->" in text  # at least one edge
    # The raw double quote in the hostile label is escaped, so it cannot end the
    # DOT string early.
    assert '\\"' in text
    assert 'A"];' not in text.replace('\\"', "")


def test_cypher_export_idempotent_merge_and_quoted(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        out = export_cypher(db, tmp_path / "g.cypher")
    text = out.read_text()
    assert "MERGE (n:Entity" in text
    assert "MERGE (a)-[r:CODE_CALLS]->(b)" in text
    assert "MERGE (a)-[r:CODE_INHERITS]->(b)" in text
    # The single quote in the hostile label is escaped so it cannot end the
    # Cypher string literal early.
    assert "\\'" in text
    assert "d'ev" not in text.replace("\\'", "'X")


def test_svg_export_self_contained_and_escaped(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        out = export_svg(db, tmp_path / "g.svg")
    text = out.read_text()
    assert text.startswith("<svg")
    assert "</svg>" in text
    # No external resource, only the required SVG namespace identifier.
    stripped = text.replace("http://www.w3.org/2000/svg", "")
    assert "http://" not in stripped and "https://" not in stripped
    assert "<script" not in text
    # The hostile label is XML-escaped in text content.
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_obsidian_export_one_note_per_entity_with_wikilinks(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        out = export_obsidian(db, tmp_path / "vault")
    md_files = sorted(p.name for p in out.glob("*.md"))
    assert len(md_files) == 3
    index = json.loads((out / "_index.json").read_text())
    assert index["notes"] == 3
    # The method note links to the function and the class it calls and inherits.
    method_note = next(p for p in out.glob("*.md") if p.read_text().startswith("---\nkind: code:method"))
    body = method_note.read_text()
    assert "[[" in body and "]]" in body
    assert "## code:calls" in body
    assert "## code:inherits" in body
