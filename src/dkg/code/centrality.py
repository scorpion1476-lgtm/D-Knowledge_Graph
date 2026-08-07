"""Hub, bridge, and chokepoint detection over the shared code graph.

Two questions this answers about an unfamiliar codebase. Which symbols is
everything wired through, and which single symbols or single edges, if removed,
would split the graph in two? The first is a popularity question and degree
alone answers it badly, because a node can have a modest neighbour count and
still sit on almost every path between two halves of the system. The second is a
connectivity question that degree cannot answer at all.

So the module computes real shortest-path betweenness (Brandes, unweighted BFS
variant) alongside degree, and a low-link depth-first pass for articulation
points and bridge edges. Everything runs on the UNDIRECTED projection of the
selected predicates: a cut is a cut regardless of which way the call points, and
betweenness over the directed graph would report zero for a node that is plainly
a chokepoint simply because the callers all point inward.

The low-link pass is written iteratively with an explicit stack. A real code
graph can contain a call or import chain thousands of nodes deep, and a
recursive depth-first pass would raise RecursionError on exactly the large
repository where this analysis is most useful.

Results are advisory. They inherit the over-approximation of the underlying
graph (name-based reference resolution, no dynamic dispatch modelling), so a
chokepoint here is a structural cut and not a proven runtime dependency. The
`why` block on every result says so.

Read-only and deterministic: nothing is written back, every list is sorted by an
explicit key, and every accumulation runs in sorted order so the same database
always produces byte-identical output.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence

from ..core.db import Database
from .analysis import DEFAULT_MAX_NODES, STRUCTURAL_PREDICATES, CodeGraphView, load_code_graph

# Blend constants for the combined hub score. Betweenness carries the larger
# share because position beats popularity: a node on many shortest paths is an
# architectural fact, whereas a high neighbour count can just be a utility
# module that everything happens to touch. Degree keeps a real share because a
# node with enormous fan-in is a hub even when it lies on no shortest path at
# all. These are fixed round numbers chosen for that reasoning, not fitted to
# any corpus, and they sum to one so the score stays inside 0.0 to 1.0.
HUB_BETWEENNESS_WEIGHT = 0.6
HUB_DEGREE_WEIGHT = 0.4

# A chokepoint is an articulation point that also carries real path traffic. The
# tier test is relative to the graph's own maximum betweenness so it is
# scale-free: it means the same thing on a 50-node graph and a 50000-node one.
# A quarter of the maximum is a deliberately blunt round threshold.
CHOKEPOINT_BETWEENNESS_FRACTION = 0.25

# Brandes costs one breadth-first sweep per source, so an exact run is
# O(nodes * (nodes + edges)) and grows quadratically. Exact is kept while the
# sweep count stays in the low thousands; above that a fixed source budget holds
# the cost linear in graph size, and the result is reported as an estimate
# rather than being silently presented as exact. Measured on this machine, both
# bounds land in the seconds, not the minutes.
EXACT_BETWEENNESS_MAX_NODES = 1000
BETWEENNESS_SOURCE_BUDGET = 500

# Published scores are rounded so the ordering of a result is fully explicable
# from the numbers a caller can see, rather than from hidden trailing digits.
SCORE_PRECISION = 6


# -- graph primitives -------------------------------------------------------


def _undirected(adjacency: Mapping[str, Iterable[str]]) -> dict[str, set[str]]:
    """Symmetric, self-loop-free copy of an adjacency mapping.

    Both algorithms below are only meaningful on an undirected graph, so the
    input is symmetrised here rather than each caller being trusted to have done
    it. A neighbour that is not itself a key is dropped: an edge pointing outside
    the loaded node set would otherwise make a traversal walk off the end of the
    graph and raise instead of returning a bounded answer.
    """
    known = set(adjacency)
    out: dict[str, set[str]] = {v: set() for v in known}
    for v in sorted(known):
        for w in adjacency.get(v, ()):
            if w == v or w not in known:
                continue
            out[v].add(w)
            out[w].add(v)
    return out


def connected_components(adjacency: Mapping[str, Iterable[str]]) -> list[list[str]]:
    """Connected components of the undirected projection, largest first.

    An isolated node is its own component. Reporting it that way is the honest
    reading of a code graph: a symbol nothing references and that references
    nothing really is a separate island, and hiding it would understate how
    fragmented the graph is.
    """
    adj = _undirected(adjacency)
    seen: set[str] = set()
    components: list[list[str]] = []
    for start in sorted(adj):
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        members: list[str] = []
        while stack:
            v = stack.pop()
            members.append(v)
            for w in sorted(adj[v]):
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        components.append(sorted(members))
    return sorted(components, key=lambda c: (-len(c), c[0]))


def betweenness_centrality(
    adjacency: Mapping[str, Iterable[str]],
    *,
    sources: Sequence[str] | None = None,
) -> dict[str, float]:
    """Normalized shortest-path betweenness by Brandes' algorithm.

    One breadth-first sweep per source builds the shortest-path counts and the
    predecessor sets, then a second pass back down the sweep order accumulates
    each node's dependency. That is the whole point of Brandes: it gets exact
    betweenness for the cost of n traversals instead of enumerating paths.

    Normalization. The accumulation visits each unordered pair twice on an
    undirected graph, once from each end, so the raw sum is already double the
    pair-counted score. Dividing by (n-1)*(n-2) therefore divides the true
    pair count by its undirected maximum, (n-1)*(n-2)/2, and yields 1.0 for a
    node that lies on every shortest path between every other pair. n is every
    node in the graph handed in, isolated ones included, so the scale does not
    shift when a caller widens or narrows the predicate selection.

    Passing `sources` runs the estimator variant: only those nodes seed a sweep
    and the result is scaled by n/len(sources). That is an approximation and
    callers must label it as one. Fewer than three nodes cannot have a node
    between two others, so the score is zero everywhere by definition.
    """
    adj = _undirected(adjacency)
    nodes = sorted(adj)
    n = len(nodes)
    score: dict[str, float] = dict.fromkeys(nodes, 0.0)
    if n < 3:
        return score

    seeds = nodes if sources is None else [s for s in sources if s in adj]
    if not seeds:
        return score

    for s in seeds:
        order: list[str] = []
        preds: dict[str, list[str]] = {v: [] for v in nodes}
        sigma: dict[str, float] = dict.fromkeys(nodes, 0.0)
        dist: dict[str, int] = dict.fromkeys(nodes, -1)
        sigma[s] = 1.0
        dist[s] = 0
        queue: deque[str] = deque([s])
        while queue:
            v = queue.popleft()
            order.append(v)
            for w in sorted(adj[v]):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                # A neighbour exactly one level deeper is on a shortest path
                # through v, whether it was discovered just now or earlier by a
                # different branch of the same level.
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    preds[w].append(v)
        delta: dict[str, float] = dict.fromkeys(nodes, 0.0)
        for w in reversed(order):
            coefficient = (1.0 + delta[w]) / sigma[w]
            for v in preds[w]:
                delta[v] += sigma[v] * coefficient
            if w != s:
                score[w] += delta[w]

    estimate = n / len(seeds)
    scale = estimate / ((n - 1) * (n - 2))
    return {v: score[v] * scale for v in nodes}


def articulation_points_and_bridges(
    adjacency: Mapping[str, Iterable[str]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Articulation points and bridge edges by an iterative low-link pass.

    Each node records its discovery index and the lowest index reachable from
    its subtree using at most one back edge. A child whose low-link cannot climb
    above its parent's discovery index has no way around that parent, which is
    exactly what makes the parent a cut vertex and, when the child cannot even
    reach the parent's own index, makes the connecting edge a bridge.

    The depth-first walk is an explicit stack of (node, parent, neighbour
    iterator) frames rather than recursion, because a code graph can nest far
    deeper than the interpreter's recursion limit and the analysis must not fail
    on the large repositories it exists to describe. The outer loop restarts on
    every undiscovered node, so a disconnected graph is fully covered and each
    component gets its own root treated by the root rule (a root is a cut vertex
    only when it has more than one depth-first child).

    Returns sorted node ids and sorted (low, high) node id pairs.
    """
    adj = _undirected(adjacency)
    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    articulation: set[str] = set()
    bridges: set[tuple[str, str]] = set()
    timer = 0

    for root in sorted(adj):
        if root in discovery:
            continue
        discovery[root] = low[root] = timer
        timer += 1
        root_children = 0
        stack: list[tuple[str, str | None, Iterable[str]]] = [(root, None, iter(sorted(adj[root])))]
        while stack:
            node, parent, neighbours = stack[-1]
            descended = False
            for other in neighbours:
                if other not in discovery:
                    discovery[other] = low[other] = timer
                    timer += 1
                    if node == root:
                        root_children += 1
                    stack.append((other, node, iter(sorted(adj[other]))))
                    descended = True
                    break
                if other != parent:
                    # Back edge: the subtree can climb to an already discovered
                    # node without going through the parent.
                    low[node] = min(low[node], discovery[other])
            if descended:
                continue
            stack.pop()
            if not stack:
                continue
            above = stack[-1][0]
            low[above] = min(low[above], low[node])
            if low[node] > discovery[above]:
                bridges.add((above, node) if above < node else (node, above))
            if above != root and low[node] >= discovery[above]:
                articulation.add(above)
        if root_children > 1:
            articulation.add(root)

    return sorted(articulation), sorted(bridges)


# -- the public analysis ----------------------------------------------------


def hubs_and_bridges(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    limit: int = 20,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict:
    """Rank the code graph's hubs and report its structural chokepoints.

    Loads the code plane once through the shared view, then answers all of it
    off that single in-memory projection: repeated database work would be both
    slower and a chance for the graph to change underneath the analysis, which
    would break the determinism guarantee.
    """
    limit = max(1, min(int(limit), 1000))
    selection = tuple(STRUCTURAL_PREDICATES) if predicates is None else tuple(sorted(set(predicates)))
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    return analyse_view(view, predicates=selection, limit=limit)


def analyse_view(
    view: CodeGraphView,
    *,
    predicates: Iterable[str] | None = None,
    limit: int = 20,
) -> dict:
    """The analysis itself, over an already-loaded view.

    Split out from `hubs_and_bridges` so the maths can be exercised against a
    constructed view without a database round trip, and so a caller that already
    holds a view for another feature does not pay to load it twice.
    """
    limit = max(1, min(int(limit), 1000))
    selection = tuple(STRUCTURAL_PREDICATES) if predicates is None else tuple(sorted(set(predicates)))

    node_ids = view.node_ids()
    node_count = len(node_ids)
    undirected = view.undirected_adjacency(selection)
    out_adjacency = view.out_adjacency(selection)
    in_adjacency = view.in_adjacency(selection)

    pair_edges = _pair_edges(view, selection)
    components = connected_components(undirected)
    seeds, sampled = _betweenness_seeds(list(node_ids))
    betweenness = betweenness_centrality(undirected, sources=seeds)
    articulation, bridge_pairs = articulation_points_and_bridges(undirected)

    degree_scale = float(node_count - 1) if node_count > 1 else 0.0
    records: dict[str, dict] = {}
    for entity_id in node_ids:
        degree = len(undirected.get(entity_id, ()))
        centrality = betweenness.get(entity_id, 0.0)
        degree_norm = (degree / degree_scale) if degree_scale else 0.0
        records[entity_id] = _record(
            view,
            entity_id,
            degree=degree,
            in_degree=len(in_adjacency.get(entity_id, ())),
            out_degree=len(out_adjacency.get(entity_id, ())),
            betweenness=centrality,
            hub_score=HUB_BETWEENNESS_WEIGHT * centrality + HUB_DEGREE_WEIGHT * degree_norm,
        )

    # A node with no neighbour in the selected projection is not a hub by any
    # reading of the word, so it is ranked out rather than padding the list with
    # zero-scored entries.
    ranked_hubs = sorted((r for r in records.values() if r["degree"] > 0), key=_rank_key)
    articulation_records = sorted((records[e] for e in articulation if e in records), key=_cut_key)

    peak = max((r["betweenness"] for r in records.values()), default=0.0)
    threshold = CHOKEPOINT_BETWEENNESS_FRACTION * peak
    chokepoints = [r for r in articulation_records if peak > 0.0 and r["betweenness"] >= threshold]

    bridge_records = sorted(
        (_bridge_record(view, pair, pair_edges.get(pair, [])) for pair in bridge_pairs),
        key=lambda b: (b["from"], b["to"]),
    )

    return {
        "hubs": ranked_hubs[:limit],
        "bridges": {
            "articulation_points": articulation_records[:limit],
            "bridge_edges": bridge_records[:limit],
        },
        "chokepoints": chokepoints[:limit],
        "totals": {
            "nodes": node_count,
            # Distinct unordered node pairs in the undirected projection, which
            # is what the analysis actually walks. Named for what it is: two
            # symbols joined by a call AND an import are one pair, not two, and
            # calling this "edges" invited the wrong reading.
            "edge_pairs": len(pair_edges),
            "stored_edges": len(view.edges_for(selection)),
            "components": len(components),
            "articulation_points": len(articulation_records),
            "bridge_edges": len(bridge_records),
            "chokepoints": len(chokepoints),
        },
        "truncated": view.truncated,
        "why": {
            "algorithms": {
                "betweenness": (
                    "Brandes shortest-path betweenness, unweighted breadth-first variant, "
                    + ("estimated from a deterministic stride sample of sources" if sampled else "exact over every source")
                ),
                "degree": "undirected neighbour count, with directed in-degree (dependents) and out-degree (dependencies) reported alongside",
                "articulation_points": "iterative depth-first low-link pass, restarted on every component",
                "bridge_edges": "same low-link pass; an edge whose child subtree cannot reach the parent's discovery index",
                "hub_score": f"{HUB_BETWEENNESS_WEIGHT} * normalized betweenness + {HUB_DEGREE_WEIGHT} * normalized degree",
                "chokepoints": (
                    f"articulation point whose normalized betweenness is at least {CHOKEPOINT_BETWEENNESS_FRACTION} "
                    "of the highest normalized betweenness in the graph"
                ),
            },
            "predicates": list(selection),
            "normalization": (
                "betweenness: accumulated pair dependencies divided by (n-1)*(n-2), which is the undirected maximum, "
                "so 1.0 means the node lies on every shortest path between every other pair; "
                "degree: neighbour count divided by n-1; n counts every node in the loaded view, isolated ones included"
            ),
            "projection": "undirected; edge direction is discarded because a structural cut is a cut whichever way the reference points",
            "betweenness_sources": (len(seeds) if seeds is not None else node_count),
            "limit": limit,
            "note": (
                "advisory and over-approximate: the underlying code graph is structural (name-based reference resolution, "
                "dynamic dispatch not modelled), so a hub rank or a chokepoint is a structural signal and not a proven "
                "runtime dependency"
                + (
                    "; betweenness here is a sampled estimate, not an exact score, because the graph exceeded the exact-run node budget"
                    if sampled
                    else ""
                )
            ),
            "lists_capped": "hubs, articulation_points, bridge_edges, and chokepoints are capped at the limit; totals report the complete counts",
        },
    }


# -- helpers ----------------------------------------------------------------


def _rank_key(record: dict) -> tuple[float, float, str]:
    """Hub ordering: score, then betweenness, then name, all from published values."""
    return (-record["hub_score"], -record["betweenness"], record["canonical"])


def _cut_key(record: dict) -> tuple[float, float, str]:
    """Cut-vertex ordering: the ones carrying the most path traffic first."""
    return (-record["betweenness"], -record["hub_score"], record["canonical"])


def _betweenness_seeds(nodes: list[str]) -> tuple[list[str] | None, bool]:
    """Sources for the Brandes sweep, and whether the run is an estimate.

    Below the budget every node seeds a sweep and the score is exact. Above it
    the sources are taken at a fixed stride across the sorted node ids rather
    than as a prefix: a prefix of content-addressed ids is arbitrary but
    clustered, whereas a stride spreads the sample across the whole id space.
    The choice is deterministic either way, which a random sample would not be.
    """
    if len(nodes) <= EXACT_BETWEENNESS_MAX_NODES:
        return None, False
    stride = (len(nodes) + BETWEENNESS_SOURCE_BUDGET - 1) // BETWEENNESS_SOURCE_BUDGET
    return nodes[::stride], True


def _pair_edges(view: CodeGraphView, predicates: Iterable[str]) -> dict[tuple[str, str], list[tuple[str, str, str]]]:
    """Unordered node pair to the directed edges that produced it.

    The undirected projection collapses direction and parallel predicates, but a
    reported bridge is more useful with both back, so the mapping is kept.
    """
    acc: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for edge in view.edges_for(predicates):
        if edge.subject_id == edge.object_id:
            continue
        pair = (edge.subject_id, edge.object_id) if edge.subject_id < edge.object_id else (edge.object_id, edge.subject_id)
        acc[pair].append((edge.subject_id, edge.predicate, edge.object_id))
    return dict(acc)


def _record(
    view: CodeGraphView,
    entity_id: str,
    *,
    degree: int,
    in_degree: int,
    out_degree: int,
    betweenness: float,
    hub_score: float,
) -> dict:
    node = view.get(entity_id)
    return {
        "canonical": node.canonical if node else entity_id,
        "display": node.display if node else entity_id,
        "kind": node.kind if node else "",
        "path": node.path if node else "",
        "language": node.language if node else "",
        "degree": degree,
        "in_degree": in_degree,
        "out_degree": out_degree,
        "betweenness": round(betweenness, SCORE_PRECISION),
        "hub_score": round(hub_score, SCORE_PRECISION),
    }


def _bridge_record(view: CodeGraphView, pair: tuple[str, str], directed: list[tuple[str, str, str]]) -> dict:
    """One bridge edge, oriented by its underlying directed edges.

    The pair itself is unordered, so the reported from/to is taken from the
    first directed edge in canonical-name order. When the pair is joined by more
    than one predicate all of them are listed, because collapsing a call plus an
    inherit into a single named predicate would misreport the graph.
    """
    low, high = pair
    if directed:
        subject, predicate, obj = sorted(directed, key=lambda d: (view.label(d[0]), d[1], view.label(d[2])))[0]
    else:
        subject, predicate, obj = low, "", high
    return {
        "from": view.label(subject),
        "to": view.label(obj),
        "predicate": predicate,
        "predicates": sorted({d[1] for d in directed}),
    }
