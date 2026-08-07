"""Suggested review questions generated from the code-graph analysis.

A reviewer opening an unfamiliar change does not need another metric; they need
to know where to look. This module turns the measured signals from hub and
bridge detection, unexpected-coupling scoring, and knowledge-gap analysis into
concrete questions about specific symbols, each carrying the evidence that
prompted it.

Two deliberate constraints:

- Questions are generated from templates over measured numbers. There is no
  model call, no network, and no randomness, so the same graph always yields the
  same questions in the same order. That makes them reviewable and diffable.
- A question is a prompt for a human, never a finding. The underlying code graph
  is structural and over-approximate, so a question can be about a symbol that
  turns out to be perfectly fine. The wording asks rather than asserts, and the
  ``why`` block says so.

Priority blends a documented per-category weight with the strength of the signal
that produced the question, and both parts are reported next to the number, so
an ordering can be argued with rather than taken on trust.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..core.db import Database
from .analysis import DEFAULT_MAX_NODES

# How much attention each category of question usually deserves, before the
# strength of the individual signal is taken into account. Round, documented
# constants rather than fitted values: a chokepoint that can break many callers
# is worth more of a reviewer's time than a lone unreferenced helper.
CATEGORY_WEIGHTS = {
    "chokepoint": 1.0,
    "untested_hotspot": 0.9,
    "unexpected_coupling": 0.8,
    "hub": 0.7,
    "bridge": 0.6,
    "thin_community": 0.4,
    "isolated": 0.3,
}

_DEFAULT_PER_CATEGORY = 5


def review_questions(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    limit: int = 20,
    per_category: int = _DEFAULT_PER_CATEGORY,
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
    analyses: dict | None = None,
) -> dict:
    """Generate review questions from the graph analysis.

    ``analyses`` lets a caller pass already-computed centrality, coupling, and
    gap results instead of recomputing all three, which matters because a
    command that shows all four views should not walk the graph four times.
    """
    limit = max(1, int(limit))
    per_category = max(1, int(per_category))
    if analyses is None:
        analyses = collect_analyses(
            db,
            tenant_id=tenant_id,
            predicates=predicates,
            resolution=resolution,
            max_nodes=max_nodes,
        )

    hubs = analyses.get("hubs") or {}
    coupling = analyses.get("coupling") or {}
    gaps = analyses.get("gaps") or {}

    questions: list[dict] = []
    questions += _chokepoint_questions(hubs, per_category)
    questions += _hub_questions(hubs, per_category)
    questions += _bridge_questions(hubs, per_category)
    questions += _coupling_questions(coupling, per_category)
    questions += _untested_questions(gaps, per_category)
    questions += _isolated_questions(gaps, per_category)
    questions += _thin_community_questions(gaps, per_category)

    # Highest priority first; ties broken by the stable id so ordering never
    # depends on which analysis happened to run first.
    questions.sort(key=lambda q: (-float(q["priority"]), str(q["id"])))
    selected = questions[:limit]

    by_category: dict[str, int] = {}
    for q in questions:
        by_category[q["category"]] = by_category.get(q["category"], 0) + 1

    return {
        "questions": selected,
        "totals": {
            "generated": len(questions),
            "returned": len(selected),
            "by_category": dict(sorted(by_category.items())),
        },
        "category_weights": dict(sorted(CATEGORY_WEIGHTS.items())),
        "truncated": bool(hubs.get("truncated") or coupling.get("truncated") or gaps.get("truncated")),
        "why": {
            "generation": "deterministic templates over measured graph signals",
            "priority": "category weight multiplied by the normalized strength of the signal",
            "note": (
                "Questions are prompts for a reviewer, not findings. The code "
                "graph is structural and over-approximate, so a question may "
                "concern a symbol that is entirely sound. No model call and no "
                "network are involved."
            ),
        },
    }


def collect_analyses(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
    limit: int = 50,
) -> dict:
    """Run the three analyses this module consumes, once each."""
    from .centrality import hubs_and_bridges
    from .coupling import unexpected_coupling
    from .gaps import knowledge_gaps

    return {
        "hubs": hubs_and_bridges(
            db, tenant_id=tenant_id, predicates=predicates, limit=limit, max_nodes=max_nodes
        ),
        "coupling": unexpected_coupling(
            db,
            tenant_id=tenant_id,
            predicates=predicates,
            limit=limit,
            resolution=resolution,
            max_nodes=max_nodes,
        ),
        "gaps": knowledge_gaps(
            db,
            tenant_id=tenant_id,
            predicates=predicates,
            limit=limit,
            resolution=resolution,
            max_nodes=max_nodes,
        ),
    }


# -- question builders ------------------------------------------------------


def _question(category: str, subject: str, text: str, strength: float, evidence: dict) -> dict:
    strength = max(0.0, min(1.0, float(strength)))
    weight = CATEGORY_WEIGHTS[category]
    return {
        # A stable, readable identity: the same concern about the same symbol
        # keeps the same id across runs, so a question list can be diffed.
        "id": f"{category}:{subject}",
        "category": category,
        "subject": subject,
        "question": text,
        "priority": round(weight * strength, 6),
        "category_weight": weight,
        "signal_strength": round(strength, 6),
        "evidence": evidence,
    }


def _chokepoint_questions(hubs: dict, cap: int) -> list[dict]:
    out: list[dict] = []
    for node in (hubs.get("chokepoints") or [])[:cap]:
        name = node["canonical"]
        out.append(
            _question(
                "chokepoint",
                name,
                (
                    f"{name} is an architectural chokepoint: removing it would split the "
                    f"graph, and it sits on many shortest paths (betweenness "
                    f"{node.get('betweenness')}). What breaks for its {node.get('in_degree')} "
                    "dependents if its behaviour changes, and is that risk acknowledged?"
                ),
                float(node.get("hub_score", 0.0)),
                {
                    "betweenness": node.get("betweenness"),
                    "degree": node.get("degree"),
                    "in_degree": node.get("in_degree"),
                    "out_degree": node.get("out_degree"),
                    "path": node.get("path"),
                    "articulation_point": True,
                },
            )
        )
    return out


def _hub_questions(hubs: dict, cap: int) -> list[dict]:
    chokepoints = {n["canonical"] for n in (hubs.get("chokepoints") or [])}
    out: list[dict] = []
    for node in hubs.get("hubs") or []:
        name = node["canonical"]
        # A chokepoint already has a sharper question; do not ask twice.
        if name in chokepoints:
            continue
        if len(out) >= cap:
            break
        out.append(
            _question(
                "hub",
                name,
                (
                    f"{name} is one of the most connected symbols in the graph "
                    f"({node.get('in_degree')} dependents, {node.get('out_degree')} "
                    "dependencies). Is its contract stable enough to carry that fan-in, "
                    "and is the change here backwards compatible for all of them?"
                ),
                float(node.get("hub_score", 0.0)),
                {
                    "degree": node.get("degree"),
                    "in_degree": node.get("in_degree"),
                    "out_degree": node.get("out_degree"),
                    "betweenness": node.get("betweenness"),
                    "path": node.get("path"),
                },
            )
        )
    return out


def _bridge_questions(hubs: dict, cap: int) -> list[dict]:
    bridges = (hubs.get("bridges") or {}).get("bridge_edges") or []
    out: list[dict] = []
    for edge in bridges[:cap]:
        frm, to = edge.get("from"), edge.get("to")
        subject = f"{frm}->{to}"
        out.append(
            _question(
                "bridge",
                subject,
                (
                    f"The link {frm} -> {to} ({edge.get('predicate')}) is the only "
                    "structural connection between those two parts of the graph. Is that "
                    "single point of contact deliberate, and is it the right seam?"
                ),
                1.0,
                {"from": frm, "to": to, "predicate": edge.get("predicate")},
            )
        )
    return out


def _coupling_questions(coupling: dict, cap: int) -> list[dict]:
    out: list[dict] = []
    for edge in (coupling.get("couplings") or [])[:cap]:
        frm, to = edge.get("from"), edge.get("to")
        subject = f"{frm}->{to}"
        reasons = ", ".join(str(s.get("name")) for s in edge.get("signals") or [])
        out.append(
            _question(
                "unexpected_coupling",
                subject,
                (
                    f"{frm} depends on {to}, which is surprising given the surrounding "
                    f"structure ({reasons}). Is this coupling intended, or should it go "
                    "through an existing boundary?"
                ),
                float(edge.get("score", 0.0)),
                {
                    "from": frm,
                    "to": to,
                    "predicate": edge.get("predicate"),
                    "score": edge.get("score"),
                    "signals": edge.get("signals"),
                    "from_language": edge.get("from_language"),
                    "to_language": edge.get("to_language"),
                },
            )
        )
    return out


def _untested_questions(gaps: dict, cap: int) -> list[dict]:
    hotspots = (gaps.get("untested_hotspots") or [])[:cap]
    # Normalize against the busiest hotspot in this graph, so strength means
    # "relative to what this repository actually looks like".
    busiest = max((int(h.get("inbound_calls", 0)) for h in hotspots), default=0)
    out: list[dict] = []
    for node in hotspots:
        name = node["canonical"]
        inbound = int(node.get("inbound_calls", 0))
        out.append(
            _question(
                "untested_hotspot",
                name,
                (
                    f"{name} is called by {inbound} other symbols and has no test edge in "
                    "the graph. What covers it today, and should this change add a test "
                    "before it lands?"
                ),
                (inbound / busiest) if busiest else 0.0,
                {
                    "inbound_calls": inbound,
                    "callers": node.get("callers"),
                    "path": node.get("path"),
                },
            )
        )
    return out


def _isolated_questions(gaps: dict, cap: int) -> list[dict]:
    out: list[dict] = []
    for node in (gaps.get("isolated") or [])[:cap]:
        name = node["canonical"]
        out.append(
            _question(
                "isolated",
                name,
                (
                    f"{name} has no structural link to anything else in the graph. Is it "
                    "dead code, an entry point reached some other way, or something the "
                    "parser could not resolve?"
                ),
                1.0,
                {"kind": node.get("kind"), "path": node.get("path"), "language": node.get("language")},
            )
        )
    return out


def _thin_community_questions(gaps: dict, cap: int) -> list[dict]:
    out: list[dict] = []
    for community in (gaps.get("thin_communities") or [])[:cap]:
        cid = community.get("community")
        subject = f"community-{cid}"
        members = community.get("members") or []
        preview = ", ".join(str(m) for m in members[:3])
        out.append(
            _question(
                "thin_community",
                subject,
                (
                    f"Cluster {cid} holds {community.get('size')} symbols ({preview}) with "
                    f"internal density {community.get('density')}. Is it a coherent unit, "
                    "or several unrelated things that happen to sit together?"
                ),
                1.0 - float(community.get("density", 0.0)),
                {
                    "community": cid,
                    "size": community.get("size"),
                    "density": community.get("density"),
                    "internal_edges": community.get("internal_edges"),
                    "members": members,
                },
            )
        )
    return out
