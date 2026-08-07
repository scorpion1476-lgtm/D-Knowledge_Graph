"""Markdown export: one document per source, tables of entities and claims."""

from __future__ import annotations

from pathlib import Path

from ..core.db import Database


def export_markdown(db: Database, out: Path, *, source_id: str | None = None) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = ["# D-Knowledge_Graph export", ""]

    if source_id is None:
        sources = db.fetchall("SELECT * FROM sources ORDER BY added_at;")
    else:
        sources = db.fetchall("SELECT * FROM sources WHERE source_id=?;", (source_id,))

    for s in sources:
        parts.append(f"## Source: {s['display_name'] or s['uri']}")
        parts.append("")
        parts.append(f"- URI: {s['uri']}")
        parts.append(f"- Kind: {s['kind']}")
        parts.append(f"- Added: {s['added_at']}")
        parts.append("")
        docs = db.fetchall(
            "SELECT * FROM documents WHERE source_id=? ORDER BY version;",
            (s["source_id"],),
        )
        for d in docs:
            parts.append(f"### Document v{d['version']} ({d['format']})")
            parts.append(f"- SHA-256: `{d['content_sha256']}`")
            parts.append(f"- Bytes: {d['byte_length']}")
            parts.append("")
            chunks = db.fetchall(
                "SELECT ord, text FROM chunks WHERE document_id=? ORDER BY ord;",
                (d["document_id"],),
            )
            for c in chunks:
                parts.append(f"#### Chunk {c['ord']}")
                parts.append("")
                parts.append(c["text"])
                parts.append("")

    parts.append("## Entities")
    parts.append("")
    parts.append("| Kind | Canonical | Display |")
    parts.append("|------|-----------|---------|")
    for e in db.fetchall("SELECT kind, canonical, display FROM entities ORDER BY kind, canonical;"):
        parts.append(f"| {e['kind']} | {e['canonical']} | {e['display']} |")

    parts.append("")
    parts.append("## Claims")
    parts.append("")
    parts.append("| Subject | Predicate | Object | Confidence |")
    parts.append("|---------|-----------|--------|------------|")
    for c in db.fetchall(
        """
        SELECT COALESCE(e.display, c.subject_id, '(none)') AS subj,
               c.predicate, c.object_text, c.confidence
        FROM claims c
        LEFT JOIN entities e ON e.entity_id = c.subject_id
        ORDER BY c.predicate;
        """
    ):
        parts.append(f"| {c['subj']} | {c['predicate']} | {c['object_text']} | {c['confidence']} |")

    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return out
