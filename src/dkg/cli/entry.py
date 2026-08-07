"""Main CLI entry point.

Usage:
    dkg <command> [<subargs>]

Commands (partial list; see 'dkg help'):
  init             Create a project-local .dkg home and initial database.
  status           Show version, database state, and adapter capabilities.
  doctor           Full self-check, JSON output.
  ingest           Ingest local files or directories.
  ingest-web       Fetch a URL with SSRF guards (requires network opt-in).
  ingest-rss       Fetch and parse an RSS/Atom feed.
  search           Keyword / FTS / hybrid search.
  graph            Query the graph.
  evidence         Fetch evidence packets for a claim.
  export           Export to JSON, Markdown, CSV, or GraphML.
  backup           Create a portable backup.
  restore          Restore from a portable backup.
  audit            List and verify audit log.
  capabilities     List adapter capabilities.
  mcp-stdio        Run the stdio MCP server on this process.
  mcp-http         Run the HTTP MCP server (loopback default).
  agent            Run a deterministic multi-agent workflow.
  help             Show this help.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from dkg import __version__

from ..adapters.capability import default_registry
from ..core.audit import AuditEntry, AuditLog
from ..core.config import load_config
from ..core.db import open_database
from ..core.errors import DKGError
from ..core.version import record_open
from . import extensions
from .output import print_json, print_kv, print_table


def _mk_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dkg", description="D-Knowledge_Graph CLI")
    parser.add_argument("--home", type=str, help="override DKG home directory")
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="machine-readable JSON output"
    )
    parser.add_argument("--version", action="version", version=f"dkg {__version__}")
    parser.add_argument(
        "--token-budget",
        dest="token_budget",
        type=int,
        default=None,
        help=(
            "bound the JSON payload to roughly this many tokens by trimming ranked "
            "lists from the tail; totals still report the true counts"
        ),
    )
    parser.add_argument(
        "--no-savings",
        dest="no_savings",
        action="store_true",
        help="omit the estimated context-savings record from impact, review, change, and architecture results",
    )
    parser.add_argument(
        "--verify-savings",
        dest="verify_savings",
        action="store_true",
        help="cross-check the savings estimate against the real tokenizer and publish the calibration error",
    )

    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("init", help="initialise a project-local .dkg home")

    sub.add_parser("status", help="show status")
    sub.add_parser("doctor", help="run self-check")
    sub.add_parser("capabilities", help="list adapter capabilities")

    p = sub.add_parser("ingest", help="ingest local file or directory")
    p.add_argument("path", help="file or directory")
    p.add_argument("--format", default=None, help="force a specific format")
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("ingest-web", help="ingest one URL (requires --allow-network)")
    p.add_argument("url")
    p.add_argument("--allow-network", action="store_true")

    p = sub.add_parser("ingest-rss", help="ingest an RSS or Atom feed (requires --allow-network)")
    p.add_argument("url")
    p.add_argument("--allow-network", action="store_true")

    p = sub.add_parser("code-ingest", help="ingest a source repository into the code graph (requires the 'code' extra)")
    p.add_argument("repo", help="path to a source repository or directory")
    p.add_argument("--full", action="store_true", help="full re-parse instead of incremental")
    p.add_argument("--resolve", action="store_true", help="type-aware resolution via language servers and dataflow (pre-staged servers)")
    p.add_argument("--languages", default=None, help="path to a dkg.languages.json config registering additional parser languages")
    p.add_argument("--include-submodules", action="store_true", help="also collect git submodule contents (off by default)")
    p.add_argument("--postprocess", default="standard", choices=["none", "minimal", "standard", "full"], help="how much of the derived-view stage to run")

    p = sub.add_parser("code-postprocess", help="run the derived-view stage on its own: communities, flows, risk index, search index")
    p.add_argument("--level", default="standard", choices=["none", "minimal", "standard", "full"])
    p.add_argument("--stage", action="append", default=None, dest="stages", help="run only these stages, ignoring the level (repeatable)")
    p.add_argument("--resolution", type=float, default=1.0)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-flows", help="list, retrieve, or query the persisted execution-flow catalogue")
    p.add_argument("--name", default=None, help="retrieve one flow by name or identifier")
    p.add_argument("--changed", action="append", default=None, dest="changed_files", help="report which flows a changed file touches (repeatable)")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("code-summaries", help="read the precomputed community summaries and per-symbol risk index")
    p.add_argument("--what", default="communities", choices=["communities", "risk", "flows"])
    p.add_argument("--key", default=None, help="one community index, or one symbol canonical name")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("code-languages", help="list every language the source-code plane parses, how, and whether it is available here")
    p.add_argument("--available-only", action="store_true", help="list only languages whose grammar is installed in this environment")

    p = sub.add_parser("code-flow", help="trace structural execution flow (forward call chains) from a code entity")
    p.add_argument("entity", help="code entity id or qualified name (for example path/to/file.py::func)")
    p.add_argument("--depth", type=int, default=5)
    p.add_argument("--max-nodes", type=int, default=500)

    p = sub.add_parser("code-hubs", help="find the most connected symbols and the architectural chokepoints")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-coupling", help="score edges that are surprising given the surrounding structure")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--resolution", type=float, default=1.0)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-gaps", help="isolated symbols, untested hotspots, and thin communities")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--resolution", type=float, default=1.0)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-impact", help="structural blast radius for a code entity or file")
    p.add_argument("--entity", default=None, help="a code entity's canonical or short name")
    p.add_argument("--file", default=None, dest="impact_file", help="a repository-relative file path")
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--max-nodes", type=int, default=500)
    p.add_argument("--repo", default=".", help="repository root, used for the savings baseline")

    p = sub.add_parser("code-wiki", help="generate a browsable markdown knowledge base from the community structure")
    p.add_argument("out", help="directory to write the knowledge base into")
    p.add_argument("--full", action="store_true", help="rewrite every page instead of only what changed")
    p.add_argument("--resolution", type=float, default=1.0)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-forget", help="drop named paths from the code graph without a full rebuild")
    p.add_argument("paths", nargs="+", help="file or directory paths to forget")
    p.add_argument("--apply", action="store_true", help="actually delete; the default is a dry run")

    p = sub.add_parser("code-refactor", help="refactoring suggestions derived from community structure and coupling")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--per-kind", type=int, default=5)
    p.add_argument("--resolution", type=float, default=1.0)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-risk", help="advisory 0 to 1 risk score for a change set, with every factor's contribution")
    p.add_argument("--file", action="append", default=None, dest="risk_files", help="changed file (repeatable)")
    p.add_argument("--symbol", action="append", default=None, dest="risk_symbols", help="changed symbol (repeatable)")
    p.add_argument("--with-churn", action="store_true", help="opt in to the git change-frequency signal (off by default)")
    p.add_argument("--repo", default=".", help="repository root, needed only for --with-churn")
    p.add_argument("--churn-commits", type=int, default=500, help="how many commits of history to read")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-cochange", help="measure impact accuracy against git co-change, the non-circular ground truth")
    p.add_argument("--repo", default=".", help="repository root whose history supplies the ground truth")
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--max-commits", type=int, default=500)
    p.add_argument("--min-support", type=int, default=2, help="commits a pair must share to count")
    p.add_argument("--max-commit-files", type=int, default=25, help="commits wider than this are excluded")
    p.add_argument("--max-nodes", type=int, default=500)

    p = sub.add_parser("code-dead", help="candidate dead code: definitions nothing references and no entry point reaches")
    p.add_argument("--include-modules", action="store_true", help="also consider file-level module nodes")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-large", help="symbols at or above a line-count threshold you choose")
    p.add_argument("--min-lines", type=int, required=True, help="the threshold, inclusive")
    p.add_argument("--kind", action="append", default=None, dest="kinds", help="filter by symbol kind (repeatable)")
    p.add_argument("--path-prefix", default=None, help="restrict to one subtree")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-rename", help="preview a symbol rename, and apply it only when you confirm")
    p.add_argument("symbol", help="canonical name (path::Symbol) or a short name unique in the graph")
    p.add_argument("new_name", help="the new identifier")
    p.add_argument("--repo", default=".", help="repository root; every file read is confined to it")
    p.add_argument("--apply", action="store_true", help="write the change (needs --confirm as well)")
    p.add_argument("--confirm", action="store_true", help="acknowledge that applying edits source files")
    p.add_argument("--diff", action="store_true", help="print the unified diff instead of the JSON preview")
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-questions", help="suggested review questions generated from the graph analysis")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--per-category", type=int, default=5)
    p.add_argument("--resolution", type=float, default=1.0)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("code-architecture", help="component-level architecture overview with coupling warnings")
    p.add_argument("--format", dest="arch_format", default="markdown", choices=["markdown", "json"])
    p.add_argument("--out", default=None, help="write the overview to this file")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--resolution", type=float, default=1.0)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("graph-snapshot", help="write a code-graph snapshot for later comparison")
    p.add_argument("out", help="path to write the snapshot JSON to")
    p.add_argument("--label", default=None, help="human-readable label recorded in the snapshot")
    p.add_argument("--resolution", type=float, default=1.0)
    p.add_argument("--max-nodes", type=int, default=20000)

    p = sub.add_parser("graph-diff", help="compare two code-graph snapshots")
    p.add_argument("before", help="path to the earlier snapshot")
    p.add_argument("after", help="path to the later snapshot")

    # mcp-install, mcp-uninstall, mcp-tools, and code-report are registered by
    # their own surfaces through the extension point below.

    p = sub.add_parser("search", help="search")
    p.add_argument("query")
    p.add_argument("--mode", default="hybrid", choices=["keyword", "fts", "hybrid"])
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--source", default=None)
    p.add_argument("--entity", default=None)

    sub.add_parser(
        "reindex",
        help="re-embed all chunks with the active embedding model (run after changing the embedding backend)",
    )

    p = sub.add_parser("graph", help="graph query")
    p.add_argument("entity", help="entity ID or canonical name")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--max-nodes", type=int, default=100)

    p = sub.add_parser("community", help="detect communities over the entity graph (modularity optimization)")
    p.add_argument(
        "--detector",
        default="both",
        choices=["both", "mnemosyne", "ariadne"],
        help="both runs a Mnemosyne base pass and an Ariadne refinement pass (default)",
    )
    p.add_argument("--resolution", type=float, default=1.0)

    p = sub.add_parser("evidence", help="fetch evidence for a claim")
    p.add_argument("claim_id")

    p = sub.add_parser("export", help="export")
    p.add_argument(
        "--format",
        choices=["json", "markdown", "csv", "graphml", "dot", "cypher", "svg", "obsidian", "html"],
        required=True,
    )
    p.add_argument("--out", required=True)
    p.add_argument("--source", default=None, help="restrict to a source ID")

    p = sub.add_parser("backup", help="write a portable backup")
    p.add_argument("--out", required=True)

    p = sub.add_parser("restore", help="restore from a portable backup")
    p.add_argument("archive")
    p.add_argument("--home", dest="restore_home", default=None)

    p = sub.add_parser("audit", help="audit log")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--verify", action="store_true")

    p = sub.add_parser("agent", help="run a deterministic multi-agent workflow")
    p.add_argument("workflow", choices=["research", "verify", "contradiction", "security-review"])
    p.add_argument("--input", default="{}", help="workflow input as JSON")

    p = sub.add_parser("registry", help="manage the multi-repo watch registry")
    p.add_argument("action", choices=["add", "list", "remove"])
    p.add_argument("name", nargs="?", help="repository name (add, remove)")
    p.add_argument("path", nargs="?", help="repository path (add)")

    p = sub.add_parser("repos-search", help="search across every registered repository, with per-repository attribution")
    p.add_argument("query", help="the search query")
    p.add_argument("--limit", type=int, default=20, help="merged result cap")
    p.add_argument("--per-repo-limit", type=int, default=10, help="hits taken from each repository")
    p.add_argument("--max-repos", type=int, default=50)

    p = sub.add_parser("update", help="re-ingest only what changed in a repository (the one incremental update path)")
    p.add_argument("--repo", default=".", help="repository to update")
    p.add_argument("--quiet", action="store_true", help="print nothing on success")
    p.add_argument("--resolve", action="store_true", help="also run type-aware resolution")

    p = sub.add_parser("hooks", help="install or remove the editor-and-commit graph update hook")
    p.add_argument("action", choices=["install", "status", "uninstall"])
    p.add_argument("--repo", default=".", help="repository to act on")
    p.add_argument("--hook", default="post-commit", help="which git hook to use")
    p.add_argument("--force", action="store_true", help="replace a hook this project did not write")

    p = sub.add_parser(
        "watch",
        help="watch ONE repository and re-ingest incrementally as it changes (no registry needed)",
    )
    p.add_argument("--repo", default=".", help="repository to watch")
    p.add_argument("--once", action="store_true", help="run one scan-and-reingest pass, then exit")
    p.add_argument("--interval", type=float, default=1.0, help="polling interval in seconds")
    p.add_argument("--max-seconds", type=float, default=None, help="stop automatically after this many seconds")
    p.add_argument("--poll", action="store_true", help="force the polling backend even if watchfiles is installed")
    p.add_argument("--languages", default=None, help="path to a dkg.languages.json for custom languages")

    p = sub.add_parser(
        "service",
        help="run the multi-repo watcher as a managed background service (start, stop, restart, status, log)",
    )
    p.add_argument("action", choices=["start", "stop", "restart", "status", "log", "run"])
    p.add_argument("name", nargs="?", help="repository name (log); omit for the supervisor's own log")
    p.add_argument("--interval", type=float, default=1.0, help="per-repository polling interval in seconds")
    p.add_argument("--max-seconds", type=float, default=None, help="stop automatically after this many seconds")
    p.add_argument("--lines", type=int, default=200, help="log lines to show (log)")
    p.add_argument("--languages", default=None, help="path to a dkg.languages.json for custom languages")

    p = sub.add_parser("daemon", help="watch registered repos and re-ingest incrementally (bounded, local)")
    p.add_argument("--once", action="store_true", help="run one scan-and-reingest pass, then exit")
    p.add_argument("--interval", type=float, default=1.0, help="polling interval in seconds")
    p.add_argument("--max-seconds", type=float, default=None, help="stop automatically after this many seconds")
    p.add_argument("--poll", action="store_true", help="force the polling backend even if watchfiles is installed")
    p.add_argument("--languages", default=None, help="path to a dkg.languages.json for custom languages")

    sub.add_parser("mcp-stdio", help="run stdio MCP server on this process")
    p = sub.add_parser("mcp-http", help="run HTTP MCP server (loopback default)")
    p.add_argument("--bind", default=None)
    p.add_argument("--port", type=int, default=None)

    sub.add_parser("help", help="show help")

    # Surfaces that own their own parser and handler. See dkg.cli.extensions.
    extensions.register(sub)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _mk_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except DKGError as e:
        if getattr(args, "as_json", False):
            print_json(e.to_dict(), out=sys.stderr)
        else:
            print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd in (None, "help"):
        _mk_parser().print_help()
        return 0

    cfg = load_config(home=args.home)
    if args.cmd == "init":
        return _cmd_init(cfg, args)
    if args.cmd == "status":
        return _cmd_status(cfg, args)
    if args.cmd == "doctor":
        return _cmd_doctor(cfg, args)
    if args.cmd == "capabilities":
        return _cmd_capabilities(cfg, args)
    if args.cmd == "ingest":
        return _cmd_ingest(cfg, args)
    if args.cmd == "ingest-web":
        return _cmd_ingest_web(cfg, args)
    if args.cmd == "ingest-rss":
        return _cmd_ingest_rss(cfg, args)
    if args.cmd == "code-ingest":
        return _cmd_code_ingest(cfg, args)
    if args.cmd == "code-languages":
        return _cmd_code_languages(cfg, args)
    if args.cmd == "code-flow":
        return _cmd_code_flow(cfg, args)
    if args.cmd == "code-hubs":
        return _cmd_code_hubs(cfg, args)
    if args.cmd == "code-coupling":
        return _cmd_code_coupling(cfg, args)
    if args.cmd == "code-gaps":
        return _cmd_code_gaps(cfg, args)
    if args.cmd == "code-postprocess":
        return _cmd_code_postprocess(cfg, args)
    if args.cmd == "code-flows":
        return _cmd_code_flows(cfg, args)
    if args.cmd == "code-summaries":
        return _cmd_code_summaries(cfg, args)
    if args.cmd == "repos-search":
        return _cmd_repos_search(cfg, args)
    if args.cmd == "code-impact":
        return _cmd_code_impact(cfg, args)
    if args.cmd == "code-wiki":
        return _cmd_code_wiki(cfg, args)
    if args.cmd == "code-forget":
        return _cmd_code_forget(cfg, args)
    if args.cmd == "code-refactor":
        return _cmd_code_refactor(cfg, args)
    if args.cmd == "code-risk":
        return _cmd_code_risk(cfg, args)
    if args.cmd == "code-cochange":
        return _cmd_code_cochange(cfg, args)
    if args.cmd == "code-dead":
        return _cmd_code_dead(cfg, args)
    if args.cmd == "code-large":
        return _cmd_code_large(cfg, args)
    if args.cmd == "code-rename":
        return _cmd_code_rename(cfg, args)
    if args.cmd == "code-questions":
        return _cmd_code_questions(cfg, args)
    if args.cmd == "code-architecture":
        return _cmd_code_architecture(cfg, args)
    if args.cmd == "graph-snapshot":
        return _cmd_graph_snapshot(cfg, args)
    if args.cmd == "graph-diff":
        return _cmd_graph_diff(cfg, args)
    if args.cmd == "search":
        return _cmd_search(cfg, args)
    if args.cmd == "reindex":
        return _cmd_reindex(cfg, args)
    if args.cmd == "community":
        return _cmd_community(cfg, args)
    if args.cmd == "graph":
        return _cmd_graph(cfg, args)
    if args.cmd == "evidence":
        return _cmd_evidence(cfg, args)
    if args.cmd == "export":
        return _cmd_export(cfg, args)
    if args.cmd == "backup":
        return _cmd_backup(cfg, args)
    if args.cmd == "restore":
        return _cmd_restore(cfg, args)
    if args.cmd == "audit":
        return _cmd_audit(cfg, args)
    if args.cmd == "agent":
        return _cmd_agent(cfg, args)
    if args.cmd == "update":
        return _cmd_update(cfg, args)
    if args.cmd == "hooks":
        return _cmd_hooks(cfg, args)
    if args.cmd == "registry":
        return _cmd_registry(cfg, args)
    if args.cmd == "watch":
        return _cmd_watch(cfg, args)
    if args.cmd == "service":
        return _cmd_service(cfg, args)
    if args.cmd == "daemon":
        return _cmd_daemon(cfg, args)
    if args.cmd == "mcp-stdio":
        return _cmd_mcp_stdio(cfg, args)
    if args.cmd == "mcp-http":
        return _cmd_mcp_http(cfg, args)
    # Surfaces that own their own handler. A command none of them claims is
    # genuinely unknown and is reported as such below, exactly as before.
    code = extensions.dispatch(cfg, args)
    if code is not None:
        return code
    raise DKGError(f"unknown command: {args.cmd}")


# -- commands --------------------------------------------------------


def _cmd_code_ingest(cfg, args) -> int:
    from ..code.ingest import ingest_repo
    from ..code.languages import LanguageRegistry, default_config_path, load_registry

    languages: LanguageRegistry | None = None
    config_path = args.languages
    if config_path is None:
        default_path = default_config_path(cfg.home)
        if default_path.exists():
            config_path = str(default_path)
    if config_path is not None:
        languages, warnings = load_registry(config_path)
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)

    with open_database(cfg.db_path) as db:
        record_open(db)
        result = ingest_repo(
            db,
            args.repo,
            audit_path=cfg.audit_path,
            incremental=not args.full,
            resolve=args.resolve,
            languages=languages,
            include_submodules=args.include_submodules,
            postprocess=args.postprocess,
        )
    if args.as_json:
        print_json(result)
    else:
        keys = ("mode", "files", "nodes", "edges", "parsed_files", "unchanged_files", "removed_files")
        rows = [(k, result.get(k)) for k in keys]
        # The effective exclusion set is part of the result, not a footnote: an
        # index that silently omitted files looks the same as one that could
        # not find them.
        rows.append(("ignored_files", result.get("ignored", {}).get("excluded_count", 0)))
        rows.append(("submodules_included", result.get("submodules", {}).get("included", False)))
        print_kv(rows)
    return 0


def _cmd_code_postprocess(cfg, args) -> int:
    from ..code.postprocess import run_postprocess

    with open_database(cfg.db_path) as db:
        result = run_postprocess(
            db,
            level=args.level,
            stages=tuple(args.stages) if args.stages else None,
            resolution=args.resolution,
            max_nodes=args.max_nodes,
        )
    print_json(result)
    return 0


def _cmd_code_flows(cfg, args) -> int:
    from ..code.catalogue import flows_affected_by, get_flow, list_flows

    with open_database(cfg.db_path) as db:
        if args.name:
            result = get_flow(db, args.name)
        elif args.changed_files:
            result = flows_affected_by(db, args.changed_files, limit=args.limit)
        else:
            result = list_flows(db, limit=args.limit)
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_summaries(cfg, args) -> int:
    from ..code.catalogue import community_summary, list_flows, symbol_risk

    with open_database(cfg.db_path) as db:
        if args.what == "risk":
            result = symbol_risk(db, args.key, limit=args.limit)
        elif args.what == "flows":
            result = list_flows(db, limit=args.limit)
        else:
            index = int(args.key) if args.key is not None else None
            result = community_summary(db, index, limit=args.limit)
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_impact(cfg, args) -> int:
    from ..code.impact import blast_radius, blast_radius_for_file

    if not args.entity and not args.impact_file:
        print("code-impact needs --entity or --file", file=sys.stderr)
        return 2
    with open_database(cfg.db_path) as db:
        if args.impact_file:
            result = blast_radius_for_file(
                db, args.impact_file, depth=args.depth, max_nodes=args.max_nodes
            )
        else:
            result = blast_radius(db, args.entity, depth=args.depth, max_nodes=args.max_nodes)
    print_json(_budgeted(args, _saved(args, result, args.repo)))
    return 0


def _cmd_code_wiki(cfg, args) -> int:
    from ..code.wiki import build_wiki

    with open_database(cfg.db_path) as db:
        result = build_wiki(
            db,
            args.out,
            resolution=args.resolution,
            max_nodes=args.max_nodes,
            incremental=not args.full,
        )
    print_json(result)
    return 0


def _cmd_code_forget(cfg, args) -> int:
    from ..code.forget import forget_paths

    with open_database(cfg.db_path) as db:
        result = forget_paths(
            db, args.paths, dry_run=not args.apply, audit_path=cfg.audit_path
        )
    print_json(result)
    return 0


def _cmd_code_languages(cfg, args) -> int:
    from ..code.parser import language_inventory

    del cfg
    inventory = language_inventory()
    if getattr(args, "available_only", False):
        inventory = {k: v for k, v in inventory.items() if v["available"]}
    by_fidelity: dict[str, list[str]] = {}
    for name, entry in inventory.items():
        by_fidelity.setdefault(entry["fidelity"], []).append(name)
    print_json(
        _budgeted(
            args,
            {
                "languages": inventory,
                "total": len(inventory),
                "by_fidelity": {k: sorted(v) for k, v in sorted(by_fidelity.items())},
                "why": (
                    "Fidelity says how a language is read: 'grammar' is a Tree-sitter parse, "
                    "'composite' unwraps the file and parses its code with another language's "
                    "grammar, and 'fallback' is the documented lower-fidelity pattern extractor "
                    "used where no permissive grammar is installable. Measured accuracy per "
                    "language is in docs/BENCHMARKS.md."
                ),
            },
        )
    )
    return 0


def _cmd_code_flow(cfg, args) -> int:
    from ..code.flow import execution_flow

    with open_database(cfg.db_path) as db:
        result = execution_flow(db, args.entity, depth=args.depth, max_nodes=args.max_nodes)
    print_json(_budgeted(args, result))
    return 0


def _budgeted(args, payload: dict) -> dict:
    """Apply the global --token-budget to a structured payload, if one is set."""
    from ..context.pack import apply_budget

    return apply_budget(payload, budget=getattr(args, "token_budget", None))


def _saved(args, payload: dict, repo_root=None) -> dict:
    """Attach the estimated context-savings record, unless it was declined.

    Attached BEFORE the token budget is applied, so the record describes the
    answer that was computed rather than the trimmed one, and the trim then
    applies to it like everything else.
    """
    from ..context.savings import attach_savings

    return attach_savings(
        payload,
        repo_root=repo_root or getattr(args, "repo", None) or ".",
        verify=bool(getattr(args, "verify_savings", False)),
        enabled=not bool(getattr(args, "no_savings", False)),
    )


def _cmd_code_hubs(cfg, args) -> int:
    from ..code.centrality import hubs_and_bridges

    with open_database(cfg.db_path) as db:
        result = hubs_and_bridges(db, limit=args.limit, max_nodes=args.max_nodes)
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_coupling(cfg, args) -> int:
    from ..code.coupling import unexpected_coupling

    with open_database(cfg.db_path) as db:
        result = unexpected_coupling(
            db, limit=args.limit, resolution=args.resolution, max_nodes=args.max_nodes
        )
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_gaps(cfg, args) -> int:
    from ..code.gaps import knowledge_gaps

    with open_database(cfg.db_path) as db:
        result = knowledge_gaps(
            db, limit=args.limit, resolution=args.resolution, max_nodes=args.max_nodes
        )
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_refactor(cfg, args) -> int:
    from ..code.refactor import refactor_suggestions

    with open_database(cfg.db_path) as db:
        result = refactor_suggestions(
            db,
            limit=args.limit,
            per_kind=args.per_kind,
            resolution=args.resolution,
            max_nodes=args.max_nodes,
        )
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_risk(cfg, args) -> int:
    from ..code.risk import change_risk

    with open_database(cfg.db_path) as db:
        result = change_risk(
            db,
            files=args.risk_files,
            symbols=args.risk_symbols,
            repo=args.repo,
            with_churn=args.with_churn,
            churn_commits=args.churn_commits,
            limit=args.limit,
            max_nodes=args.max_nodes,
        )
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_cochange(cfg, args) -> int:
    from ..code.cochange import measure_against_cochange

    with open_database(cfg.db_path) as db:
        result = measure_against_cochange(
            db,
            args.repo,
            depth=args.depth,
            max_commits=args.max_commits,
            min_support=args.min_support,
            max_commit_files=args.max_commit_files,
            max_nodes=args.max_nodes,
        )
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_dead(cfg, args) -> int:
    from ..code.deadcode import dead_code_candidates

    with open_database(cfg.db_path) as db:
        result = dead_code_candidates(
            db,
            include_modules=args.include_modules,
            limit=args.limit,
            max_nodes=args.max_nodes,
        )
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_large(cfg, args) -> int:
    from ..code.size import large_symbols

    with open_database(cfg.db_path) as db:
        result = large_symbols(
            db,
            min_lines=args.min_lines,
            kinds=args.kinds,
            path_prefix=args.path_prefix,
            limit=args.limit,
            max_nodes=args.max_nodes,
        )
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_rename(cfg, args) -> int:
    """Preview a rename, and apply it only when both flags are given.

    Applying lives here and not on the MCP surface deliberately: it writes
    source, and the surface is the boundary against an agent acting on content
    it was fed. On the command line the confirmation is typed by a person.
    """
    from ..code.rename import apply_rename, preview_rename

    with open_database(cfg.db_path) as db:
        preview = preview_rename(
            db, args.symbol, args.new_name, repo_root=args.repo, max_nodes=args.max_nodes
        )
    if not preview["resolved"]:
        print_json(preview)
        return 2
    if args.apply:
        result = apply_rename(
            preview, repo_root=args.repo, confirm=args.confirm, dry_run=not args.confirm
        )
        if args.diff:
            print(result["diff"], end="")
            return 0
        print_json(result)
        return 0 if result["applied"] else 3
    if args.diff:
        from ..code.rename import render_diff

        print(render_diff(preview, repo_root=args.repo), end="")
        return 0
    print_json(_budgeted(args, preview))
    return 0


def _cmd_code_questions(cfg, args) -> int:
    from ..code.review import review_questions

    with open_database(cfg.db_path) as db:
        result = review_questions(
            db,
            limit=args.limit,
            per_category=args.per_category,
            resolution=args.resolution,
            max_nodes=args.max_nodes,
        )
    result = _saved(args, result)
    print_json(_budgeted(args, result))
    return 0


def _cmd_code_architecture(cfg, args) -> int:
    from ..code.architecture import architecture_map, render_markdown

    with open_database(cfg.db_path) as db:
        result = architecture_map(
            db, limit=args.limit, resolution=args.resolution, max_nodes=args.max_nodes
        )
    # Attached to the structured form. The Markdown rendering is prose for a
    # human, and a token accounting block inside it would be noise.
    result = _saved(args, result)
    if args.arch_format == "json":
        if args.out:
            Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print_json(result)
        return 0
    text = render_markdown(result)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def _cmd_graph_snapshot(cfg, args) -> int:
    from ..code.diff import snapshot_code_graph

    with open_database(cfg.db_path) as db:
        snapshot = snapshot_code_graph(
            db, resolution=args.resolution, max_nodes=args.max_nodes, label=args.label
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counts = snapshot.get("counts", {})
    print(f"wrote {out} ({counts.get('nodes')} nodes, {counts.get('edges')} edges)")
    return 0


def _cmd_graph_diff(cfg, args) -> int:
    from ..code.diff import diff_snapshots, load_snapshot

    # Reads two files only; the database is not opened at all.
    result = diff_snapshots(load_snapshot(args.before), load_snapshot(args.after))
    print_json(_budgeted(args, result))
    return 0


def _load_language_registry(cfg, config_path):
    """Resolve a custom-language registry from an explicit path or the home default."""
    from ..code.languages import default_config_path, load_registry

    if config_path is None:
        default_path = default_config_path(cfg.home)
        if default_path.exists():
            config_path = str(default_path)
    if config_path is None:
        return None
    languages, warnings = load_registry(config_path)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return languages


def _cmd_repos_search(cfg, args) -> int:
    from ..search.federated import search_registered
    from ..watch.registry import Registry

    result = search_registered(
        args.query,
        registry=Registry.in_home(cfg.home),
        limit=args.limit,
        per_repo_limit=args.per_repo_limit,
        max_repos=args.max_repos,
        token_budget=getattr(args, "token_budget", None),
    )
    print_json(result)
    return 0


def _cmd_registry(cfg, args) -> int:
    from ..watch.registry import Registry

    reg = Registry.in_home(cfg.home)
    if args.action == "add":
        if not args.name or not args.path:
            raise DKGError("usage: dkg registry add <name> <path>")
        entry = reg.add(args.name, args.path)
        print_json({"added": {"name": entry.name, "path": entry.path}})
        return 0
    if args.action == "remove":
        if not args.name:
            raise DKGError("usage: dkg registry remove <name>")
        reg.remove(args.name)
        print_json({"removed": args.name})
        return 0
    repos = [{"name": e.name, "path": e.path} for e in reg.list()]
    if args.as_json:
        print_json({"repos": repos})
    elif repos:
        print_table(["name", "path"], [[r["name"], r["path"]] for r in repos])
    else:
        print("no repositories registered")
    return 0


def _cmd_watch(cfg, args) -> int:
    """Watch exactly one repository, with no registry and no multi-repo daemon.

    Deliberately the same WatchDaemon the multi-repository path uses, handed a
    registry that holds one entry and is never written to disk. One watcher
    implementation stays under test, and this command leaves no registry file
    behind and does not touch an existing one.
    """
    from ..watch.daemon import WatchDaemon
    from ..watch.registry import TransientRegistry

    reg = TransientRegistry.for_repo(args.repo)
    languages = _load_language_registry(cfg, args.languages)
    daemon = WatchDaemon(
        cfg.db_path,
        reg,
        audit_path=cfg.audit_path,
        poll_interval=args.interval,
        use_watchfiles=(False if args.poll else None),
        languages=languages,
    )
    entry = reg.list()[0]
    if args.once:
        results = daemon.poll_once()
        payload = {
            "backend": daemon.backend,
            "repo": entry.path,
            "results": results,
            "health": daemon.health(),
        }
        print_json(payload)
        return 0
    daemon.run(max_seconds=args.max_seconds)
    health = daemon.health()
    health["repo"] = entry.path
    print_json(health) if args.as_json else print(f"watch stopped: {entry.path}")
    return 0


def _cmd_service(cfg, args) -> int:
    """Managed background service over the multi-repository watcher.

    `run` is the supervisor itself and is what `start` spawns. It is a public
    action rather than a hidden flag so that the thing the service does can be
    run in the foreground and watched, which is the first thing anyone debugging
    a service that will not start needs to do.
    """
    from ..watch.registry import Registry
    from ..watch.service import (
        ServicePaths,
        Supervisor,
        list_service_logs,
        read_service_log,
        release_pid_file,
        service_status,
        start_service,
        stop_service,
    )

    paths = ServicePaths(Path(cfg.home))
    action = args.action

    if action == "status":
        print_json(service_status(paths))
        return 0

    if action == "log":
        if args.name is None and not (paths.log_dir / "service.log").is_file():
            available = list_service_logs(paths)
            raise DKGError(
                "no supervisor log yet"
                + (f"; available logs: {', '.join(available)}" if available else "")
            )
        payload = read_service_log(paths, args.name, lines=args.lines)
        if args.as_json:
            print_json(payload)
        else:
            print(payload["log"])
            for line in payload["lines"]:
                print(line)
        return 0

    if action == "stop":
        print_json(stop_service(paths))
        return 0

    if action in ("start", "restart"):
        if action == "restart":
            stop_service(paths)
        result = start_service(
            paths,
            home=cfg.home,
            interval=args.interval,
            max_seconds=args.max_seconds,
            languages=args.languages,
        )
        print_json(result)
        return 0

    # action == "run": this process IS the supervisor.
    import os as _os
    import signal as _signal

    from ..watch.service import claim_pid_file

    if not claim_pid_file(paths, _os.getpid()):
        raise DKGError("another watch service already holds the process-identity file")

    registry = Registry.in_home(cfg.home)
    languages = _load_language_registry(cfg, args.languages)
    supervisor = Supervisor(
        cfg.db_path,
        registry,
        paths,
        audit_path=cfg.audit_path,
        poll_interval=args.interval,
        languages=languages,
    )

    def _handle(_signum, _frame):
        supervisor.stop()

    # A service that ignores SIGTERM cannot be stopped by `dkg service stop`.
    for sig in (_signal.SIGTERM, _signal.SIGINT):
        try:
            _signal.signal(sig, _handle)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            pass

    try:
        supervisor.run(max_seconds=args.max_seconds, cycle_interval=args.interval)
    finally:
        release_pid_file(paths, _os.getpid())
    return 0


def _cmd_daemon(cfg, args) -> int:
    from ..watch.daemon import WatchDaemon
    from ..watch.registry import Registry

    reg = Registry.in_home(cfg.home)
    languages = _load_language_registry(cfg, args.languages)
    daemon = WatchDaemon(
        cfg.db_path,
        reg,
        audit_path=cfg.audit_path,
        poll_interval=args.interval,
        use_watchfiles=(False if args.poll else None),
        languages=languages,
    )
    if len(reg) == 0:
        raise DKGError("no repositories registered; add one with 'dkg registry add <name> <path>'")
    if args.once:
        results = daemon.poll_once()
        payload = {"backend": daemon.backend, "results": results, "health": daemon.health()}
        print_json(payload) if args.as_json else print_json(payload)
        return 0
    daemon.run(max_seconds=args.max_seconds)
    health = daemon.health()
    print_json(health) if args.as_json else print("daemon stopped")
    return 0


def _cmd_init(cfg, args) -> int:
    cfg.home.mkdir(parents=True, exist_ok=True)
    (cfg.home / "config.json").write_text(
        json.dumps(
            {"network": {"allow_outbound": False}, "telemetry": {"enabled": False}},
            indent=2,
        ),
        encoding="utf-8",
    )
    (cfg.home / ".init-marker").write_text("dkg\n", encoding="utf-8")
    with open_database(cfg.db_path) as db:
        record_open(db)
        AuditLog(db, cfg.audit_path).record(
            AuditEntry(action="config.update", outcome="ok", details={"init": True})
        )
    if args.as_json:
        print_json({"ok": True, "home": str(cfg.home)})
    else:
        print(f"initialised D-Knowledge_Graph home at {cfg.home}")
    return 0


def _cmd_status(cfg, args) -> int:
    with open_database(cfg.db_path) as db:
        vi = record_open(db)
        doc_row = db.fetchone("SELECT COUNT(*) AS n FROM documents;")
        doc_count = int(doc_row["n"]) if doc_row else 0
        chunk_row = db.fetchone("SELECT COUNT(*) AS n FROM chunks;")
        chunk_count = int(chunk_row["n"]) if chunk_row else 0
        entity_row = db.fetchone("SELECT COUNT(*) AS n FROM entities;")
        entity_count = int(entity_row["n"]) if entity_row else 0
        claim_row = db.fetchone("SELECT COUNT(*) AS n FROM claims;")
        claim_count = int(claim_row["n"]) if claim_row else 0
    payload = {
        "app_version": vi.app,
        "schema_major": vi.schema_major,
        "home": str(cfg.home),
        "documents": doc_count,
        "chunks": chunk_count,
        "entities": entity_count,
        "claims": claim_count,
        "network_allowed": cfg.network.allow_outbound,
        "telemetry_enabled": cfg.telemetry.enabled,
    }
    if args.as_json:
        print_json(payload)
    else:
        print_kv([(k, v) for k, v in payload.items()])
    return 0


def _cmd_doctor(cfg, args) -> int:
    reg = default_registry()
    with open_database(cfg.db_path) as db:
        vi = record_open(db)
        chain_ok, break_at = AuditLog(db).verify_chain()
    payload = {
        "app_version": vi.app,
        "schema_major": vi.schema_major,
        "home": str(cfg.home),
        "capabilities": reg.describe(),
        "audit_chain_ok": chain_ok,
        "audit_chain_break": break_at,
        "notes": [
            "Read-only defaults. Outbound network requires explicit opt-in.",
            "No LLM is bundled; deterministic extractors are used by default.",
        ],
    }
    print_json(payload)
    return 0 if chain_ok else 3


def _cmd_capabilities(cfg, args) -> int:
    reg = default_registry()
    if args.as_json:
        print_json({"capabilities": reg.describe()})
        return 0
    print_table(
        ["NAME", "AVAILABLE", "KIND", "REASON"],
        [[c["name"], c["available"], c["kind"], c["reason"]] for c in reg.describe()],
    )
    return 0


def _cmd_ingest(cfg, args) -> int:
    from ..ingest.base import ingest_path

    path = Path(args.path)
    with open_database(cfg.db_path) as db:
        report = ingest_path(db, path, forced_format=args.format, recursive=args.recursive, dry_run=args.dry_run, audit_path=cfg.audit_path)
    if args.as_json:
        print_json(report)
    else:
        print(f"ingested {report['documents_added']} documents / {report['chunks_added']} chunks")
        if report["skipped"]:
            print(f"skipped: {len(report['skipped'])}")
    return 0


def _cmd_ingest_web(cfg, args) -> int:
    if not args.allow_network:
        raise DKGError("--allow-network is required to fetch URLs")
    from ..ingest.web import ingest_url

    with open_database(cfg.db_path) as db:
        report = ingest_url(db, args.url, cfg=cfg)
    print_json(report) if args.as_json else print(report["message"])
    return 0


def _cmd_ingest_rss(cfg, args) -> int:
    if not args.allow_network:
        raise DKGError("--allow-network is required to fetch feeds")
    from ..ingest.rss import ingest_feed

    with open_database(cfg.db_path) as db:
        report = ingest_feed(db, args.url, cfg=cfg)
    print_json(report) if args.as_json else print(report["message"])
    return 0


def _cmd_search(cfg, args) -> int:
    from ..search.fts import fts_search
    from ..search.hybrid import hybrid_search
    from ..search.keyword import keyword_search

    with open_database(cfg.db_path) as db:
        if args.mode == "keyword":
            results = keyword_search(db, args.query, limit=args.limit, source_id=args.source)
        elif args.mode == "fts":
            results = fts_search(db, args.query, limit=args.limit)
        else:
            results = hybrid_search(db, args.query, limit=args.limit, source_id=args.source)
    if args.as_json:
        print_json({"query": args.query, "mode": args.mode, "results": results})
    else:
        for r in results:
            print(f"[{r['score']:.3f}] {r['chunk_id']} {r['snippet']}")
    return 0


def _cmd_reindex(cfg, args) -> int:
    from ..adapters.embedding import default_embedding_adapter
    from ..search.vector_index import reindex

    adapter = default_embedding_adapter()
    ok, why = adapter.available()
    if adapter.name == "hashing" or not ok:
        print_json(
            {
                "reindexed": False,
                "adapter": adapter.name,
                "reason": f"no real embedding model available: {why}. Install the "
                "'embeddings' extra and pre-stage a model, then re-run.",
            }
        )
        return 1
    with open_database(cfg.db_path) as db:
        summary = reindex(db, adapter=adapter)
    print_json({"reindexed": True, **summary})
    return 0


def _cmd_community(cfg, args) -> int:
    from ..graph.community import communities_from_db

    with open_database(cfg.db_path) as db:
        if args.detector == "both":
            from ..graph.community import communities_combined

            result = communities_combined(db, resolution=args.resolution)
        elif args.detector == "ariadne":
            try:
                from ..ariadne import detect_communities_ariadne

                result = detect_communities_ariadne(db, resolution=args.resolution)
            except Exception as e:
                result = communities_from_db(db, resolution=args.resolution)
                result["fallback"] = f"ariadne unavailable: {e!r}; used mnemosyne"
        else:
            result = communities_from_db(db, resolution=args.resolution)
    print_json(_budgeted(args, result))
    return 0


def _cmd_graph(cfg, args) -> int:
    from ..graph.query import neighbourhood

    with open_database(cfg.db_path) as db:
        result = neighbourhood(db, args.entity, depth=args.depth, max_nodes=args.max_nodes)
    print_json(_budgeted(args, result))
    return 0


def _cmd_evidence(cfg, args) -> int:
    from ..evidence.ledger import claim_evidence

    with open_database(cfg.db_path) as db:
        result = claim_evidence(db, args.claim_id)
    print_json(_budgeted(args, result))
    return 0


def _cmd_export(cfg, args) -> int:
    from ..export.csv_ import export_csv
    from ..export.graphml import export_graphml
    from ..export.json_ import export_json
    from ..export.markdown import export_markdown

    with open_database(cfg.db_path) as db:
        out = Path(args.out)
        if args.format == "json":
            export_json(db, out, source_id=args.source)
        elif args.format == "markdown":
            export_markdown(db, out, source_id=args.source)
        elif args.format == "csv":
            export_csv(db, out, source_id=args.source)
        elif args.format == "graphml":
            export_graphml(db, out)
        elif args.format == "dot":
            from ..export.dot import export_dot

            export_dot(db, out)
        elif args.format == "cypher":
            from ..export.cypher import export_cypher

            export_cypher(db, out)
        elif args.format == "svg":
            from ..export.svg import export_svg

            export_svg(db, out)
        elif args.format == "obsidian":
            from ..export.obsidian import export_obsidian

            export_obsidian(db, out)
        elif args.format == "html":
            from ..export.viz import export_html

            export_html(db, out)
    print_json({"ok": True, "out": str(out)}) if args.as_json else print(f"exported to {out}")
    return 0


def _cmd_backup(cfg, args) -> int:
    from ..export.backup import make_backup

    out = Path(args.out)
    with open_database(cfg.db_path) as db:
        info = make_backup(db, out)
    print_json(info) if args.as_json else print(f"backup written to {out}")
    return 0


def _cmd_restore(cfg, args) -> int:
    from ..export.backup import restore_backup

    home = Path(args.restore_home or cfg.home)
    result = restore_backup(Path(args.archive), home)
    print_json(result) if args.as_json else print(f"restored into {home}")
    return 0


def _cmd_audit(cfg, args) -> int:
    with open_database(cfg.db_path) as db:
        log = AuditLog(db, cfg.audit_path)
        if args.verify:
            ok, break_at = log.verify_chain()
            print_json({"chain_ok": ok, "first_break": break_at})
            return 0 if ok else 3
        entries = log.list(limit=args.limit)
        print_json(entries)
        return 0


def _cmd_agent(cfg, args) -> int:
    from ..agents.coordinator import run_workflow

    payload = json.loads(args.input or "{}")
    with open_database(cfg.db_path) as db:
        result = run_workflow(db, args.workflow, payload, cfg=cfg)
    print_json(_budgeted(args, result))
    return 0


def _cmd_update(cfg, args) -> int:
    """Re-ingest only what changed. Used by the editor, the hook, and by hand."""
    from ..watch.hooks import update_now

    with open_database(cfg.db_path) as db:
        result = update_now(
            db, Path(args.repo), audit_path=cfg.audit_path, resolve=bool(args.resolve)
        )
    if not args.quiet:
        print_json(result) if getattr(args, "as_json", False) else print(
            f"updated {result['repo']}: {result['nodes']} nodes, {result['edges']} edges"
        )
    return 0


def _cmd_hooks(cfg, args) -> int:
    from ..watch import hooks

    repo = Path(args.repo)
    if args.action == "install":
        result = hooks.install(repo, name=args.hook, home=cfg.home, force=bool(args.force))
        print_json(result)
        # A refusal to clobber is not an error, but it is not success either, so
        # the exit code says which happened rather than always claiming success.
        return 0 if result.get("installed") else 1
    if args.action == "uninstall":
        print_json(hooks.uninstall(repo, name=args.hook))
        return 0
    state = hooks.status(repo, args.hook)
    print_json(
        {
            "hook": state.name,
            "path": str(state.path),
            "installed": state.installed,
            "written_by_this_project": state.ours,
            "reason": state.reason,
        }
    )
    return 0


def _cmd_mcp_stdio(cfg, args) -> int:
    from ..mcp.server_stdio import serve_stdio

    with open_database(cfg.db_path) as db:
        serve_stdio(db)
    return 0


def _cmd_mcp_http(cfg, args) -> int:
    from ..mcp.server_http import serve_http

    bind = args.bind or cfg.mcp.http_bind
    port = args.port or cfg.mcp.http_port
    with open_database(cfg.db_path) as db:
        serve_http(db, host=bind, port=port, cfg=cfg)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
