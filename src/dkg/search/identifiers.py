"""Identifier-aware retrieval: query-side extraction and embedding-text enrichment.

Two halves, both in the shared core so either plane benefits:

- Query side. :func:`extract_query_identifiers` pulls the dotted, snake-case, and
  camel-case tokens out of a query. :func:`identifier_search` finds the stored
  chunks whose entity qualified name matches one of them, and
  :func:`match_fraction` scores how much of the query a candidate's names
  account for. The caller turns that into exactly one extra rank-0 reciprocal
  rank fusion vote, so the signal sits on the same scale as the engines it joins
  rather than on a tuned constant.
- Index side. :func:`enrich_embedding_text` prefixes the text an entity is
  embedded from with its dotted qualified form, its word-split identifier, and
  its enclosing directory, so a natural-language or differently-cased query can
  reach a symbol whose raw body never spells the query's form.

Everything here reads. No function in this module writes to the database, so it
is safe on the read-only tool surface.

The module is plane-neutral on purpose. It never imports the code plane and
never filters on a ``code:`` entity kind: a chunk is associated with an entity by
the generic rule that the entity records the same ``path`` and ``start_line`` in
its metadata as the chunk's document path and start offset. Any producer that
records those two fields gets identifier awareness for free.
"""

from __future__ import annotations

import re
import sqlite3

from ..core.db import Database

__all__ = [
    "IDENTIFIER_RRF_VOTE",
    "RRF_K",
    "chunk_identifier_context",
    "dotted_form",
    "enclosing_directory",
    "enrich_embedding_text",
    "extract_query_identifiers",
    "identifier_matches",
    "identifier_search",
    "match_fraction",
    "split_identifier",
]

# The reciprocal rank fusion constant the search path already uses. An identifier
# match is worth exactly one rank-0 vote in that fusion, scaled by the fraction
# of the query's identifiers it accounts for. That is a derivation, not a tuned
# number: it says an identifier match counts the same as one engine ranking the
# candidate first, no more.
RRF_K = 60.0
IDENTIFIER_RRF_VOTE = 1.0 / RRF_K

# SQLite's default limit on bound parameters is 999; stay well under it.
_PARAM_BATCH = 400

# The read path is reachable from the read-only tool surface, so the work a
# caller can provoke has to be bounded on every dimension, not just on the result
# count. Each extracted identifier costs one bounded entity lookup, so the number
# of identifiers taken from a query is capped too. Mirrors the keyword engine,
# which caps its own token list.
MAX_QUERY_IDENTIFIERS = 16

# Word boundaries inside a single identifier: lowerUpper, and the tail of an
# acronym run that starts a new word (HTTPServer -> HTTP, Server).
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")

# A candidate identifier token in a query: a word that may be joined to further
# words by a dot, a double colon, or a slash. Requiring a word character on both
# sides of every separator keeps sentence-final punctuation out.
_QUERY_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:(?:::|[./])[A-Za-z_][A-Za-z0-9_]*)+|[A-Za-z_][A-Za-z0-9_]*")

_NAMESPACE_SEP = re.compile(r"(?:::|[/\\])")
_HAS_CASE_CHANGE = re.compile(r"[a-z0-9][A-Z]|[A-Z]{2}[a-z]")


def split_identifier(name: str) -> list[str]:
    """Split an identifier into lower-case words.

    Handles snake_case, kebab-case, camelCase, PascalCase, and acronym runs, so
    ``parse_query``, ``parseQuery``, and ``ParseQuery`` all split the same way.
    """
    words: list[str] = []
    for part in _NON_WORD.split(name or ""):
        if not part:
            continue
        for word in _CAMEL_BOUNDARY.split(part):
            if word:
                words.append(word.lower())
    return words


def _strip_extension(component: str) -> str:
    """Drop a trailing file extension from a path component.

    Generic by design: a short trailing alphanumeric run after the last dot is
    treated as an extension. This module must not know either plane's file
    types, so it does not consult a language table.
    """
    head, dot, tail = component.rpartition(".")
    if dot and head and tail.isalnum() and len(tail) <= 10:
        return head
    return component


def _path_segments(path: str) -> list[str]:
    parts = [p for p in _NAMESPACE_SEP.split(path or "") if p and p not in (".", "..")]
    if parts:
        parts[-1] = _strip_extension(parts[-1])
    return [p for p in parts if p]


def dotted_form(qualified: str) -> str:
    """Normalise a qualified name to its dotted form.

    ``src/dkg/search/hybrid.py::Hybrid.run`` becomes
    ``src.dkg.search.hybrid.Hybrid.run``. A name that is already dotted is
    returned with its segments intact.
    """
    raw = (qualified or "").strip()
    if not raw:
        return ""
    path_part, sep, symbol_part = raw.partition("::")
    segments: list[str] = []
    if sep:
        segments.extend(_path_segments(path_part))
        for part in _NAMESPACE_SEP.split(symbol_part):
            segments.extend(p for p in part.split(".") if p)
    elif _NAMESPACE_SEP.search(path_part):
        segments.extend(_path_segments(path_part))
    else:
        segments.extend(p for p in path_part.split(".") if p)
    return ".".join(segments)


def enclosing_directory(path: str) -> str:
    """Return the directory that encloses a path or the path part of a qualified name."""
    raw = (path or "").strip()
    if not raw:
        return ""
    head = raw.partition("::")[0]
    head = head.replace("\\", "/")
    parent, sep, _leaf = head.rpartition("/")
    if not sep:
        return ""
    return parent


def _looks_like_identifier(token: str) -> bool:
    """True for the three shapes the requirement names: dotted, snake, camel."""
    if _NAMESPACE_SEP.search(token) or "." in token:
        return True
    if "_" in token.strip("_") or token.startswith("_") or token.endswith("_"):
        return True
    return bool(_HAS_CASE_CHANGE.search(token))


def extract_query_identifiers(query: str) -> list[str]:
    """Identifier-shaped tokens in a query, in order of first appearance.

    Only dotted, snake-case, and camel-case tokens qualify, so an ordinary prose
    query yields nothing and the ranking is left exactly as it was. At most
    ``MAX_QUERY_IDENTIFIERS`` are returned, so a caller cannot make the read path
    do unbounded work by naming a thousand symbols.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _QUERY_TOKEN.finditer(query or ""):
        token = match.group(0)
        if not _looks_like_identifier(token):
            continue
        key = dotted_form(token).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(token)
        if len(out) >= MAX_QUERY_IDENTIFIERS:
            break
    return out


def _segment_words(name: str) -> list[list[str]]:
    dotted = dotted_form(name)
    if not dotted:
        return []
    return [split_identifier(seg) for seg in dotted.split(".") if seg]


def identifier_matches(identifier: str, candidate: str) -> bool:
    """True when a query identifier names part of a candidate qualified name.

    The rule: the identifier's dotted segments must appear as a contiguous run of
    the candidate's dotted segments, comparing each segment by its word split so
    ``hybrid_search``, ``hybridSearch``, and ``HybridSearch`` are the same
    segment. A suffix match is the common case; allowing any contiguous run lets
    a module-level query such as ``search.hybrid`` reach the symbols inside it.
    """
    q = _segment_words(identifier)
    c = _segment_words(candidate)
    if not q or not c or len(q) > len(c):
        return False
    if any(not seg for seg in q):
        return False
    span = len(q)
    for start in range(len(c) - span + 1):
        if c[start : start + span] == q:
            return True
    return False


def match_fraction(identifiers: list[str], names: list[str]) -> tuple[float, list[str]]:
    """Fraction of the query's identifiers that any of ``names`` accounts for.

    Returns the fraction and the matched identifiers sorted by canonical name so
    the output is byte-identical for the same inputs.
    """
    if not identifiers:
        return 0.0, []
    matched = {i for i in identifiers if any(identifier_matches(i, n) for n in names if n)}
    return len(matched) / len(identifiers), sorted(matched)


def enrich_embedding_text(
    text: str, *, qualified: str | None = None, path: str | None = None
) -> str:
    """Prefix the text an entity is embedded from with its identifier context.

    Adds the dotted qualified form, the word-split identifier, and the enclosing
    directory, each on its own labelled line, ahead of the original text. When
    neither a qualified name nor a path is known the text is returned unchanged,
    so the document plane's free text is never decorated with an empty header.
    """
    name = qualified or path or ""
    directory_source = path or qualified or ""
    lines: list[str] = []
    dotted = dotted_form(name)
    if dotted:
        lines.append(f"qualified: {dotted}")
        words = split_identifier(dotted.rsplit(".", 1)[-1])
        if words:
            lines.append(f"identifier: {' '.join(words)}")
    directory = enclosing_directory(directory_source)
    if directory:
        lines.append(f"directory: {directory}")
    if not lines:
        return text or ""
    return "\n".join([*lines, text or ""])


def _entity_rows(db: Database, tenant_id: str, paths: list[str] | None) -> list[sqlite3.Row]:
    """Entities that record a path and a start line, optionally limited to paths."""
    base = (
        "SELECT canonical, display, "
        "json_extract(metadata_json,'$.path') AS path, "
        "json_extract(metadata_json,'$.start_line') AS start_line "
        "FROM entities WHERE tenant_id=? "
        "AND json_extract(metadata_json,'$.path') IS NOT NULL "
        "AND json_extract(metadata_json,'$.start_line') IS NOT NULL"
    )
    if paths is None:
        return list(db.fetchall(base + " ORDER BY canonical;", (tenant_id,)))
    rows: list[sqlite3.Row] = []
    unique = sorted({p for p in paths if p})
    for start in range(0, len(unique), _PARAM_BATCH):
        batch = unique[start : start + _PARAM_BATCH]
        marks = ",".join("?" for _ in batch)
        rows.extend(
            db.fetchall(
                base + f" AND json_extract(metadata_json,'$.path') IN ({marks}) ORDER BY canonical;",
                (tenant_id, *batch),
            )
        )
    return rows


def _chunk_rows(db: Database, tenant_id: str, chunk_ids: list[str] | None) -> list[sqlite3.Row]:
    base = (
        "SELECT c.chunk_id AS chunk_id, c.start_offset AS start_offset, "
        "json_extract(d.metadata_json,'$.path') AS path "
        "FROM chunks c JOIN documents d ON d.document_id = c.document_id "
        "WHERE c.tenant_id=?"
    )
    if chunk_ids is None:
        return list(db.fetchall(base + " ORDER BY c.chunk_id;", (tenant_id,)))
    rows: list[sqlite3.Row] = []
    unique = sorted({c for c in chunk_ids if c})
    for start in range(0, len(unique), _PARAM_BATCH):
        batch = unique[start : start + _PARAM_BATCH]
        marks = ",".join("?" for _ in batch)
        rows.extend(
            db.fetchall(base + f" AND c.chunk_id IN ({marks}) ORDER BY c.chunk_id;", (tenant_id, *batch))
        )
    return rows


def _chunk_rows_for_paths(db: Database, tenant_id: str, paths: list[str]) -> list[sqlite3.Row]:
    """Chunk positions for the given document paths, read in bounded batches."""
    rows: list[sqlite3.Row] = []
    unique = sorted({p for p in paths if p})
    for start in range(0, len(unique), _PARAM_BATCH):
        batch = unique[start : start + _PARAM_BATCH]
        marks = ",".join("?" for _ in batch)
        rows.extend(
            db.fetchall(
                "SELECT c.chunk_id AS chunk_id, c.start_offset AS start_offset, "
                "json_extract(d.metadata_json,'$.path') AS path "
                "FROM chunks c JOIN documents d ON d.document_id = c.document_id "
                f"WHERE c.tenant_id=? AND json_extract(d.metadata_json,'$.path') IN ({marks}) "
                "ORDER BY c.chunk_id;",
                (tenant_id, *batch),
            )
        )
    return rows


def chunk_identifier_context(
    db: Database, *, tenant_id: str = "local", chunk_ids: list[str] | None = None
) -> dict[str, dict[str, str]]:
    """Map each chunk to the path and qualified name it belongs to.

    Read-only. A chunk is matched to an entity when the entity's metadata records
    the same path as the chunk's document and the same start line as the chunk's
    start offset. Where two entities collide on that pair the lexicographically
    smallest qualified name wins, so the mapping is deterministic.
    """
    chunks = _chunk_rows(db, tenant_id, chunk_ids)
    if not chunks:
        return {}
    paths = [r["path"] for r in chunks if r["path"]]
    by_position: dict[tuple[str, int], str] = {}
    if paths:
        for row in _entity_rows(db, tenant_id, None if chunk_ids is None else paths):
            try:
                key = (str(row["path"]), int(row["start_line"]))
            except (TypeError, ValueError):
                continue
            canonical = str(row["canonical"] or "")
            if not canonical:
                continue
            current = by_position.get(key)
            if current is None or canonical < current:
                by_position[key] = canonical
    out: dict[str, dict[str, str]] = {}
    for row in chunks:
        path = str(row["path"] or "")
        qualified = ""
        if path and row["start_offset"] is not None:
            try:
                qualified = by_position.get((path, int(row["start_offset"])), "")
            except (TypeError, ValueError):
                qualified = ""
        if path or qualified:
            out[str(row["chunk_id"])] = {"path": path, "qualified": qualified}
    return out


def _like_pattern(identifier: str) -> str:
    words = [w for seg in _segment_words(identifier) for w in seg]
    if not words:
        return "%"
    return "%" + "%".join(words) + "%"


def identifier_search(
    db: Database,
    identifiers: list[str],
    *,
    tenant_id: str = "local",
    limit: int = 20,
) -> list[dict]:
    """Chunks whose entity qualified name matches one of the query identifiers.

    Read-only and bounded: each identifier contributes at most ``limit`` entity
    rows, which are then confirmed against :func:`identifier_matches` before any
    chunk is returned. Results are sorted by descending match fraction with ties
    broken by qualified name, so the order is deterministic.
    """
    if not identifiers:
        return []
    lim = max(1, min(int(limit), 200))
    candidates: dict[str, set[str]] = {}  # canonical -> matched identifiers
    for identifier in identifiers:
        pattern = _like_pattern(identifier)
        rows = db.fetchall(
            "SELECT canonical, json_extract(metadata_json,'$.path') AS path, "
            "json_extract(metadata_json,'$.start_line') AS start_line "
            "FROM entities WHERE tenant_id=? "
            "AND json_extract(metadata_json,'$.path') IS NOT NULL "
            "AND json_extract(metadata_json,'$.start_line') IS NOT NULL "
            "AND (LOWER(canonical) LIKE ? OR LOWER(display) LIKE ?) "
            "ORDER BY canonical LIMIT ?;",
            (tenant_id, pattern, pattern, lim),
        )
        for row in rows:
            canonical = str(row["canonical"] or "")
            if canonical and identifier_matches(identifier, canonical):
                candidates.setdefault(canonical, set()).add(identifier)
    if not candidates:
        return []

    positions = _entity_positions(db, tenant_id, sorted(candidates))
    if not positions:
        return []

    # Position to canonical. On a collision the lexicographically smallest
    # qualified name wins so the mapping does not depend on row order.
    wanted: dict[tuple[str, int], str] = {}
    for canonical, key in sorted(positions.items()):
        current = wanted.get(key)
        if current is None or canonical < current:
            wanted[key] = canonical

    chunk_rows = _chunk_rows_for_paths(db, tenant_id, sorted({p for p, _ in wanted}))
    hits: list[tuple[float, str, str]] = []  # (-fraction, canonical, chunk_id)
    for row in chunk_rows:
        path = str(row["path"] or "")
        if not path or row["start_offset"] is None:
            continue
        try:
            key = (path, int(row["start_offset"]))
        except (TypeError, ValueError):
            continue
        matched = wanted.get(key)
        if matched is None:
            continue
        fraction = len(candidates[matched]) / len(identifiers)
        hits.append((-fraction, matched, str(row["chunk_id"])))
    if not hits:
        return []
    hits.sort()
    chunk_ids = [h[2] for h in hits[:lim]]
    texts = _chunk_texts(db, tenant_id, chunk_ids)
    out: list[dict] = []
    for neg_fraction, canonical, chunk_id in hits[:lim]:
        text_row = texts.get(chunk_id)
        if text_row is None:
            continue
        text = str(text_row["text"] or "")
        out.append(
            {
                "chunk_id": chunk_id,
                "document_id": str(text_row["document_id"] or ""),
                "snippet": text[:240],
                "text": text,
                "score": -neg_fraction,
                "qualified": canonical,
                "why": {"engine": "identifier", "qualified": canonical},
            }
        )
    return out


def _entity_positions(
    db: Database, tenant_id: str, canonicals: list[str]
) -> dict[str, tuple[str, int]]:
    """Path and start line for named entities, read in bounded batches."""
    out: dict[str, tuple[str, int]] = {}
    for start in range(0, len(canonicals), _PARAM_BATCH):
        batch = canonicals[start : start + _PARAM_BATCH]
        marks = ",".join("?" for _ in batch)
        for row in db.fetchall(
            "SELECT canonical, json_extract(metadata_json,'$.path') AS path, "
            "json_extract(metadata_json,'$.start_line') AS start_line "
            f"FROM entities WHERE tenant_id=? AND canonical IN ({marks}) ORDER BY canonical;",
            (tenant_id, *batch),
        ):
            try:
                out[str(row["canonical"])] = (str(row["path"]), int(row["start_line"]))
            except (TypeError, ValueError):
                continue
    return out


def _chunk_texts(db: Database, tenant_id: str, chunk_ids: list[str]) -> dict[str, sqlite3.Row]:
    out: dict[str, sqlite3.Row] = {}
    unique = sorted({c for c in chunk_ids if c})
    for start in range(0, len(unique), _PARAM_BATCH):
        batch = unique[start : start + _PARAM_BATCH]
        marks = ",".join("?" for _ in batch)
        for row in db.fetchall(
            "SELECT chunk_id, document_id, text FROM chunks "
            f"WHERE tenant_id=? AND chunk_id IN ({marks}) ORDER BY chunk_id;",
            (tenant_id, *batch),
        ):
            out[str(row["chunk_id"])] = row
    return out
