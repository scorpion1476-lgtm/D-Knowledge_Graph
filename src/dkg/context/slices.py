"""Answer-shaped node-level slices: the unit the graph should actually return.

The problem this solves. A structural answer that returns a list of qualified
names is cheap but not usable on its own: a reader still has to open the files
to see what those symbols do, and opening files is where the tokens go. An
earlier measurement of this project charged the graph route for reading the
whole of every file its answer named, and on a small corpus the graph route lost
to simply handing over the repository. That result was honest, and it was also a
signal that the wrong thing was being returned.

A file is the wrong unit. A file holds many definitions and the question is
about one of them. The right unit is the SYMBOL, and inside a symbol the right
unit is its signature plus the few lines that actually bear on the question.
That is what this module returns:

- one slice per relevant symbol, never a whole file,
- each slice carrying the declaration plus the minimal relevant lines,
- ranked by a documented relevance score,
- packed into a caller-supplied token budget, with what was dropped reported.

Three detail levels, so the caller chooses the trade rather than inheriting one:

``signature``  the declaration line only. Enough to answer "what is the shape of
               the impacted surface".
``focused``    the declaration plus the lines that mention the seed or a name on
               the path to it, with elisions marked. The default: it is what a
               reviewer actually needs to judge a change.
``full``       the whole symbol body. Still far smaller than the file, and the
               honest upper bound.

Ranking is deterministic and documented, so two runs over one database produce
byte-identical output and a result can be diffed:

    score = 1 / (1 + distance)          proximity to the seed
          * edge confidence along the path
          * (1 + 0.25 if directly adjacent to the seed)

Ties break on the canonical name. The seed itself is always marked required, so
the packer can never drop the thing the question is about in order to fit.

Everything here is read-only with respect to the graph.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field

from ..core.db import Database
from .pack import Unit, pack_units
from .tokens import count_tokens

DETAIL_LEVELS = ("signature", "focused", "full")

# Predicates traversed for each relation a question can be about. Reverse means
# "follow edges backwards": who calls me, rather than whom I call.
RELATIONS = {
    "impact": (("code:calls", "code:inherits", "code:imports"), True),
    "callers": (("code:calls",), True),
    "callees": (("code:calls",), False),
    "flow": (("code:calls",), False),
    "neighbours": (("code:calls", "code:inherits", "code:imports"), None),
}

# How many lines of context are kept around a line that matched, in focused
# mode. One line either side is enough to see the statement in its block without
# pulling in the rest of the body.
_FOCUS_CONTEXT = 1

# A directly adjacent symbol is worth more than its distance alone implies: it
# is the thing that actually touches the seed.
_ADJACENCY_BONUS = 0.25


@dataclass(frozen=True)
class Slice:
    """One symbol, reduced to the part of it that bears on the question."""

    canonical: str
    kind: str
    path: str
    start_line: int
    end_line: int
    distance: int
    confidence: float
    score: float
    excerpt: str
    elided_lines: int
    required: bool = False

    def to_dict(self) -> dict:
        return {
            "canonical": self.canonical,
            "kind": self.kind,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "distance": self.distance,
            "confidence": round(self.confidence, 4),
            "score": round(self.score, 6),
            "excerpt": self.excerpt,
            "elided_lines": self.elided_lines,
            "required": self.required,
        }


@dataclass
class _Node:
    entity_id: str
    canonical: str
    kind: str
    distance: int
    confidence: float
    adjacent: bool = False
    meta: dict = field(default_factory=dict)


def _resolve(db: Database, ident: str, tenant_id: str) -> dict | None:
    row = db.fetchone(
        "SELECT entity_id, canonical, display, kind, metadata_json FROM entities "
        "WHERE tenant_id=? AND kind LIKE 'code:%' AND (entity_id=? OR canonical=?) LIMIT 1;",
        (tenant_id, ident, ident),
    )
    return dict(row) if row else None


def _traverse(
    db: Database,
    root: dict,
    *,
    predicates: tuple[str, ...],
    reverse: bool | None,
    depth: int,
    max_nodes: int,
    tenant_id: str,
) -> tuple[list[_Node], bool]:
    """Bounded breadth-first walk recording distance and path confidence.

    Iterative, so a deep graph cannot exhaust the Python stack. The confidence
    carried to a node is the product along the path that reached it, which is
    what makes a chain of weak name-based edges rank below a short strong one.
    """
    placeholders = ",".join("?" for _ in predicates)
    nodes: dict[str, _Node] = {
        root["entity_id"]: _Node(
            root["entity_id"],
            root["canonical"],
            root["kind"],
            0,
            1.0,
            meta=_meta(root),
        )
    }
    frontier: deque[str] = deque([root["entity_id"]])
    truncated = False
    while frontier:
        cur = frontier.popleft()
        node = nodes[cur]
        if node.distance >= depth:
            continue
        rows: list = []
        if reverse is not False:
            rows += list(
                db.fetchall(
                    f"SELECT r.subject_id AS other, r.weight, e.canonical, e.kind, e.metadata_json "  # noqa: S608
                    f"FROM relationships r JOIN entities e ON e.entity_id = r.subject_id "
                    f"WHERE r.tenant_id=? AND r.object_id=? AND r.predicate IN ({placeholders}) LIMIT 2000;",
                    (tenant_id, cur, *predicates),
                )
            )
        if reverse is not True:
            rows += list(
                db.fetchall(
                    f"SELECT r.object_id AS other, r.weight, e.canonical, e.kind, e.metadata_json "  # noqa: S608
                    f"FROM relationships r JOIN entities e ON e.entity_id = r.object_id "
                    f"WHERE r.tenant_id=? AND r.subject_id=? AND r.predicate IN ({placeholders}) LIMIT 2000;",
                    (tenant_id, cur, *predicates),
                )
            )
        for row in rows:
            other = row["other"]
            if other in nodes:
                continue
            if len(nodes) >= max_nodes:
                truncated = True
                break
            weight = float(row["weight"] if row["weight"] is not None else 1.0)
            nodes[other] = _Node(
                other,
                row["canonical"],
                row["kind"],
                node.distance + 1,
                node.confidence * weight,
                adjacent=node.distance == 0,
                meta=_meta(row),
            )
            frontier.append(other)
        if truncated:
            break
    return list(nodes.values()), truncated


def _meta(row) -> dict:
    raw = row["metadata_json"] if "metadata_json" in row.keys() else None
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _symbol_text(db: Database, canonical: str, tenant_id: str) -> str:
    """The stored source of one symbol, or an empty string when not retained.

    Symbol bodies are written as chunks at ingest time. An index-only ingest
    keeps no text, in which case a slice degrades to its declaration from the
    graph rather than failing.
    """
    row = db.fetchone(
        "SELECT c.text FROM chunks c JOIN documents d ON d.document_id = c.document_id "
        "WHERE c.tenant_id=? AND d.format LIKE 'code:%' AND c.chunk_id IN ("
        "  SELECT chunk_id FROM chunks WHERE tenant_id=? AND start_offset=("
        "    SELECT json_extract(metadata_json,'$.start_line') FROM entities "
        "    WHERE tenant_id=? AND canonical=? LIMIT 1)"
        ") AND d.metadata_json LIKE ? LIMIT 1;",
        (tenant_id, tenant_id, tenant_id, canonical, f'%"{canonical.split("::")[0]}"%'),
    )
    return str(row["text"]) if row and row["text"] else ""


def _focus_terms(seed_canonical: str) -> set[str]:
    """Names whose appearance in a line makes that line relevant."""
    terms = set()
    tail = seed_canonical.split("::")[-1]
    for part in (seed_canonical, tail, *tail.split(".")):
        cleaned = part.strip()
        if len(cleaned) >= 3:
            terms.add(cleaned)
    return terms


def _excerpt(text: str, *, detail: str, terms: set[str], start_line: int) -> tuple[str, int]:
    """Reduce a symbol body to the requested detail level.

    Returns the excerpt and how many lines were elided, so a trimmed slice is
    visibly trimmed rather than passing as the whole definition.
    """
    lines = text.splitlines()
    if not lines:
        return "", 0
    if detail == "full":
        return text, 0
    signature_end = _signature_end(lines)
    if detail == "signature":
        return "\n".join(lines[:signature_end]), max(0, len(lines) - signature_end)

    keep: set[int] = set(range(signature_end))
    for i, line in enumerate(lines):
        if any(term in line for term in terms):
            for j in range(max(0, i - _FOCUS_CONTEXT), min(len(lines), i + _FOCUS_CONTEXT + 1)):
                keep.add(j)
    if len(keep) == signature_end:
        # Nothing in the body mentioned the seed. The declaration is the honest
        # answer: this symbol is on the path structurally, not textually.
        return "\n".join(lines[:signature_end]), max(0, len(lines) - signature_end)

    out: list[str] = []
    previous = -1
    for i in sorted(keep):
        if previous >= 0 and i > previous + 1:
            out.append(f"    # ... {i - previous - 1} line(s) elided, from line {start_line + previous + 1}")
        out.append(lines[i])
        previous = i
    if previous < len(lines) - 1:
        out.append(f"    # ... {len(lines) - 1 - previous} line(s) elided")
    return "\n".join(out), len(lines) - len(keep)


def _signature_end(lines: list[str]) -> int:
    """Index just past the declaration.

    A declaration can wrap across lines, so it runs until the first line whose
    stripped text ends in an opening brace or a colon, capped so a file with an
    unusual layout cannot swallow the whole body.
    """
    for i, line in enumerate(lines[:8]):
        stripped = line.rstrip()
        if stripped.endswith((":", "{", ")", ";")) and not stripped.lstrip().startswith("#"):
            return i + 1
    return 1


def answer_slices(
    db: Database,
    seed: str,
    *,
    relation: str = "impact",
    depth: int = 3,
    detail: str = "focused",
    token_budget: int | None = 4000,
    max_nodes: int = 500,
    tenant_id: str = "local",
) -> dict:
    """Ranked node-level slices answering a structural question about ``seed``.

    This is the answer-shaped alternative to returning a list of names and
    letting the caller open every file. It returns the code itself, but only the
    part of it the question is about, and only as much as the budget allows.
    """
    if relation not in RELATIONS:
        from ..core.errors import ValidationError

        raise ValidationError(
            f"unknown relation {relation!r}; expected one of {sorted(RELATIONS)}"
        )
    if detail not in DETAIL_LEVELS:
        from ..core.errors import ValidationError

        raise ValidationError(
            f"unknown detail {detail!r}; expected one of {list(DETAIL_LEVELS)}"
        )
    root = _resolve(db, seed, tenant_id)
    if root is None:
        return {
            "seed": seed,
            "found": False,
            "slices": [],
            "why": "no code entity with that name; nothing was guessed",
        }

    predicates, reverse = RELATIONS[relation]
    depth = max(1, min(int(depth), 10))
    max_nodes = max(1, min(int(max_nodes), 5000))
    nodes, truncated = _traverse(
        db,
        root,
        predicates=predicates,
        reverse=reverse,
        depth=depth,
        max_nodes=max_nodes,
        tenant_id=tenant_id,
    )

    terms = _focus_terms(root["canonical"])
    built: list[Slice] = []
    for node in nodes:
        text = _symbol_text(db, node.canonical, tenant_id)
        excerpt, elided = _excerpt(
            text,
            detail="full" if node.distance == 0 else detail,
            terms=terms,
            start_line=int(node.meta.get("start_line", 1)),
        )
        score = (1.0 / (1.0 + node.distance)) * max(node.confidence, 0.0001)
        if node.adjacent:
            score *= 1.0 + _ADJACENCY_BONUS
        built.append(
            Slice(
                canonical=node.canonical,
                kind=node.kind,
                path=str(node.meta.get("path", node.canonical.split("::")[0])),
                start_line=int(node.meta.get("start_line", 0)),
                end_line=int(node.meta.get("end_line", 0)),
                distance=node.distance,
                confidence=node.confidence,
                score=score,
                excerpt=excerpt,
                elided_lines=elided,
                # The seed is what the question is about, so the packer must
                # never drop it to fit; dropping it would leave a cheap answer
                # to a different question.
                required=node.distance == 0,
            )
        )

    built.sort(key=lambda s: (-s.required, -s.score, s.canonical))
    packed = pack_units(
        [
            Unit(
                key=s.canonical,
                kind=s.kind.removeprefix("code:"),
                text=s.excerpt,
                score=s.score,
                required=s.required,
            )
            for s in built
        ],
        budget=token_budget,
    )
    kept = {u.key for u in packed.units}
    by_key = {s.canonical: s for s in built}

    return {
        "seed": root["canonical"],
        "found": True,
        "relation": relation,
        "detail": detail,
        "depth": depth,
        "slices": [by_key[u.key].to_dict() for u in packed.units],
        "omitted": [
            {"canonical": u.key, "score": round(u.score, 6), "tokens": u.tokens()}
            for u in packed.omitted
        ],
        "totals": {
            "matched": len(built),
            "returned": len(kept),
            "omitted": len(packed.omitted),
            "tokens_used": packed.tokens_used,
            "token_budget": token_budget,
            "budget_exceeded": packed.budget_exceeded,
            "traversal_truncated": truncated,
        },
        "text": packed.text,
        "why": (
            "Node-level slices, not files: each entry is one symbol reduced to its "
            "declaration plus the lines bearing on the seed, ranked by "
            "1/(1+distance) times the product of edge confidence along the path, "
            "with a bonus for direct adjacency, and packed into the token budget. "
            "The seed is marked required and is never dropped to fit. The "
            "underlying edges are structural and name-based, so the matched set "
            "is over-approximate: a returned symbol may not truly be affected."
        ),
    }


def slice_tokens(result: dict) -> int:
    """Tokens the returned slices actually cost, counted not estimated."""
    return count_tokens(result.get("text", ""))
