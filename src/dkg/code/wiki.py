"""A browsable markdown knowledge base generated from the community structure.

The Obsidian export writes one note per entity with wikilinks. That is a graph
dump: useful for a tool, close to useless for a person, because it has as many
pages as the graph has nodes and none of them says what a region of the codebase
IS. This writes one page per community instead, with its members, the entry
points execution reaches it through, and the edges that leave it. That is the
unit a reader can hold.

Regeneration is INCREMENTAL. A page is rewritten only when its rendered content
differs from what was written last time, tracked by a manifest of content
digests, and a page whose community no longer exists is removed. On an unchanged
graph a regeneration writes nothing at all and says so, which is what makes it
safe to run from a hook.

The advisory labelling travels with the pages. Everything here derives from one
run of a modularity optimizer over a name-based, over-approximate graph, so
every page carries that caveat and community indices are marked as the
per-run labels they are. A knowledge base that dropped the caveat would be read
as documentation of a design nobody wrote.

Writes only inside the output directory. No network.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..core.db import Database
from .analysis import DEFAULT_MAX_NODES, STRUCTURAL_PREDICATES, load_code_graph
from .deadcode import ENTRY_POINT_KINDS, ENTRY_POINT_NAMES

MANIFEST_NAME = ".dkg-wiki-manifest.json"
INDEX_NAME = "index.md"
PAGE_PREFIX = "community-"

# Bounds, so one enormous community cannot produce an unreadable page.
MAX_MEMBERS_LISTED = 300
MAX_EDGES_LISTED = 200

# The caveat carried by every page.
ADVISORY = (
    "**Advisory.** This page is generated from one run of a modularity optimizer "
    "over a structural, name-based code graph. A community is a cluster of edges, "
    "not a module boundary anyone designed, and the edges themselves "
    "over-approximate: a name-matched call may not happen and a dynamic one is "
    "not here at all. Community numbers are arbitrary labels from a single run "
    "and must never be compared against another run."
)

_UNSAFE = re.compile(r"[^A-Za-z0-9._/:@ -]")


def _escape(text: str) -> str:
    """Make a symbol name safe to place in a markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _slug(index: int) -> str:
    return f"{PAGE_PREFIX}{index}.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_manifest(out_dir: Path) -> dict[str, str]:
    path = out_dir / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # An unreadable manifest means every page is rewritten, which is
        # correct and slow rather than incorrect and fast.
        return {}
    pages = obj.get("pages")
    return {str(k): str(v) for k, v in pages.items()} if isinstance(pages, dict) else {}


def build_wiki(
    db: Database,
    out_dir: str | Path,
    *,
    tenant_id: str = "local",
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
    incremental: bool = True,
) -> dict:
    """Render the knowledge base into ``out_dir``, rewriting only what changed."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    communities = view.communities(STRUCTURAL_PREDICATES, resolution=resolution)
    neighbours = view.undirected_adjacency(STRUCTURAL_PREDICATES)

    members: dict[int, list[str]] = {}
    for node_id, index in communities.items():
        members.setdefault(index, []).append(node_id)

    pages: dict[str, str] = {}
    summaries: list[dict] = []
    for index in sorted(members):
        summary = _summarise(view, communities, neighbours, index, sorted(members[index]))
        summaries.append(summary)
        pages[_slug(index)] = _render_page(summary)
    pages[INDEX_NAME] = _render_index(summaries, view)

    previous = _load_manifest(out) if incremental else {}
    written: list[str] = []
    unchanged: list[str] = []
    for name, content in sorted(pages.items()):
        digest = _digest(content)
        target = out / name
        if incremental and previous.get(name) == digest and target.exists():
            unchanged.append(name)
            continue
        target.write_text(content, encoding="utf-8")
        written.append(name)

    # A page whose community no longer exists is removed, not left to be read
    # as current.
    removed: list[str] = []
    for name in sorted(previous):
        if name not in pages:
            stale = out / name
            if stale.exists():
                stale.unlink()
            removed.append(name)

    manifest = {
        "generated_at": _now(),
        "pages": {name: _digest(content) for name, content in sorted(pages.items())},
    }
    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    return {
        "out_dir": str(out.resolve()),
        "communities": len(summaries),
        "pages": sorted(pages),
        "written": written,
        "unchanged": unchanged,
        "removed": removed,
        "incremental": incremental,
        "truncated": view.truncated,
        "why": {
            "advisory": ADVISORY,
            "incremental": (
                "a page is rewritten only when its rendered content differs from "
                "the digest recorded last time, so regenerating an unchanged "
                "graph writes nothing"
            ),
            "community_indices": (
                "arbitrary per-run labels; never compare an index across runs"
            ),
        },
    }


def _summarise(view, communities, neighbours, index: int, group: list[str]) -> dict:
    group_set = set(group)
    internal = 0
    crossing: list[tuple[str, str, int]] = []
    for node_id in group:
        for other in sorted(neighbours.get(node_id, set())):
            if other in group_set:
                internal += 1
            else:
                crossing.append(
                    (view.label(node_id), view.label(other), communities.get(other, -1))
                )
    internal //= 2
    size = len(group)
    density = round(internal / (size * (size - 1) / 2), 4) if size >= 2 else 1.0
    entries = sorted(
        view.label(n)
        for n in group
        if view.nodes[n].kind in ENTRY_POINT_KINDS or view.nodes[n].display in ENTRY_POINT_NAMES
    )
    return {
        "index": index,
        "members": sorted((view.label(n), view.nodes[n].kind, view.path_of(n)) for n in group),
        "member_count": size,
        "files": sorted({view.path_of(n) for n in group if view.path_of(n)}),
        "entry_points": entries,
        "internal_edges": internal,
        "crossing": sorted(set(crossing)),
        "density": density,
        "languages": sorted({view.language_of(n) for n in group if view.language_of(n)}),
    }


def _render_page(summary: dict) -> str:
    index = summary["index"]
    lines = [
        f"# Community {index}",
        "",
        ADVISORY,
        "",
        "## At a glance",
        "",
        "| property | value |",
        "| --- | --- |",
        f"| members | {summary['member_count']} |",
        f"| files | {len(summary['files'])} |",
        f"| internal edges | {summary['internal_edges']} |",
        f"| edges leaving | {len(summary['crossing'])} |",
        f"| internal density | {summary['density']} |",
        f"| languages | {', '.join(summary['languages']) or 'none recorded'} |",
        "",
        "## Entry points",
        "",
    ]
    if summary["entry_points"]:
        lines.append(
            "Execution can start here. Everything else in this community is reached."
        )
        lines.append("")
        for name in summary["entry_points"][:MAX_MEMBERS_LISTED]:
            lines.append(f"- `{_escape(name)}`")
    else:
        lines.append(
            "None detected. That does not mean this region is unreachable: an "
            "entry point is only detected when a recognised route, schedule, "
            "test, or conventional name declares one."
        )
    lines += ["", "## Members", "", "| symbol | kind | file |", "| --- | --- | --- |"]
    for name, kind, path in summary["members"][:MAX_MEMBERS_LISTED]:
        lines.append(f"| `{_escape(name)}` | {_escape(kind)} | `{_escape(path)}` |")
    if summary["member_count"] > MAX_MEMBERS_LISTED:
        lines.append("")
        lines.append(
            f"_{summary['member_count'] - MAX_MEMBERS_LISTED} further members are "
            f"not listed; this page is capped at {MAX_MEMBERS_LISTED}._"
        )

    lines += ["", "## Edges leaving this community", ""]
    if summary["crossing"]:
        lines += ["| from | to | community |", "| --- | --- | --- |"]
        for frm, to, other in summary["crossing"][:MAX_EDGES_LISTED]:
            link = f"[{other}]({_slug(other)})" if other >= 0 else "unknown"
            lines.append(f"| `{_escape(frm)}` | `{_escape(to)}` | {link} |")
        if len(summary["crossing"]) > MAX_EDGES_LISTED:
            lines.append("")
            lines.append(
                f"_{len(summary['crossing']) - MAX_EDGES_LISTED} further crossing "
                f"edges are not listed; this page is capped at {MAX_EDGES_LISTED}._"
            )
    else:
        lines.append("None. Nothing in this community references anything outside it.")

    lines += ["", "## Files", ""]
    for path in summary["files"][:MAX_MEMBERS_LISTED]:
        lines.append(f"- `{_escape(path)}`")
    lines += ["", f"[Back to the index]({INDEX_NAME})", ""]
    return "\n".join(lines)


def _render_index(summaries: list[dict], view) -> str:
    lines = [
        "# Code knowledge base",
        "",
        ADVISORY,
        "",
        "## Communities",
        "",
        "Largest first. Each page lists that community's members, the entry "
        "points execution reaches it through, and the edges that leave it.",
        "",
        "| community | members | files | density | entry points |",
        "| --- | --- | --- | --- | --- |",
    ]
    ordered = sorted(summaries, key=lambda s: (-s["member_count"], s["index"]))
    for summary in ordered:
        link = f"[Community {summary['index']}]({_slug(summary['index'])})"
        lines.append(
            f"| {link} | {summary['member_count']} | {len(summary['files'])} | "
            f"{summary['density']} | {len(summary['entry_points'])} |"
        )
    lines += [
        "",
        "## Totals",
        "",
        f"- Nodes: {len(view)}",
        f"- Communities: {len(summaries)}",
        f"- Languages: {', '.join(view.languages()) or 'none recorded'}",
        "",
    ]
    if view.truncated:
        lines += [
            "> The graph view hit its node or edge cap, so this knowledge base "
            "describes part of the graph rather than all of it.",
            "",
        ]
    return "\n".join(lines)
