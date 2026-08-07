"""Budgeted node-level context packing.

The packing strategy underneath every other lever. Instead of returning whole
files, the graph returns ranked typed units (a signature plus the minimal
relevant lines) and packs as many as fit a caller-supplied token budget.

One rule is absolute and is why this is not just truncation: a unit marked
structurally required is never dropped to save tokens. If the required set alone
exceeds the budget, the budget is reported as exceeded and the required units
are still returned. Silently dropping a symbol the answer depends on would trade
a correct expensive answer for a cheap wrong one, which is not a saving.

Everything is deterministic: units are ordered by (required, score, key) with
ties broken by name, so the same inputs always pack the same way.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .tokens import count_tokens

# A unit costs slightly more than its text: the caller has to label it so the
# reader knows what it is. Counted so the budget is not quietly overspent.
_HEADER_TEMPLATE = "# {kind} {key}\n"


@dataclass
class Unit:
    """One rankable piece of context."""

    key: str
    kind: str
    text: str
    score: float = 0.0
    required: bool = False
    _tokens: int | None = field(default=None, repr=False, compare=False)

    def rendered(self) -> str:
        return _HEADER_TEMPLATE.format(kind=self.kind, key=self.key) + self.text

    def tokens(self) -> int:
        if self._tokens is None:
            self._tokens = count_tokens(self.rendered())
        return self._tokens


@dataclass
class PackResult:
    units: list[Unit]
    omitted: list[Unit]
    tokens_used: int
    budget: int | None
    budget_exceeded: bool
    required_count: int

    @property
    def text(self) -> str:
        return "\n".join(u.rendered() for u in self.units)

    def to_dict(self) -> dict:
        return {
            "included": [{"key": u.key, "kind": u.kind, "tokens": u.tokens(), "required": u.required} for u in self.units],
            "omitted": [{"key": u.key, "kind": u.kind, "tokens": u.tokens()} for u in self.omitted],
            "tokens_used": self.tokens_used,
            "budget": self.budget,
            "budget_exceeded": self.budget_exceeded,
            "required_count": self.required_count,
            "omitted_count": len(self.omitted),
            "why": {
                "ordering": "required first, then score descending, then key ascending",
                "rule": "a structurally required unit is never dropped to fit the budget",
            },
        }


def _ordered(units: Iterable[Unit]) -> list[Unit]:
    return sorted(units, key=lambda u: (not u.required, -float(u.score), u.key))


def pack_units(units: Sequence[Unit], *, budget: int | None = None) -> PackResult:
    """Pack ranked units into a token budget.

    ``budget`` of None means unbounded, which is the honest default: a caller
    who has not chosen a budget should not silently get a truncated answer.
    """
    ordered = _ordered(units)
    if budget is None:
        total = sum(u.tokens() for u in ordered)
        return PackResult(ordered, [], total, None, False, sum(1 for u in ordered if u.required))

    budget = max(0, int(budget))
    included: list[Unit] = []
    omitted: list[Unit] = []
    used = 0
    for unit in ordered:
        cost = unit.tokens()
        if unit.required:
            # Required units are admitted unconditionally. The overspend is
            # reported rather than hidden.
            included.append(unit)
            used += cost
            continue
        if used + cost <= budget:
            included.append(unit)
            used += cost
        else:
            omitted.append(unit)
    return PackResult(
        units=included,
        omitted=omitted,
        tokens_used=used,
        budget=budget,
        budget_exceeded=used > budget,
        required_count=sum(1 for u in included if u.required),
    )


def units_from_graph(
    db,
    keys: Sequence[str],
    *,
    tenant_id: str = "local",
    required: Iterable[str] = (),
    scores: dict[str, float] | None = None,
    max_lines: int = 12,
) -> list[Unit]:
    """Build units for named code symbols from the stored chunk text.

    Only the first ``max_lines`` lines of a symbol are taken, which is the
    signature plus enough body to judge relevance. Taking the whole body would
    make node-level slicing no cheaper than reading the file.
    """
    required_set = set(required)
    scores = scores or {}
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        return []
    placeholders = ",".join("?" * len(wanted))
    rows = db.fetchall(
        "SELECT e.canonical, e.kind, c.text FROM entities e "
        "LEFT JOIN chunks c ON c.tenant_id = e.tenant_id "
        "AND c.text IS NOT NULL AND instr(c.text, e.display) > 0 "
        f"WHERE e.tenant_id=? AND e.canonical IN ({placeholders}) "
        "GROUP BY e.canonical ORDER BY e.canonical;",
        (tenant_id, *wanted),
    )
    by_key = {r["canonical"]: (r["kind"], r["text"] or "") for r in rows}
    units: list[Unit] = []
    for key in wanted:
        kind, text = by_key.get(key, ("code:unknown", ""))
        snippet = "\n".join(text.splitlines()[:max_lines])
        units.append(
            Unit(
                key=key,
                kind=kind,
                text=snippet,
                score=float(scores.get(key, 0.0)),
                required=key in required_set,
            )
        )
    return units


# Ranked list fields an analysis payload may carry, longest-tail first. Trimming
# these is how a token budget is applied to a structured result: the ranked
# lists are what grow without bound, and they are already ordered worst-last.
_TRIMMABLE = (
    "couplings", "hubs", "chokepoints", "questions", "isolated",
    "untested_hotspots", "thin_communities", "components", "edges",
    "warnings", "cycles", "impacted", "reached", "chains", "results",
    # Nested under "bridges" in the centrality payload, where they are usually
    # the largest list of all.
    "bridge_edges", "articulation_points",
    # Nested under "communities" and the diff payload.
    "members", "added_nodes", "removed_nodes", "added_edges", "removed_edges",
)


def _trimmable_lists(payload: dict, trimmable: tuple[str, ...]) -> list[tuple[list, str]]:
    """Every ranked list in the payload, including one level of nesting.

    Nesting matters: an analysis payload keeps its biggest list inside a nested
    object (bridge edges under bridges), and a top-level-only scan would leave
    the dominant list untouched while stripping the visible ones to nothing.
    """
    found: list[tuple[list, str]] = []
    for key, value in payload.items():
        if key in trimmable and isinstance(value, list) and value:
            found.append((value, key))
        elif isinstance(value, dict):
            for sub, subvalue in value.items():
                if sub in trimmable and isinstance(subvalue, list) and subvalue:
                    found.append((subvalue, f"{key}.{sub}"))
    return found


def apply_budget(payload: dict, *, budget: int | None, trimmable: tuple[str, ...] = _TRIMMABLE) -> dict:
    """Trim a structured payload's ranked lists to fit a token budget.

    Applied to the serialised JSON, because that is what a caller actually pays
    for. Only ranked lists are trimmed, and only from the tail, so the entries
    kept are the highest-ranked ones.

    Two rules keep a trimmed payload useful rather than merely small. Trimming
    takes one entry at a time from whichever list is currently longest, so no
    single list is stripped while another stays long. And no list is ever
    emptied: the top entry always survives, because a result listing nothing is
    not a cheaper answer, it is no answer. If the budget still cannot be met the
    payload says so.

    Totals are never rewritten. The payload keeps reporting the true counts and
    records what was dropped, so a trimmed result can never be mistaken for a
    complete one.
    """
    import copy
    import json

    if budget is None or budget <= 0:
        return payload

    def size(obj: dict) -> int:
        return count_tokens(json.dumps(obj, indent=2, sort_keys=True))

    if size(payload) <= budget:
        return payload

    trimmed = copy.deepcopy(payload)
    dropped: dict[str, int] = {}
    lists = _trimmable_lists(trimmed, trimmable)
    # The report block is part of what the caller pays for, so it is present
    # during trimming. Adding it afterwards let the loop stop just under budget
    # and the finished payload land just over.
    block = {
        "budget": budget,
        "tokens": 0,
        "trimmed_for_budget": False,
        "entries_dropped": {},
        "budget_exceeded": False,
        "note": (
            "Ranked lists were trimmed from the tail, longest first, to fit the "
            "budget. No list is emptied: the top entry always survives. The "
            "totals elsewhere in this payload are unchanged and still report the "
            "true counts, so this result is not a complete listing."
        ),
    }
    trimmed["token_budget"] = block
    while size(trimmed) > budget:
        # Longest first, and never below one entry.
        candidates = [(lst, name) for lst, name in lists if len(lst) > 1]
        if not candidates:
            break
        lst, name = max(candidates, key=lambda pair: (len(pair[0]), pair[1]))
        lst.pop()
        dropped[name] = dropped.get(name, 0) + 1
        # Keep the report block current inside the loop: it grows as keys are
        # added to entries_dropped, and measuring without that growth let the
        # loop stop short and the finished payload land over budget.
        block["trimmed_for_budget"] = True
        block["entries_dropped"] = dict(sorted(dropped.items()))

    block["trimmed_for_budget"] = bool(dropped)
    block["entries_dropped"] = dict(sorted(dropped.items()))
    # Writing the count into the payload changes the payload's size by the
    # digits of the count, so settle on a fixed point. Two passes is normally
    # enough; the bound stops a pathological oscillation.
    # Writing the count into the payload changes the payload's size by the
    # digits of the count, so settle on a fixed point. At a digit boundary the
    # two values can oscillate (1949 makes it 1950, 1950 makes it 1949); in that
    # case take the LARGER, because overstating what a caller pays is safe and
    # understating it is not.
    seen: list[int] = []
    final = size(trimmed)
    while final not in seen:
        seen.append(final)
        block["tokens"] = final
        final = size(trimmed)
    reported = max([*seen, final])
    block["tokens"] = reported
    block["budget_exceeded"] = reported > budget
    return trimmed
