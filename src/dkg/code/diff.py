"""Point-in-time snapshots of the code graph, and structural diffs between them.

A snapshot is a plain JSON object describing the code plane as it stood at one
moment: its nodes, its reference edges, and its community partition. A diff of
two snapshots answers the question a reviewer actually asks after a change
landed, which is what appeared, what disappeared, which references got weaker or
stronger, and which parts of the codebase regrouped.

Two identity decisions shape everything here.

First, nodes and edges are keyed by canonical name, not by entity id. Entity ids
are content hashes derived from the tenant and the kind, so they are stable
inside one store but meaningless to a human and not comparable across machines.
Canonical names (``path`` for a module, ``path::symbol`` for a symbol) are what a
reviewer reads in a diff, and they line up between two stores built from the same
repository, so a snapshot taken on one machine can be diffed against a snapshot
taken on another.

Second, the snapshot body carries no wall-clock timestamp. A timestamp inside the
body would make two snapshots of an unchanged graph differ, which would destroy
the one property the whole feature rests on. The moment of capture is recorded in
a separate top-level ``taken_at`` field, and that field is excluded from every
comparison performed here; ``VOLATILE_KEYS`` names it so a caller can strip it
before comparing snapshots itself. Everything else is a deterministic function of
the database: every list is sorted by an explicit key, every mapping is built in
sorted key order, and weights are rounded, so the same store always serialises
byte-identically apart from ``taken_at``.

Nothing in this module writes to the database, and what it reports is STRUCTURAL
and over-approximate, exactly like the blast-radius and execution-flow features
it sits beside: reference resolution is name-based, so a diff can over-report.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.db import Database
from ..core.errors import ValidationError
from .analysis import DEFAULT_MAX_NODES, STRUCTURAL_PREDICATES, load_code_graph

SNAPSHOT_KIND = "dkg.code-graph-snapshot"
SNAPSHOT_VERSION = 1

# A snapshot of a very large graph is still small; anything past this is either
# not a snapshot or an attempt to make a reader allocate. Bounded so a caller
# that can name a path cannot exhaust memory.
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024

DIFF_KIND = "dkg.code-graph-diff"
DIFF_VERSION = 1

# Fields that are recorded for a human but must never enter a comparison,
# because they change even when the graph does not.
VOLATILE_KEYS = ("taken_at",)

# Every key a snapshot body must carry. ``label`` and ``taken_at`` are omitted on
# purpose: they are optional and carry no graph content.
REQUIRED_SNAPSHOT_KEYS = (
    "kind",
    "version",
    "tenant_id",
    "predicates",
    "resolution",
    "counts",
    "nodes",
    "edges",
    "communities",
    "truncated",
)

_NODE_FIELDS = ("canonical", "kind", "path", "language")
_EDGE_FIELDS = ("from", "to", "predicate")

# Edge confidences are stored as floats. Comparing raw floats would let a value
# that differs only in the last representable bit surface as a weight change, and
# would stop two runs serialising byte-identically, so every weight is rounded to
# a fixed precision on the way in and compared at that precision.
_WEIGHT_PRECISION = 6

_STRUCTURAL_NOTE = "structural and over-approximate; reference resolution is name-based, so a diff can over-report"
_COMMUNITY_NOTE = (
    "community indices are arbitrary labels assigned independently by each run, so membership is compared by "
    "co-membership over the nodes present in both snapshots, never by index equality"
)
_TIMESTAMP_NOTE = "taken_at is excluded from every comparison"


# -- snapshot ---------------------------------------------------------------


def snapshot_code_graph(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
    label: str | None = None,
) -> dict:
    """Capture the code graph as a JSON-serialisable snapshot.

    The read is bounded by ``max_nodes`` and the resulting ``truncated`` flag is
    carried in the snapshot, because a diff of two partial graphs is not a diff
    of two repositories and a reader has to be told so.

    The predicate selection and the resolution used are recorded in the body.
    They are not decoration: two snapshots built from different selections
    describe different graphs, and :func:`diff_snapshots` flags the mismatch
    rather than quietly presenting the difference as a code change.

    Community indices are renumbered here by the alphabetically first canonical
    name in each community. The detector's own numbering falls out of entity id
    ordering, which is a content hash and therefore arbitrary, so renumbering is
    what makes a snapshot of one store serialise the same way every time. The
    numbers stay arbitrary labels regardless, and the diff never compares them.

    ``taken_at`` sits at the top level, outside the body, so that two snapshots
    of an unchanged graph are equal once that field is removed.
    """
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValidationError("tenant_id must be a non-empty string")
    if not _is_number(resolution):
        raise ValidationError("resolution must be a number")
    resolution = float(resolution)
    if resolution <= 0:
        raise ValidationError("resolution must be greater than zero")
    if label is not None and not isinstance(label, str):
        raise ValidationError("label must be a string when given")

    preds = _normalise_predicates(predicates)
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)

    node_records = _snapshot_nodes(view)
    edge_records = _snapshot_edges(view, preds)
    communities = _snapshot_communities(view, preds, resolution)
    community_count = len(set(communities.values()))

    snapshot: dict[str, Any] = {
        "kind": SNAPSHOT_KIND,
        "version": SNAPSHOT_VERSION,
        "tenant_id": tenant_id,
    }
    if label is not None:
        snapshot["label"] = label
    snapshot.update(
        {
            "predicates": list(preds),
            "resolution": resolution,
            "counts": {
                "nodes": len(node_records),
                "edges": len(edge_records),
                "communities": community_count,
            },
            "nodes": node_records,
            "edges": edge_records,
            "communities": communities,
            "truncated": bool(view.truncated),
            "taken_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return snapshot


def _snapshot_nodes(view: Any) -> list[dict]:
    """Node records deduplicated by their whole content and sorted by name.

    Two entities can in principle share a canonical name with different kinds.
    Keeping both records rather than picking a winner keeps the snapshot a
    faithful description of the store; the duplicate is only collapsed when the
    records are identical in every field, which carries no information.
    """
    seen: set[tuple[str, ...]] = set()
    records: list[dict] = []
    for entity_id in view.node_ids():
        node = view.get(entity_id)
        if node is None:  # pragma: no cover - node_ids() is drawn from nodes
            continue
        key = (node.canonical, node.kind, node.path, node.language)
        if key in seen:
            continue
        seen.add(key)
        records.append(dict(zip(_NODE_FIELDS, key, strict=True)))
    records.sort(key=lambda rec: tuple(rec[f] for f in _NODE_FIELDS))
    return records


def _snapshot_edges(view: Any, predicates: tuple[str, ...]) -> list[dict]:
    """Edge records keyed by canonical endpoints, strongest weight kept.

    Collapsing entity ids to names can in principle map two stored edges onto one
    (from, to, predicate) triple. Keeping the strongest confidence is the honest
    reading of what the graph asserts about that relation, and it is stable,
    whereas taking whichever edge happened to be read last would let a diff
    report a weight change that nothing in the code caused.
    """
    strongest: dict[tuple[str, str, str], float] = {}
    for edge in view.edges_for(predicates):
        key = (view.label(edge.subject_id), view.label(edge.object_id), edge.predicate)
        weight = _round_weight(edge.weight)
        if key not in strongest or weight > strongest[key]:
            strongest[key] = weight
    return [_edge_record(key, weight) for key, weight in sorted(strongest.items())]


def _snapshot_communities(view: Any, predicates: tuple[str, ...], resolution: float) -> dict[str, int]:
    """Canonical name to community index, renumbered into a stable order."""
    raw = view.communities(predicates, resolution=resolution)
    # On a canonical collision the lowest entity id wins, so the choice does not
    # depend on iteration order.
    by_canonical: dict[str, int] = {}
    for entity_id in view.node_ids():
        by_canonical.setdefault(view.label(entity_id), raw[entity_id])

    members: dict[int, list[str]] = defaultdict(list)
    for canonical, index in by_canonical.items():
        members[index].append(canonical)
    renumbered: dict[str, int] = {}
    # Each canonical belongs to exactly one community, so the alphabetically
    # first member is a unique and stable key for ordering the communities.
    for new_index, (_old_index, group) in enumerate(sorted(members.items(), key=lambda kv: min(kv[1]))):
        for canonical in group:
            renumbered[canonical] = new_index
    return {canonical: renumbered[canonical] for canonical in sorted(renumbered)}


# -- diff -------------------------------------------------------------------


def diff_snapshots(before: dict, after: dict) -> dict:
    """Report what changed between two code-graph snapshots.

    Nodes and edges are compared by value. A node record is compared whole, so a
    symbol whose kind or language changed shows up as a removal and an addition
    rather than in an invented "changed node" category that would have to guess
    which field mattered. Edges are matched on (from, to, predicate) and a weight
    difference on a surviving edge is reported separately, because a confidence
    that moved is a different event from a reference that appeared.

    Community membership is the subtle part. Community indices are arbitrary
    labels produced independently by each run: nothing ties index 3 in one
    snapshot to index 3 in another, so comparing index equality would report
    noise and miss real regroupings. What is comparable is CO-MEMBERSHIP, the set
    of other nodes a node shares a community with. A node is reported here only
    when that set changed, listing what it gained and what it lost.

    Co-membership is computed over the nodes present in BOTH snapshots. Nodes
    that were added or removed entirely are excluded twice over: they are not
    reported as community changes themselves, and they are not counted as gains
    or losses for the nodes that survived. Without the second exclusion a single
    new function joining an existing cluster would mark every member of that
    cluster as changed, and the real regroupings would be swamped.

    ``taken_at`` is excluded from every comparison, so two snapshots of an
    unchanged graph diff to nothing even though they were taken at different
    times.
    """
    before = _validate_snapshot(before, "before")
    after = _validate_snapshot(after, "after")

    before_nodes = _node_index(before)
    after_nodes = _node_index(after)
    added_nodes = [after_nodes[k] for k in sorted(set(after_nodes) - set(before_nodes))]
    removed_nodes = [before_nodes[k] for k in sorted(set(before_nodes) - set(after_nodes))]

    before_edges = _edge_index(before)
    after_edges = _edge_index(after)
    added_edges = [_edge_record(k, after_edges[k]) for k in sorted(set(after_edges) - set(before_edges))]
    removed_edges = [_edge_record(k, before_edges[k]) for k in sorted(set(before_edges) - set(after_edges))]
    changed_edges = [
        {
            "from": key[0],
            "to": key[1],
            "predicate": key[2],
            "before_weight": before_edges[key],
            "after_weight": after_edges[key],
        }
        for key in sorted(set(before_edges) & set(after_edges))
        if before_edges[key] != after_edges[key]
    ]

    community_changes = _community_changes(before, after)

    parameters_match = list(before["predicates"]) == list(after["predicates"]) and float(before["resolution"]) == float(after["resolution"])
    truncated = bool(before["truncated"]) or bool(after["truncated"])
    notes = [_STRUCTURAL_NOTE, _COMMUNITY_NOTE, _TIMESTAMP_NOTE]
    if not parameters_match:
        notes.append(
            "the two snapshots were built with different predicate selections or resolutions, so edge and community "
            "differences below are not all code changes"
        )
    if truncated:
        notes.append("at least one snapshot hit its node cap, so it describes a partial graph and the diff is partial too")

    counts = {
        "added_nodes": len(added_nodes),
        "removed_nodes": len(removed_nodes),
        "added_edges": len(added_edges),
        "removed_edges": len(removed_edges),
        "changed_edges": len(changed_edges),
        "community_changes": len(community_changes),
    }
    # 'changed' is derived from the counts rather than tracked separately, so it
    # can never disagree with the categories it summarises.
    summary = {**counts, "changed": any(counts.values())}

    return {
        "kind": DIFF_KIND,
        "version": DIFF_VERSION,
        "before": _snapshot_context(before),
        "after": _snapshot_context(after),
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
        "changed_edges": changed_edges,
        "community_changes": community_changes,
        "summary": summary,
        "why": {
            "analysis": "structural",
            "parameters_match": parameters_match,
            "truncated": truncated,
            "notes": notes,
        },
    }


def _community_changes(before: dict, after: dict) -> list[dict]:
    """Nodes whose set of community peers changed, with the peers gained and lost."""
    before_communities = before["communities"]
    after_communities = after["communities"]
    common = (
        {rec["canonical"] for rec in before["nodes"]}
        & {rec["canonical"] for rec in after["nodes"]}
        & set(before_communities)
        & set(after_communities)
    )
    before_peers = _co_membership(before_communities, common)
    after_peers = _co_membership(after_communities, common)

    changes: list[dict] = []
    for canonical in sorted(common):
        gained = sorted(after_peers[canonical] - before_peers[canonical])
        lost = sorted(before_peers[canonical] - after_peers[canonical])
        if gained or lost:
            changes.append({"canonical": canonical, "gained": gained, "lost": lost})
    return changes


def _co_membership(communities: dict, common: set[str]) -> dict[str, set[str]]:
    """canonical -> the other common nodes sharing its community."""
    groups: dict[int, set[str]] = defaultdict(set)
    for canonical, index in communities.items():
        if canonical in common:
            groups[int(index)].add(canonical)
    return {canonical: groups[int(communities[canonical])] - {canonical} for canonical in common}


def _snapshot_context(snapshot: dict) -> dict:
    """The identifying facts of one side of a diff, without its full content."""
    context = {
        "tenant_id": snapshot["tenant_id"],
        "counts": dict(snapshot["counts"]),
        "truncated": bool(snapshot["truncated"]),
        "predicates": list(snapshot["predicates"]),
        "resolution": float(snapshot["resolution"]),
    }
    if "label" in snapshot:
        context["label"] = snapshot["label"]
    return context


# -- loading and validation -------------------------------------------------


def load_snapshot(path: str | Path, *, root: str | Path | None = None, max_bytes: int = MAX_SNAPSHOT_BYTES) -> dict:
    """Read a snapshot from disk, validating it before it reaches a caller.

    A file on disk is untrusted input: it may be hand-edited, truncated, or from
    a future version of this format. It is rejected loudly rather than coerced,
    so a diff is never computed over something that only resembles a snapshot.

    Two bounds matter when the caller is not a human at a terminal. ``root``
    confines the read to a directory, so a caller that can name a path cannot
    turn this into a general filesystem read; symlinks are resolved before the
    check so a link out of the root does not slip past it. ``max_bytes`` caps
    the read, so naming a huge file cannot exhaust memory. The CLI leaves
    ``root`` unset because the user is choosing the path themselves; the MCP
    surface always sets it.
    """
    p = Path(path)
    if root is not None:
        base = Path(root).resolve()
        try:
            resolved = p.resolve()
        except OSError as e:
            raise ValidationError(f"snapshot path could not be resolved: {e}") from e
        if not resolved.is_relative_to(base):
            # Deliberately does not echo the resolved path, which would confirm
            # what exists outside the root.
            raise ValidationError(f"snapshot path is outside the permitted directory {base}")
        p = resolved
    if not p.is_file():
        raise ValidationError(f"snapshot file not found: {p}")
    size = p.stat().st_size
    if size > max_bytes:
        raise ValidationError(f"snapshot file {p} is {size} bytes, over the {max_bytes} byte limit")
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ValidationError(f"snapshot file {p} could not be read: {e}") from e
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValidationError(f"snapshot file {p} is not valid JSON: {e}") from e
    return _validate_snapshot(obj, str(p))


def _validate_snapshot(obj: Any, where: str) -> dict:
    """Reject anything that is not a well-formed snapshot, naming the problem."""
    if not isinstance(obj, dict):
        raise ValidationError(f"{where}: expected a snapshot object, got {type(obj).__name__}")
    kind = obj.get("kind")
    if kind != SNAPSHOT_KIND:
        raise ValidationError(f"{where}: expected kind {SNAPSHOT_KIND!r}, got {kind!r}")
    version = obj.get("version")
    if not _is_int(version) or version != SNAPSHOT_VERSION:
        raise ValidationError(f"{where}: expected version {SNAPSHOT_VERSION}, got {version!r}")
    for key in REQUIRED_SNAPSHOT_KEYS:
        if key not in obj:
            raise ValidationError(f"{where}: missing required key {key!r}")

    if not isinstance(obj["tenant_id"], str) or not obj["tenant_id"]:
        raise ValidationError(f"{where}: 'tenant_id' must be a non-empty string")
    if not isinstance(obj["truncated"], bool):
        raise ValidationError(f"{where}: 'truncated' must be a boolean")
    if not _is_number(obj["resolution"]):
        raise ValidationError(f"{where}: 'resolution' must be a number")
    if not isinstance(obj["predicates"], list) or not all(isinstance(p, str) for p in obj["predicates"]):
        raise ValidationError(f"{where}: 'predicates' must be a list of strings")
    if not isinstance(obj["counts"], dict):
        raise ValidationError(f"{where}: 'counts' must be an object")
    for count_key in ("nodes", "edges", "communities"):
        if not _is_int(obj["counts"].get(count_key)):
            raise ValidationError(f"{where}: 'counts.{count_key}' must be an integer")
    if "label" in obj and not isinstance(obj["label"], str):
        raise ValidationError(f"{where}: 'label' must be a string when present")
    for volatile in VOLATILE_KEYS:
        if volatile in obj and not isinstance(obj[volatile], str):
            raise ValidationError(f"{where}: {volatile!r} must be a string when present")

    if not isinstance(obj["nodes"], list):
        raise ValidationError(f"{where}: 'nodes' must be a list")
    for i, rec in enumerate(obj["nodes"]):
        if not isinstance(rec, dict):
            raise ValidationError(f"{where}: nodes[{i}] must be an object")
        for field in _NODE_FIELDS:
            if field not in rec:
                raise ValidationError(f"{where}: nodes[{i}] missing required key {field!r}")
            if not isinstance(rec[field], str):
                raise ValidationError(f"{where}: nodes[{i}].{field} must be a string")
        if not rec["canonical"]:
            raise ValidationError(f"{where}: nodes[{i}].canonical must not be empty")

    if not isinstance(obj["edges"], list):
        raise ValidationError(f"{where}: 'edges' must be a list")
    for i, rec in enumerate(obj["edges"]):
        if not isinstance(rec, dict):
            raise ValidationError(f"{where}: edges[{i}] must be an object")
        for field in _EDGE_FIELDS:
            if field not in rec:
                raise ValidationError(f"{where}: edges[{i}] missing required key {field!r}")
            if not isinstance(rec[field], str) or not rec[field]:
                raise ValidationError(f"{where}: edges[{i}].{field} must be a non-empty string")
        if "weight" not in rec:
            raise ValidationError(f"{where}: edges[{i}] missing required key 'weight'")
        if not _is_number(rec["weight"]):
            raise ValidationError(f"{where}: edges[{i}].weight must be a number")

    if not isinstance(obj["communities"], dict):
        raise ValidationError(f"{where}: 'communities' must be an object")
    for canonical, index in obj["communities"].items():
        if not isinstance(canonical, str) or not canonical:
            raise ValidationError(f"{where}: 'communities' keys must be non-empty strings")
        if not _is_int(index):
            raise ValidationError(f"{where}: communities[{canonical!r}] must be an integer")
    return obj


# -- small shared helpers ---------------------------------------------------


def _normalise_predicates(predicates: Iterable[str] | None) -> tuple[str, ...]:
    """Sorted unique predicate selection, matching how the shared view keys it."""
    if predicates is None:
        return STRUCTURAL_PREDICATES
    selected = tuple(sorted({str(p) for p in predicates}))
    if not selected:
        raise ValidationError("predicates must name at least one predicate when given")
    return selected


def _round_weight(value: Any) -> float:
    return round(float(value), _WEIGHT_PRECISION)


def _edge_record(key: tuple[str, str, str], weight: float) -> dict:
    return {"from": key[0], "to": key[1], "predicate": key[2], "weight": weight}


def _node_index(snapshot: dict) -> dict[tuple[str, ...], dict]:
    """Node records by their whole content, so comparison is by value."""
    index: dict[tuple[str, ...], dict] = {}
    for rec in snapshot["nodes"]:
        key = tuple(rec[f] for f in _NODE_FIELDS)
        index.setdefault(key, dict(zip(_NODE_FIELDS, key, strict=True)))
    return index


def _edge_index(snapshot: dict) -> dict[tuple[str, str, str], float]:
    """Edge weights by (from, to, predicate), strongest kept, rounded once."""
    index: dict[tuple[str, str, str], float] = {}
    for rec in snapshot["edges"]:
        key = (rec["from"], rec["to"], rec["predicate"])
        weight = _round_weight(rec["weight"])
        if key not in index or weight > index[key]:
            index[key] = weight
    return index


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
