"""Model-free exact answers for structural questions.

Some questions the graph answers exactly. "What calls this?" is a set lookup,
not an inference. Sending a model a pile of source so it can re-derive an answer
the database already holds costs tokens and adds a chance of being wrong.

This recognises a small, deliberately narrow family of such questions and
answers them with zero model tokens. The narrowness is the point: a pattern that
tried to catch everything would misfire on questions that genuinely need
judgement, and a confidently wrong exact answer is worse than an expensive
correct one. Anything not clearly matched returns None so the normal path runs.

Every answer carries the same over-approximation caveat the underlying code
graph carries, because that is what it is derived from.
"""

from __future__ import annotations

import re

from ..core.db import Database

# Each pattern must name a symbol and be unambiguous about what is asked. The
# symbol group is deliberately permissive about path::name shapes.
_SYMBOL = r"(?P<symbol>[\w./:\\-]+(?:::[\w.]+)?)"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("impact", re.compile(rf"\b(?:blast[- ]radius|impact(?:ed)?(?: radius| set)?)\s+(?:of|for)\s+{_SYMBOL}", re.I)),
    ("impact", re.compile(rf"\bwhat\s+(?:breaks|is\s+affected)\s+(?:if\s+)?(?:i\s+)?(?:change|modify)\s+{_SYMBOL}", re.I)),
    ("callers", re.compile(rf"\b(?:who|what)\s+calls\s+{_SYMBOL}", re.I)),
    ("callers", re.compile(rf"\bcallers?\s+(?:of|for)\s+{_SYMBOL}", re.I)),
    ("callees", re.compile(rf"\bwhat\s+does\s+{_SYMBOL}\s+call\b", re.I)),
    ("tests_for", re.compile(rf"\b(?:tests?|test coverage)\s+(?:for|of)\s+{_SYMBOL}", re.I)),
    ("is_tested", re.compile(rf"\bis\s+{_SYMBOL}\s+tested\b", re.I)),
]

EXACT_KINDS = ("impact", "callers", "callees", "tests_for", "is_tested")

# A question can contain a structural fragment AND ask for something only a
# reader can supply. "Who calls X and why was it designed that way" matches the
# callers pattern, but answering just the callers silently drops half the
# question and presents a partial answer as the whole one. Any of these markers
# means the question is not purely structural, so the exact path declines and
# the normal path runs.
_JUDGEMENT = re.compile(
    r"\b(why|should|would|could|explain|justify|rationale|worth|better|best|"
    r"enough|safe to|ok to|okay to|recommend|suggest|opinion|"
    r"refactor|redesign|improve|review|too\s+(?:many|much|few|big|large|small))\b",
    re.I,
)

_ADVISORY = (
    "Derived from the structural code graph, whose reference resolution is "
    "name-based and therefore over-approximate."
)


def classify(question: str) -> tuple[str, str] | None:
    """Return (kind, symbol) when the question is exactly answerable."""
    if not question or not question.strip():
        return None
    if _JUDGEMENT.search(question):
        # Structural fragment inside a question that also needs judgement.
        return None
    for kind, pattern in _PATTERNS:
        m = pattern.search(question)
        if m:
            symbol = m.group("symbol").strip().strip(".,?;:'\"")
            if symbol:
                return kind, symbol
    return None


def _resolve(db: Database, symbol: str, tenant_id: str) -> tuple[dict | None, list[str]]:
    """Resolve a symbol, returning it only when the match is unambiguous.

    A bare name like ``handle`` can belong to several files. Taking the first
    alphabetically and answering confidently is the worst failure this module
    can have: it reports one symbol's test coverage under another symbol's name,
    and nothing in the output says a choice was made. So all candidates are
    fetched and more than one is a refusal, with the candidates named so the
    caller can disambiguate.
    """
    rows = db.fetchall(
        "SELECT entity_id, canonical, display, kind FROM entities "
        "WHERE tenant_id=? AND kind LIKE 'code:%' AND (entity_id=? OR canonical=? OR display=?) "
        "ORDER BY CASE WHEN kind IN ('code:function','code:method') THEN 0 ELSE 1 END, canonical;",
        (tenant_id, symbol, symbol, symbol),
    )
    candidates = [dict(r) for r in rows]
    if not candidates:
        return None, []
    # An exact canonical or id match is unambiguous even if a display name also
    # matches elsewhere; only a bare display name can be genuinely ambiguous.
    exact = [c for c in candidates if c["canonical"] == symbol or c["entity_id"] == symbol]
    if exact:
        return exact[0], [c["canonical"] for c in exact]
    if len(candidates) > 1:
        return None, [c["canonical"] for c in candidates]
    return candidates[0], [candidates[0]["canonical"]]


def _neighbours(db: Database, entity_id: str, tenant_id: str, predicate: str, *, reverse: bool) -> list[str]:
    if reverse:
        sql = (
            "SELECT e.canonical FROM relationships r JOIN entities e ON e.entity_id = r.subject_id "
            "WHERE r.tenant_id=? AND r.object_id=? AND r.predicate=? ORDER BY e.canonical;"
        )
    else:
        sql = (
            "SELECT e.canonical FROM relationships r JOIN entities e ON e.entity_id = r.object_id "
            "WHERE r.tenant_id=? AND r.subject_id=? AND r.predicate=? ORDER BY e.canonical;"
        )
    return [r["canonical"] for r in db.fetchall(sql, (tenant_id, entity_id, predicate))]


def answer_exact(db: Database, question: str, *, tenant_id: str = "local", depth: int = 3) -> dict | None:
    """Answer a structural question exactly, or return None to fall through.

    ``model_tokens`` is zero by construction: no model is consulted. That field
    is what the benchmark counts, so the saving is visible rather than asserted.
    """
    classified = classify(question)
    if classified is None:
        return None
    kind, symbol = classified

    root, candidates = _resolve(db, symbol, tenant_id)
    if root is None:
        # Refusing beats guessing, for both shapes of failure: a symbol that is
        # not in the graph, and a name that matches several symbols.
        ambiguous = len(candidates) > 1
        return {
            "kind": kind,
            "question": question,
            "symbol": symbol,
            "resolved": False,
            "ambiguous": ambiguous,
            "candidates": candidates,
            "answer": [],
            "model_tokens": 0,
            "why": (
                f"symbol {symbol!r} matches {len(candidates)} symbols; name it exactly to disambiguate"
                if ambiguous
                else f"symbol {symbol!r} is not in the code graph; falling back is the caller's choice"
            ),
        }

    if kind == "impact":
        from ..code.impact import blast_radius

        result = blast_radius(db, root["entity_id"], tenant_id=tenant_id, depth=depth)
        answer = sorted(i["canonical"] for i in result["impacted"])
        depth_note = {"depth": depth, "truncated": bool(result.get("truncated"))}
    elif kind == "callers":
        answer = _neighbours(db, root["entity_id"], tenant_id, "code:calls", reverse=True)
    elif kind == "callees":
        answer = _neighbours(db, root["entity_id"], tenant_id, "code:calls", reverse=False)
    elif kind in ("tests_for", "is_tested"):
        answer = _neighbours(db, root["entity_id"], tenant_id, "code:tested_by", reverse=False)
    else:  # pragma: no cover - EXACT_KINDS is exhaustive
        return None

    payload: dict = {
        "kind": kind,
        "question": question,
        "symbol": root["canonical"],
        "resolved": True,
        "answer": answer,
        "count": len(answer),
        "model_tokens": 0,
        "why": _ADVISORY,
    }
    if kind == "is_tested":
        payload["tested"] = bool(answer)
    if kind == "impact":
        # A bounded traversal must say it was bounded, or a short answer reads
        # as a complete one.
        payload["traversal"] = depth_note
    payload["ambiguous"] = False
    return payload
