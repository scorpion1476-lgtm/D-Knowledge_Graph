"""Unexpected-coupling scoring over the shared code graph.

A dependency is "unexpected" when the rest of the graph would not have predicted
it: it leaves its own cluster, it crosses a language boundary, or it wires a
peripheral symbol straight into a hub. This module scores every structural edge
on those three named signals and returns the highest-scoring ones.

The output is ADVISORY and heuristic. Three things it rests on are themselves
approximations: the community partition is a modularity optimization over a
name-based structural graph, the language tags come from the parser rather than
from any resolved import, and the degree thresholds are read off this one graph's
own degree distribution. A flagged edge is a prompt to look, not a defect
finding, and a low score is not evidence that an edge is fine.

Nothing here writes to the database and nothing here is plane-shared logic: it
reads the code plane through the shared analysis view and adds no store of its
own. Every returned list is sorted by an explicit key so the same database always
yields the same output.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from ..core.db import Database
from .analysis import DEFAULT_MAX_NODES, STRUCTURAL_PREDICATES, CodeEdge, CodeGraphView, load_code_graph

SIGNAL_CROSS_COMMUNITY = "cross_community"
SIGNAL_CROSS_LANGUAGE = "cross_language"
SIGNAL_PERIPHERY_TO_HUB = "periphery_to_hub"

# Additive weights, summing to 1.0, so a score is a plain fraction of the total
# available evidence and a reader can subtract a signal they disagree with.
#
# cross_community is weighted highest because the partition is derived from the
# whole graph's link structure, so an edge leaving its cluster is the surprise
# with the most context behind it.
#
# cross_language sits just below it deliberately. A cross-language edge really is
# interesting, but in this graph it can only have come from name-based reference
# resolution (two symbols in different languages sharing a short name), so a
# share of these edges are resolution artifacts rather than real dependencies.
# Ranking this signal top would put artifacts above real architecture.
#
# periphery_to_hub is the weakest because a leaf reaching a widely used utility
# is both common and usually benign; it earns attention only in company.
SIGNAL_WEIGHTS: dict[str, float] = {
    SIGNAL_CROSS_COMMUNITY: 0.45,
    SIGNAL_CROSS_LANGUAGE: 0.35,
    SIGNAL_PERIPHERY_TO_HUB: 0.20,
}

# Quantiles of the graph's own degree distribution. These fix only WHERE in the
# observed distribution the two bands are cut; the degree values they resolve to
# are read off the graph in front of us, never carried in from a tuned corpus.
PERIPHERAL_QUANTILE = 0.25
HUB_QUANTILE = 0.90

# Scores and confidences are rounded so repeated runs and serialized output are
# byte-stable rather than differing in float noise.
_DECIMALS = 6
_MAX_LIMIT = 1000


def _nearest_rank(values: Sequence[int], quantile: float) -> int:
    """Nearest-rank quantile over an already sorted sequence of degrees.

    Nearest rank is used rather than an interpolating percentile because a degree
    is a count. An interpolated threshold of 3.5 is a degree no node can hold, so
    the "at or below" and "at or above" band tests would never land exactly on an
    observed value and the reported threshold would not be explainable against
    any real node. Nearest rank always returns a degree some node actually has.
    """
    if not values:
        return 0
    rank = math.ceil(quantile * len(values))
    index = min(max(rank - 1, 0), len(values) - 1)
    return values[index]


def _degree_thresholds(degrees: dict[str, int]) -> dict:
    """Derive the peripheral and hub degree bands from this graph's degrees.

    Only nodes with at least one edge under the selected predicates are sampled.
    Isolated nodes (a module nobody imports, under a call-only selection, for
    instance) are real graph members but they can never be an endpoint of a
    scored edge, so including their zero degrees would drag the peripheral
    threshold to 0 and silently disable the signal for every graph that has a few
    of them.

    A pure star exposes an edge case worth naming: with nine leaves and one hub,
    nine tenths of the sampled degrees are 1, so the hub quantile lands on 1 too
    and the two bands collapse onto each other. Rather than accept a threshold
    pair that classifies every node as both peripheral and hub, the hub band is
    raised to the next degree actually observed above the peripheral band. That
    value still comes from the graph. When no degree at all sits above the
    peripheral band (every connected node has the same degree) there is no
    periphery and no hub to speak of, so the spread flag stays false and the
    signal is reported as unavailable instead of firing on everything.
    """
    connected = sorted(d for d in degrees.values() if d > 0)
    peripheral = _nearest_rank(connected, PERIPHERAL_QUANTILE)
    at_hub_quantile = _nearest_rank(connected, HUB_QUANTILE)
    raised = False
    hub = at_hub_quantile
    if hub <= peripheral:
        above = [d for d in connected if d > peripheral]
        if above:
            hub = min(above)
            raised = True
    return {
        "peripheral_degree": peripheral,
        "hub_degree": hub,
        "percentiles": {
            "peripheral_quantile": PERIPHERAL_QUANTILE,
            "hub_quantile": HUB_QUANTILE,
            "degree_at_hub_quantile": at_hub_quantile,
            "hub_raised_to_next_observed_degree": raised,
        },
        "degree_spread": hub > peripheral,
        "sample_size": len(connected),
    }


def _score_edge(
    view: CodeGraphView,
    edge: CodeEdge,
    communities: dict[str, int],
    degrees: dict[str, int],
    thresholds: dict,
) -> dict | None:
    """Score one edge, or return None when no signal fires.

    Signals are appended in a fixed declaration order rather than sorted by
    contribution, so the same edge always explains itself the same way and a
    reader can scan a column of results without the reasons shuffling.
    """
    subject, obj = edge.subject_id, edge.object_id
    signals: list[dict] = []

    subject_community = communities.get(subject, -1)
    object_community = communities.get(obj, -1)
    if subject_community != object_community:
        signals.append(
            {
                "name": SIGNAL_CROSS_COMMUNITY,
                "contribution": SIGNAL_WEIGHTS[SIGNAL_CROSS_COMMUNITY],
                "detail": f"community {subject_community} to community {object_community}: the edge leaves its own cluster",
            }
        )

    subject_language = view.language_of(subject)
    object_language = view.language_of(obj)
    # An unknown language is not evidence of a boundary, so a blank tag on either
    # end suppresses the signal rather than inventing a crossing.
    if subject_language and object_language and subject_language != object_language:
        signals.append(
            {
                "name": SIGNAL_CROSS_LANGUAGE,
                "contribution": SIGNAL_WEIGHTS[SIGNAL_CROSS_LANGUAGE],
                "detail": (
                    f"{subject_language} to {object_language}: cross-language edges here come from name-based "
                    "reference resolution, so some are resolution artifacts rather than real dependencies"
                ),
            }
        )

    subject_degree = degrees.get(subject, 0)
    object_degree = degrees.get(obj, 0)
    if thresholds["degree_spread"]:
        low = thresholds["peripheral_degree"]
        high = thresholds["hub_degree"]
        # Direction-free: either end may be the peripheral one. The first match
        # wins so the signal contributes its weight once, never twice.
        for near, near_degree, far, far_degree in (
            (subject, subject_degree, obj, object_degree),
            (obj, object_degree, subject, subject_degree),
        ):
            if near_degree <= low and far_degree >= high:
                signals.append(
                    {
                        "name": SIGNAL_PERIPHERY_TO_HUB,
                        "contribution": SIGNAL_WEIGHTS[SIGNAL_PERIPHERY_TO_HUB],
                        "detail": (
                            f"{view.label(near)} has degree {near_degree} at or below the peripheral band {low}, "
                            f"{view.label(far)} has degree {far_degree} at or above the hub band {high}"
                        ),
                    }
                )
                break

    if not signals:
        return None
    # The weights sum to 1.0, so the clamp is a guard rather than a correction;
    # it keeps the contract (0.0 to 1.0) true even if the weights are retuned.
    score = min(1.0, max(0.0, round(sum(s["contribution"] for s in signals), _DECIMALS)))
    return {
        "from": view.label(subject),
        "to": view.label(obj),
        "predicate": edge.predicate,
        "confidence": round(float(edge.weight), _DECIMALS),
        "score": score,
        "signals": signals,
        "from_language": subject_language,
        "to_language": object_language,
        "from_community": subject_community,
        "to_community": object_community,
    }


def unexpected_coupling(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    limit: int = 20,
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict:
    """Flag structural edges that the rest of the graph would not have predicted.

    Every selected edge is scored on three independently explainable signals:
    ``cross_community`` (the endpoints sit in different communities of the
    modularity-optimization partition), ``cross_language`` (the endpoints carry
    different language tags), and ``periphery_to_hub`` (one endpoint is in the
    graph's peripheral degree band and the other is in its hub band). An edge on
    which no signal fires scores 0 and is not returned, because a zero row would
    say nothing and would bury the rows that do.

    Results are ordered by score descending and then by endpoint name ascending,
    so equal scores never reorder between runs. The empty graph, the single-node
    graph, and a graph where every connected node has the same degree all return
    normally; the last of those reports ``degree_spread`` false and the
    periphery-to-hub signal simply does not fire.
    """
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    preds = tuple(STRUCTURAL_PREDICATES) if predicates is None else tuple(sorted(set(predicates)))
    limit = max(1, min(int(limit), _MAX_LIMIT))
    resolution = float(resolution)

    communities = view.communities(preds, resolution=resolution)
    degrees = {node_id: view.degree(node_id, preds) for node_id in view.node_ids()}
    thresholds = _degree_thresholds(degrees)

    selected = view.edges_for(preds)
    scored: list[dict] = []
    for edge in selected:
        if edge.subject_id == edge.object_id:
            continue  # a self reference couples a symbol to itself and surprises nobody
        row = _score_edge(view, edge, communities, degrees, thresholds)
        if row is not None:
            scored.append(row)
    scored.sort(key=lambda r: (-r["score"], r["from"], r["to"], r["predicate"]))

    return {
        "couplings": scored[:limit],
        "totals": {
            "nodes": len(view),
            "edges": len(selected),
            "scored_edges": len(scored),
            "communities": len(set(communities.values())),
        },
        "thresholds": thresholds,
        "weights": dict(SIGNAL_WEIGHTS),
        "truncated": view.truncated,
        "why": {
            "predicates": list(preds),
            "resolution": resolution,
            "signals": [SIGNAL_CROSS_COMMUNITY, SIGNAL_CROSS_LANGUAGE, SIGNAL_PERIPHERY_TO_HUB],
            "thresholds_derived_from": "the observed degree distribution of this graph's connected nodes, not tuned constants",
            "note": (
                "advisory heuristic, over-approximate: the community partition, the language tags, and the degree "
                "bands are all read off a name-based structural graph, so a flagged edge is a prompt to look rather "
                "than a defect finding, and an unflagged edge is not thereby shown to be sound"
            ),
        },
    }
