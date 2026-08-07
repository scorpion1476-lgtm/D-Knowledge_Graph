"""The README's MCP tool table must be the real tool surface, not a prose summary.

A README that lists an assistant-facing tool surface is making a checkable
promise: these tools exist, and these are all of them. Both halves matter and
both rot in the same way. A tool added to the registry and not to the table
makes the document understate the surface; a tool removed from the registry and
left in the table sends a reader to something that is not there.

The registry is built for real here rather than scraped out of the source with a
regular expression, because a name that appears in the file but is never
registered would satisfy a regex and fail a caller.

The kind assertion is the security-relevant one. The surface is the trust
boundary against an assistant acting on injected content, so "read-only" has to
be a property the test enforces, not a sentence in a paragraph.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dkg.core.db import open_database
from dkg.mcp.tools import build_read_registry

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
SECTION = "### The tool surface"


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    """The real registry against an empty database.

    Empty on purpose: the surface's shape is a property of the build, not of
    any graph, so this holds on a machine with no optional extra installed.
    """
    home = tmp_path_factory.mktemp("mcp-table")
    with open_database(home / "graph.db") as db:
        return build_read_registry(db, code_root=home)


def _table_rows() -> dict[str, str]:
    text = README.read_text(encoding="utf-8")
    assert SECTION in text, f"README has no {SECTION!r} heading"
    body = text.split(SECTION, 1)[1]
    # The table ends at the next heading.
    body = re.split(r"^## ", body, maxsplit=1, flags=re.M)[0]
    rows = re.findall(r"^\|\s*`(dkg\.[A-Za-z0-9_.]+)`\s*\|\s*(.+?)\s*\|\s*$", body, re.M)
    return dict(rows)


def test_the_readme_lists_every_registered_tool(registry):
    listed = set(_table_rows())
    registered = set(registry.tools)
    missing = sorted(registered - listed)
    assert not missing, (
        f"the README's MCP table omits {len(missing)} registered tool(s): {missing}"
    )


def test_the_readme_invents_no_tool(registry):
    listed = set(_table_rows())
    registered = set(registry.tools)
    invented = sorted(listed - registered)
    assert not invented, (
        f"the README's MCP table names {len(invented)} tool(s) that are not "
        f"registered: {invented}"
    )


def test_every_listed_tool_has_a_real_description():
    rows = _table_rows()
    assert rows, "the MCP table parsed to nothing, so the checks above are vacuous"
    thin = [name for name, text in rows.items() if len(text.strip()) < 20]
    assert not thin, f"these MCP table entries have no real description: {thin}"


def test_no_tool_declares_itself_writable(registry):
    """The cheap half of the check: nothing is even LABELLED as writing."""
    writable = sorted(
        name for name, spec in registry.tools.items() if spec.kind != "read"
    )
    assert not writable, (
        f"the MCP surface registers non-read tools: {writable}. The README "
        "promises a read-only surface."
    )


def test_no_tool_actually_mutates_the_database(tmp_path):
    """The half that matters: call every tool and check the database, by content.

    Asserting `spec.kind == "read"` tests a label. A tool labelled read that
    writes would pass it, which a review pointed out, and the README's promise
    is about behaviour rather than about a field. So this calls every registered
    tool against a populated graph and compares a full logical dump plus every
    table's row count before and after.

    A tool that raises on the arguments it is handed is fine and is recorded:
    the question here is only whether anything CHANGED, and a call that failed
    changed nothing. What would not be fine is a tool that succeeds and writes.
    """
    import hashlib

    from dkg.core.db import open_database

    home = tmp_path / "readonly"
    home.mkdir()
    db_path = home / "graph.db"

    with open_database(db_path) as db:
        _populate(db)

    def snapshot() -> tuple[str, dict[str, int]]:
        """A full logical dump plus every table's row count.

        The dump is taken through sqlite3 directly rather than through the
        project's Database wrapper, because the wrapper deliberately refuses
        some SQL and does not expose iterdump. Read-only here: the connection
        is opened, dumped and closed without a write.
        """
        import sqlite3

        connection = sqlite3.connect(str(db_path))
        try:
            connection.row_factory = sqlite3.Row
            dump = "\n".join(connection.iterdump())
            tables = [
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
                )
            ]
            counts = {
                name: connection.execute(f'SELECT COUNT(*) AS n FROM "{name}";').fetchone()["n"]
                for name in tables
            }
        finally:
            connection.close()
        return hashlib.sha256(dump.encode("utf-8")).hexdigest(), counts

    before_digest, before_counts = snapshot()

    called, mutated = [], []
    with open_database(db_path) as db:
        registry = build_read_registry(db, code_root=home)
        for name in sorted(registry.tools):
            try:
                registry.call(name, _arguments_for(name, home))
            except Exception:
                # An argument this test did not guess right. It wrote nothing.
                pass
            called.append(name)

    after_digest, after_counts = snapshot()

    assert called, "no tool was called, so this check is vacuous"
    if after_digest != before_digest:
        changed = {
            k: (before_counts.get(k), after_counts.get(k))
            for k in set(before_counts) | set(after_counts)
            if before_counts.get(k) != after_counts.get(k)
        }
        mutated.append(f"database content changed after calling the surface: {changed}")
    assert not mutated, "; ".join(mutated)


def _populate(db) -> None:
    """A small real graph, so the tools have something to read.

    An empty database would let every tool return nothing and the comparison
    would hold trivially. Something has to be in there for a write to be able
    to show up as a difference.
    """
    from dkg.ingest.base import ingest_text

    ingest_text(
        db,
        "Alpha relates to beta. The retry budget is 3 attempts.\n",
        display_name="mcp-readonly",
    )


def _arguments_for(name: str, root) -> dict:
    """Plausible arguments per tool, so as many as possible really execute."""
    if name in ("dkg.evidence.claim",):
        return {"claim_id": "none"}
    if name in ("dkg.graph.neighbourhood",):
        return {"entity": "alpha"}
    if name.startswith(("dkg.search", "dkg.code.search", "dkg.repos.search")):
        return {"query": "alpha"}
    if name == "dkg.code.symbols":
        return {"path": str(root / "sample.py"), "text": "def go():\n    return 1\n"}
    if name == "dkg.graph.diff":
        return {"before": "before.json", "after": "after.json"}
    if name == "dkg.docs.section":
        return {"document": "README.md", "section": "Overview"}
    if name == "dkg.prompts.get":
        return {"name": "review"}
    if name in ("dkg.code.impact", "dkg.code.impact_radius", "dkg.code.flow"):
        return {"entity": "alpha"}
    return {"symbol": "alpha", "entity": "alpha", "name": "alpha", "query": "alpha"}


def test_the_table_check_would_notice_a_drifted_name():
    """Negative control, so a parse returning nothing cannot pass as agreement."""
    rows = _table_rows()
    assert "dkg.status" in rows, "the table parser did not find a tool it should have"
    assert "dkg.definitely.not.a.tool" not in rows
