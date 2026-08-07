"""Shared in-memory view of the code graph for the graph-analysis features.

Hub and bridge detection, unexpected-coupling scoring, knowledge-gap analysis,
the architecture map, and graph diffing all need the same thing first: the code
plane's nodes and edges pulled out of the shared store into adjacency structures
that can be walked repeatedly without re-querying. This module is that shared
substrate, so each feature holds one analysis idea instead of its own loader.

The view is read-only, bounded by a node cap, and deterministic: every list it
returns is in a stable sorted order, so the same database always produces the
same analysis output. Nothing here is code-plane-specific logic beyond selecting
the ``code:`` kinds and predicates; the shared core is untouched.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ..core.db import Database

# Reference edges: one symbol reaching another. These carry the architecture.
STRUCTURAL_PREDICATES = ("code:calls", "code:imports", "code:inherits")
# Containment (parent defines child) and test linkage are real edges but are not
# reference flow, so they are loaded and kept addressable separately.
CONTAINMENT_PREDICATE = "code:defines"
TESTED_BY_PREDICATE = "code:tested_by"
ALL_PREDICATES = (*STRUCTURAL_PREDICATES, CONTAINMENT_PREDICATE, TESTED_BY_PREDICATE)

# Kinds that represent a callable or type definition, as opposed to a file.
SYMBOL_KINDS = ("code:function", "code:method", "code:class", "code:type", "code:test")
MODULE_KIND = "code:module"

DEFAULT_MAX_NODES = 20000

# Edge budget per capped node. A code graph is sparse, so this is generous, and
# it keeps a pathological store from making the edge read unbounded even when
# the node read is capped.
MAX_EDGES_PER_NODE = 200


@dataclass(frozen=True)
class CodeNode:
    """One code entity as the analysis layer sees it."""

    entity_id: str
    canonical: str
    display: str
    kind: str
    path: str
    language: str
    # The definition's line span, as the parser recorded it. Loaded here rather
    # than re-read per feature, because the size query, the rename preview, and
    # the wiki all need it and the rule is that nothing re-implements the loader.
    # Zero means the parser recorded no span, which is different from a one-line
    # definition and is reported as unknown rather than as a length of one.
    start_line: int = 0
    end_line: int = 0

    @property
    def is_module(self) -> bool:
        return self.kind == MODULE_KIND

    @property
    def is_test(self) -> bool:
        return self.kind == "code:test"

    @property
    def line_count(self) -> int:
        """Lines the definition spans, or 0 when the span is unknown.

        An unknown span is reported as 0 rather than guessed at 1, so a symbol
        whose parser recorded no lines is never ranked as though it were small.
        """
        if self.start_line <= 0 or self.end_line < self.start_line:
            return 0
        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class CodeEdge:
    """One code relationship, with the stored confidence as its weight."""

    subject_id: str
    predicate: str
    object_id: str
    weight: float


class CodeGraphView:
    """Nodes, edges, and derived adjacency for one tenant's code graph.

    Adjacency is built lazily per predicate selection and cached, because the
    analysis features ask for different slices (calls only, all structural
    edges, structural plus containment) and each slice is walked many times.
    """

    def __init__(self, nodes: dict[str, CodeNode], edges: list[CodeEdge], *, truncated: bool = False) -> None:
        self.nodes = nodes
        self.edges = tuple(edges)
        self.truncated = truncated
        self._node_ids = tuple(sorted(nodes))
        self._out_cache: dict[tuple[str, ...], dict[str, list[str]]] = {}
        self._in_cache: dict[tuple[str, ...], dict[str, list[str]]] = {}
        self._undirected_cache: dict[tuple[str, ...], dict[str, set[str]]] = {}

    # -- basic accessors ----------------------------------------------------

    def __len__(self) -> int:
        return len(self.nodes)

    @property
    def is_empty(self) -> bool:
        return not self.nodes

    def node_ids(self) -> tuple[str, ...]:
        """Every node id, sorted, so downstream iteration is deterministic."""
        return self._node_ids

    def get(self, entity_id: str) -> CodeNode | None:
        return self.nodes.get(entity_id)

    def label(self, entity_id: str) -> str:
        """The canonical name for a node, falling back to its id."""
        node = self.nodes.get(entity_id)
        return node.canonical if node else entity_id

    def language_of(self, entity_id: str) -> str:
        node = self.nodes.get(entity_id)
        return node.language if node else ""

    def path_of(self, entity_id: str) -> str:
        node = self.nodes.get(entity_id)
        return node.path if node else ""

    def symbol_ids(self) -> list[str]:
        """Definition nodes only, excluding file-level module nodes."""
        return [n for n in self._node_ids if self.nodes[n].kind in SYMBOL_KINDS]

    def languages(self) -> list[str]:
        return sorted({n.language for n in self.nodes.values() if n.language})

    # -- edge selection -----------------------------------------------------

    @staticmethod
    def _key(predicates: Iterable[str] | None) -> tuple[str, ...]:
        if predicates is None:
            return STRUCTURAL_PREDICATES
        return tuple(sorted(set(predicates)))

    def edges_for(self, predicates: Iterable[str] | None = None) -> list[CodeEdge]:
        """Edges whose predicate is in the selection, in a stable order."""
        wanted = set(self._key(predicates))
        return [e for e in self.edges if e.predicate in wanted]

    def out_adjacency(self, predicates: Iterable[str] | None = None) -> dict[str, list[str]]:
        """subject -> sorted unique objects."""
        key = self._key(predicates)
        cached = self._out_cache.get(key)
        if cached is None:
            acc: dict[str, set[str]] = defaultdict(set)
            for e in self.edges_for(key):
                acc[e.subject_id].add(e.object_id)
            cached = {n: sorted(acc.get(n, ())) for n in self._node_ids}
            self._out_cache[key] = cached
        return cached

    def in_adjacency(self, predicates: Iterable[str] | None = None) -> dict[str, list[str]]:
        """object -> sorted unique subjects."""
        key = self._key(predicates)
        cached = self._in_cache.get(key)
        if cached is None:
            acc: dict[str, set[str]] = defaultdict(set)
            for e in self.edges_for(key):
                acc[e.object_id].add(e.subject_id)
            cached = {n: sorted(acc.get(n, ())) for n in self._node_ids}
            self._in_cache[key] = cached
        return cached

    def undirected_adjacency(self, predicates: Iterable[str] | None = None) -> dict[str, set[str]]:
        """Direction-free neighbours, self-loops dropped."""
        key = self._key(predicates)
        cached = self._undirected_cache.get(key)
        if cached is None:
            acc: dict[str, set[str]] = {n: set() for n in self._node_ids}
            for e in self.edges_for(key):
                if e.subject_id == e.object_id:
                    continue
                if e.subject_id in acc and e.object_id in acc:
                    acc[e.subject_id].add(e.object_id)
                    acc[e.object_id].add(e.subject_id)
            cached = acc
            self._undirected_cache[key] = cached
        return cached

    def weighted_undirected_edges(
        self, predicates: Iterable[str] | None = None
    ) -> list[tuple[str, str, float]]:
        """Undirected (u, v, weight) triples with parallel edges summed.

        This is the form the community detector consumes. Each unordered pair
        appears once, keyed on the sorted pair so the output is deterministic.
        """
        acc: dict[tuple[str, str], float] = defaultdict(float)
        for e in self.edges_for(predicates):
            if e.subject_id == e.object_id:
                continue
            pair = (e.subject_id, e.object_id) if e.subject_id < e.object_id else (e.object_id, e.subject_id)
            acc[pair] += e.weight
        return [(u, v, w) for (u, v), w in sorted(acc.items())]

    # -- degrees ------------------------------------------------------------

    def out_degree(self, entity_id: str, predicates: Iterable[str] | None = None) -> int:
        return len(self.out_adjacency(predicates).get(entity_id, ()))

    def in_degree(self, entity_id: str, predicates: Iterable[str] | None = None) -> int:
        return len(self.in_adjacency(predicates).get(entity_id, ()))

    def degree(self, entity_id: str, predicates: Iterable[str] | None = None) -> int:
        """Undirected degree: distinct neighbours in either direction."""
        return len(self.undirected_adjacency(predicates).get(entity_id, ()))

    # -- community partition ------------------------------------------------

    def communities(
        self, predicates: Iterable[str] | None = None, *, resolution: float = 1.0
    ) -> dict[str, int]:
        """Node id to community index, via the built-in modularity optimizer.

        Isolated nodes are absent from the detector's edge list, so they are
        assigned their own singleton community here rather than being silently
        dropped. The result is advisory and structural, like every community
        assignment in this project.
        """
        from ..graph.community import detect_communities

        edges = self.weighted_undirected_edges(predicates)
        result = detect_communities(list(self._node_ids), edges, resolution=resolution)
        assignment: dict[str, int] = dict(result["assignment"])
        next_id = (max(assignment.values()) + 1) if assignment else 0
        for node in self._node_ids:
            if node not in assignment:
                assignment[node] = next_id
                next_id += 1
        return assignment


def load_code_graph(
    db: Database,
    *,
    tenant_id: str = "local",
    max_nodes: int = DEFAULT_MAX_NODES,
) -> CodeGraphView:
    """Read the code plane out of the shared store into an analysis view.

    Only ``code:`` entities and ``code:`` relationships are read; the document
    and media planes are untouched. When the node cap is reached the view is
    marked truncated so a caller can report the bound honestly rather than
    presenting a partial graph as complete.
    """
    max_nodes = max(1, min(int(max_nodes), 200000))
    rows = db.fetchall(
        "SELECT entity_id, canonical, display, kind, metadata_json FROM entities "
        "WHERE tenant_id=? AND kind LIKE 'code:%' ORDER BY canonical LIMIT ?;",
        (tenant_id, max_nodes + 1),
    )
    truncated = len(rows) > max_nodes
    nodes: dict[str, CodeNode] = {}
    for row in rows[:max_nodes]:
        meta = _metadata(row["metadata_json"])
        nodes[row["entity_id"]] = CodeNode(
            entity_id=row["entity_id"],
            canonical=row["canonical"],
            display=row["display"],
            kind=row["kind"],
            path=str(meta.get("path", "")),
            language=str(meta.get("language", "")),
            start_line=_as_int(meta.get("start_line")),
            end_line=_as_int(meta.get("end_line")),
        )

    edges: list[CodeEdge] = []
    edges_truncated = False
    if nodes:
        # The node cap has to bound the edge read as well. Selecting every
        # code relationship and filtering in Python would read and sort the
        # whole table even when the caller asked for one node, so the endpoint
        # filter is pushed into SQL and the result is capped too. The
        # placeholder list is sized to the node count; every value is bound.
        node_ids = list(nodes)
        edge_cap = max_nodes * MAX_EDGES_PER_NODE
        placeholders = ",".join("?" * len(node_ids))
        erows = db.fetchall(
            "SELECT subject_id, predicate, object_id, weight FROM relationships "
            "WHERE tenant_id=? AND predicate LIKE 'code:%' "
            f"AND subject_id IN ({placeholders}) AND object_id IN ({placeholders}) "
            "ORDER BY subject_id, predicate, object_id LIMIT ?;",
            (tenant_id, *node_ids, *node_ids, edge_cap + 1),
        )
        if len(erows) > edge_cap:
            edges_truncated = True
            erows = erows[:edge_cap]
        for row in erows:
            weight = row["weight"]
            edges.append(
                CodeEdge(
                    subject_id=row["subject_id"],
                    predicate=row["predicate"],
                    object_id=row["object_id"],
                    weight=float(weight) if weight is not None else 1.0,
                )
            )
    # One truncated flag covers both bounds, so a caller is never told the view
    # is complete when either the node read or the edge read was clipped.
    return CodeGraphView(nodes, edges, truncated=truncated or edges_truncated)


def _as_int(raw: object) -> int:
    """A metadata line number as an int, defaulting to 0 when it is not one."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _metadata(raw: object) -> dict:
    if not raw:
        return {}
    import json

    try:
        obj = json.loads(str(raw))
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}
