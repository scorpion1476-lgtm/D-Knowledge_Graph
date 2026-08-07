"""CLI and MCP surfaces for the graph-analysis features.

The analysis modules are unit-tested next to their own code. What is pinned here
is the wiring: that every feature is actually reachable from the command line and
from the read-only MCP registry, and that the MCP surface stays read-only.
"""

from __future__ import annotations

import json

import pytest

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

from dkg.cli.entry import main  # noqa: E402
from dkg.core.db import open_database  # noqa: E402
from dkg.mcp.tools import build_read_registry  # noqa: E402

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source

ANALYSIS_COMMANDS = (
    "code-hubs",
    "code-coupling",
    "code-gaps",
    "code-questions",
    "code-architecture",
)

ANALYSIS_TOOLS = (
    "dkg.code.hubs",
    "dkg.code.coupling",
    "dkg.code.gaps",
    "dkg.code.questions",
    "dkg.code.architecture",
    "dkg.graph.diff",
)

SOURCE = (
    "core.py",
    "def util():\n    return 1\n"
    "def hub():\n    return util()\n"
    "def alpha():\n    return hub()\n"
    "def beta():\n    return hub()\n"
    "def orphan():\n    return 0\n",
    "python",
)


@pytest.fixture
def home(tmp_path):
    """A project home with a small code graph already ingested."""
    h = tmp_path / ".dkg"
    h.mkdir(parents=True, exist_ok=True)
    if _TS:
        with open_database(h / "graph.sqlite") as db:
            path, text, lang = SOURCE
            write_code_graph(db, [parse_source(path, text, language=lang)], {path: text}, source_uri="test://cli")
    return h


# -- CLI --------------------------------------------------------------------


@requires_ts
@pytest.mark.parametrize("cmd", ANALYSIS_COMMANDS)
def test_each_analysis_command_runs_and_prints(home, capsys, cmd):
    assert main(["--home", str(home), cmd]) == 0
    out = capsys.readouterr().out
    assert out.strip(), cmd


@requires_ts
def test_code_hubs_reports_the_hub(home, capsys):
    assert main(["--home", str(home), "code-hubs"]) == 0
    result = json.loads(capsys.readouterr().out)
    names = {h["canonical"] for h in result["hubs"]}
    assert "core.py::hub" in names


@requires_ts
def test_code_questions_names_the_orphan(home, capsys):
    assert main(["--home", str(home), "code-questions", "--limit", "50"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert any("orphan" in q["subject"] for q in result["questions"])


@requires_ts
def test_code_architecture_markdown_and_json(home, capsys, tmp_path):
    assert main(["--home", str(home), "code-architecture"]) == 0
    text = capsys.readouterr().out
    assert "# Architecture overview" in text
    assert "```mermaid" in text

    out = tmp_path / "arch.json"
    assert main(["--home", str(home), "code-architecture", "--format", "json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "components" in payload and "warnings" in payload


@requires_ts
def test_snapshot_then_diff_round_trip(home, capsys, tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    assert main(["--home", str(home), "graph-snapshot", str(before)]) == 0
    capsys.readouterr()

    # Add a symbol, snapshot again, and the diff must see exactly that.
    with open_database(home / "graph.sqlite") as db:
        path, text, lang = SOURCE
        grown = text + "def added():\n    return hub()\n"
        write_code_graph(
            db,
            [parse_source(path, grown, language=lang)],
            {path: grown},
            source_uri="test://cli",
            replace_paths={path},
        )
    assert main(["--home", str(home), "graph-snapshot", str(after)]) == 0
    capsys.readouterr()

    assert main(["--home", str(home), "graph-diff", str(before), str(after)]) == 0
    diff = json.loads(capsys.readouterr().out)
    added = {n["canonical"] for n in diff["added_nodes"]}
    assert "core.py::added" in added
    assert diff["summary"]["changed"] is True


@requires_ts
def test_graph_diff_of_a_snapshot_against_itself_is_empty(home, capsys, tmp_path):
    snap = tmp_path / "s.json"
    assert main(["--home", str(home), "graph-snapshot", str(snap)]) == 0
    capsys.readouterr()
    assert main(["--home", str(home), "graph-diff", str(snap), str(snap)]) == 0
    diff = json.loads(capsys.readouterr().out)
    assert diff["summary"]["changed"] is False


def test_mcp_tools_command_lists_targets(home, capsys):
    assert main(["--home", str(home), "--json", "mcp-tools"]) == 0
    payload = json.loads(capsys.readouterr().out)
    names = {t["name"] for t in payload["tools"]}
    assert "claude-code" in names
    assert len(names) >= 3


def test_mcp_install_dry_run_writes_nothing(home, capsys, tmp_path):
    root = tmp_path / "fixture-config"
    assert (
        main(
            [
                "--home",
                str(home),
                "mcp-install",
                "claude-code",
                "--config-root",
                str(root),
                "--dry-run",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert result["written"] is False
    # A dry run must leave the filesystem exactly as it found it.
    assert not root.exists()


def test_mcp_install_then_uninstall_against_a_fixture_root(home, capsys, tmp_path):
    root = tmp_path / "fixture-config"
    assert main(["--home", str(home), "mcp-install", "claude-code", "--config-root", str(root)]) == 0
    installed = json.loads(capsys.readouterr().out)
    target = tmp_path / "fixture-config" / installed["path"].split("fixture-config/")[-1]
    assert target.is_file()
    config = json.loads(target.read_text(encoding="utf-8"))
    entry = config[installed["servers_key"]][installed["server_name"]]
    assert isinstance(entry["args"], list), "argv form, never a shell string"
    assert "read-only" in entry["description"]

    assert main(["--home", str(home), "mcp-uninstall", "claude-code", "--config-root", str(root)]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["removed"] is True
    # This fixture root held nothing but the entry this project wrote, so a
    # symmetric uninstall takes the file with it rather than leaving an empty
    # husk behind. Asserting the file is gone is the stronger claim: it is what
    # makes the install-then-uninstall round trip byte-identical to the
    # pre-install tree. A tool config that also held a human's own servers keeps
    # the file and loses only our entry; that case is covered in
    # tests/integration/test_uninstall_scopes.py.
    assert not target.exists()
    # Nothing this project wrote survives. The tool's own marker directory does,
    # deliberately: pruning it would make the tool look uninstalled to the next
    # detection run. So the only thing left under the root is that empty marker.
    survivors = sorted(p for p in root.rglob("*") if p.is_file())
    assert survivors == [], survivors


# -- MCP registry -----------------------------------------------------------


def test_every_analysis_tool_is_registered(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        reg = build_read_registry(db)
        names = {spec.name for spec in reg.list_tools()} if hasattr(reg, "list_tools") else set(reg.tools)
    for tool in ANALYSIS_TOOLS:
        assert tool in names, tool


def test_registry_exposes_no_write_tool(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        reg = build_read_registry(db)
        names = {spec.name for spec in reg.list_tools()} if hasattr(reg, "list_tools") else set(reg.tools)
    forbidden = ("write", "delete", "ingest", "update", "insert", "remove", "install")
    for name in names:
        assert not any(word in name.lower() for word in forbidden), name


@requires_ts
def test_analysis_tools_answer_over_a_real_graph(home):
    with open_database(home / "graph.sqlite") as db:
        reg = build_read_registry(db)
        hubs = reg.call("dkg.code.hubs", {"limit": 5})
        assert any(h["canonical"] == "core.py::hub" for h in hubs["hubs"])
        gaps = reg.call("dkg.code.gaps", {"limit": 10})
        assert any("orphan" in n["canonical"] for n in gaps["isolated"])
        questions = reg.call("dkg.code.questions", {"limit": 10})
        assert questions["questions"]
        arch = reg.call("dkg.code.architecture", {"format": "markdown"})
        assert "```mermaid" in arch["markdown"]


def test_graph_diff_tool_rejects_missing_arguments(tmp_path):
    from dkg.core.errors import ValidationError

    with open_database(tmp_path / "g.db") as db:
        reg = build_read_registry(db)
        with pytest.raises(ValidationError):
            reg.call("dkg.graph.diff", {"before": "only-one.json"})


# -- MCP read bounds added after adversarial review -------------------------


def test_graph_diff_tool_cannot_read_outside_the_snapshot_root(tmp_path):
    """The read-only MCP surface must not become a filesystem read primitive."""
    from dkg.core.errors import ValidationError

    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"kind": "secret", "value": "MY-SECRET-VALUE"}', encoding="utf-8")

    with open_database(home / "g.db") as db:
        reg = build_read_registry(db)
        with pytest.raises(ValidationError) as exc:
            reg.call("dkg.graph.diff", {"before": str(outside), "after": str(outside)})
        assert "MY-SECRET-VALUE" not in str(exc.value)


def test_graph_diff_tool_root_defaults_to_the_database_directory(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with open_database(home / "g.db") as db:
        reg = build_read_registry(db)
        # A snapshot written next to the database is readable ...
        snap = home / "s.json"
        from dkg.code.diff import snapshot_code_graph

        snap.write_text(json.dumps(snapshot_code_graph(db)), encoding="utf-8")
        diff = reg.call("dkg.graph.diff", {"before": str(snap), "after": str(snap)})
        assert diff["summary"]["changed"] is False


def test_analysis_view_bounds_the_edge_read_not_just_the_node_read(tmp_path):
    """A node cap that leaves the edge read unbounded is not a bound."""
    from dkg.code.analysis import load_code_graph

    with open_database(tmp_path / "g.db") as db:
        with db.transaction():
            for i in range(40):
                db.execute(
                    "INSERT INTO entities(entity_id,tenant_id,kind,canonical,display,metadata_json) VALUES (?,?,?,?,?,?);",
                    (f"e{i}", "local", "code:function", f"f{i}.py::f{i}", f"f{i}",
                     json.dumps({"path": f"f{i}.py", "language": "python"})),
                )
            n = 0
            for i in range(40):
                for j in range(40):
                    if i != j:
                        db.execute(
                            "INSERT OR IGNORE INTO relationships(relationship_id,tenant_id,subject_id,"
                            "predicate,object_id,support,weight,evidence_json,metadata_json) VALUES (?,?,?,?,?,?,?,?,?);",
                            (f"r{n}", "local", f"e{i}", "code:calls", f"e{j}", "supports", 0.6, "[]", "{}"),
                        )
                        n += 1
        view = load_code_graph(db, max_nodes=2)
        assert len(view) == 2
        assert view.truncated is True
        # No edge may reference a node outside the cap, and the read must not
        # have pulled the whole relationship table into memory.
        for e in view.edges:
            assert e.subject_id in view.nodes and e.object_id in view.nodes
        assert len(view.edges) <= 2 * 200
