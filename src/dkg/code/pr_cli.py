"""CLI surface for the repository report and the pull-request review comment.

Two subcommands live here so the pull-request reporting path owns its own parser
and handler rather than growing inside the shared entry module:

``code-report``   analyse a repository, optionally build the review block, gate
                  on a named risk level, and write the rendered comment.
``pr-publish``    validate a rendered comment and put it in the one sticky
                  pull-request comment its marker owns.

``pr-publish`` is the only command in this project that can make an outbound
call, and it will not make one unless ``--allow-egress`` is passed. The air-gap
default is not weakened by its existence: without that flag it either performs a
dry run or refuses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COMMANDS = ("code-report", "pr-publish")

# Named risk levels the gate accepts, plus the off switch that is the default.
# Imported lazily in the handler; spelled out here so `--help` works without the
# code extra installed.
RISK_GATE_CHOICES = ("off", "low", "moderate", "elevated", "high")

# Bound on the body file the publication path will read. A comment is capped at
# 65000 bytes anyway, so anything far larger is a mistake or an attack.
MAX_BODY_FILE_BYTES = 4 * 1024 * 1024


def register(sub: argparse._SubParsersAction) -> None:
    _register_code_report(sub)
    _register_pr_publish(sub)


def _register_code_report(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "code-report",
        help="analyze a repository and write a structural code report (requires the 'code' extra)",
    )
    p.add_argument("repo", help="path to a source repository or directory")
    p.add_argument("--base", default=None, help="git base ref for changed-file impact (for example the PR base SHA)")
    p.add_argument("--format", dest="report_format", default="markdown", choices=["markdown", "json"])
    p.add_argument("--out", default=None, help="write the report to this file")
    p.add_argument(
        "--fail-on-impact",
        dest="fail_on_impact",
        type=int,
        default=None,
        help=(
            "DEPRECATED advisory gate: exit non-zero if the structural impacted-entity "
            "count exceeds N. A raw count is not comparable across repositories; prefer "
            "--risk-gate. Behaviour is unchanged"
        ),
    )
    p.add_argument(
        "--risk-gate",
        dest="risk_gate",
        default="off",
        choices=list(RISK_GATE_CHOICES),
        help=(
            "gate the run on a NAMED risk level, off by default. The run fails when the "
            "observed level is at or above this one. Thresholds are derived from this "
            "graph's own score distribution and are published in the output. Note that "
            "'low' is the bottom cut, so gating there fails on any scored change"
        ),
    )
    p.add_argument(
        "--review",
        action="store_true",
        help="build the review block (risk level, changed symbols, flows, test gaps, token saving)",
    )
    p.add_argument(
        "--comment-out",
        dest="comment_out",
        default=None,
        help="render the pull-request review comment to this file (implies --review)",
    )
    p.add_argument(
        "--marker",
        default=None,
        help="hidden marker key identifying the sticky comment (default dkg-code-review)",
    )
    p.add_argument("--top", type=int, default=10, help="rows per table in the review (default 10)")
    p.add_argument(
        "--cache-check",
        dest="cache_check",
        action="store_true",
        help=(
            "validate a restored graph database before analysing it; when it is unusable "
            "the file is removed and the graph is rebuilt in full"
        ),
    )
    p.add_argument("--full", action="store_true", help="full re-parse instead of git-incremental")
    p.add_argument("--languages", default=None, help="path to a dkg.languages.json for custom languages")


def _register_pr_publish(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "pr-publish",
        help="validate a rendered review comment and post it as one sticky pull-request comment",
    )
    p.add_argument("--body-file", dest="body_file", required=True, help="the rendered comment to post")
    p.add_argument("--repo", dest="pr_repo", required=True, help="OWNER/NAME of the repository")
    p.add_argument("--pr", dest="pr_number", type=int, required=True, help="pull-request number")
    p.add_argument("--marker", default=None, help="marker key (default dkg-code-review)")
    p.add_argument("--api-base", dest="api_base", default=None, help="API base URL (https only)")
    p.add_argument(
        "--token-env",
        dest="token_env",
        default="GITHUB_TOKEN",
        help="environment variable holding the API token (default GITHUB_TOKEN)",
    )
    p.add_argument(
        "--allow-egress",
        dest="allow_egress",
        action="store_true",
        help=(
            "EXPLICIT OPT-IN OUTBOUND CALL. Without this the command validates and, with "
            "--dry-run, reports what it would do, but never contacts the network"
        ),
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="validate the body locally and report the planned action without writing",
    )
    p.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")


def dispatch(cfg, args) -> int | None:
    if args.cmd not in COMMANDS:
        return None
    if args.cmd == "pr-publish":
        return _cmd_pr_publish(cfg, args)
    return _cmd_code_report(cfg, args)


# -- code-report -------------------------------------------------------------


def _cmd_code_report(cfg, args) -> int:
    from ..cli.entry import _load_language_registry, _saved
    from ..cli.output import print_json
    from ..core.db import open_database
    from ..core.version import record_open
    from .ingest import ingest_repo
    from .pr_comment import DEFAULT_MARKER_KEY, render_pr_comment
    from .report import build_report, evaluate_gates, prepare_cached_database, render_markdown

    # Anything that consumes the review implies it. Reporting a gate verdict with
    # no score behind it would be a verdict nobody can check.
    want_review = bool(args.review or args.comment_out or args.risk_gate != "off")

    if args.fail_on_impact is not None:
        print(
            "warning: --fail-on-impact is deprecated. It compares an over-approximate "
            "impacted-entity count against an integer, which is not comparable across "
            "repositories. Its behaviour is unchanged; prefer --risk-gate.",
            file=sys.stderr,
        )

    cache = prepare_cached_database(cfg.db_path) if args.cache_check else None
    # A restored database that failed validation has been deleted, so there is
    # nothing to be incremental against and the rebuild must be a full one.
    incremental = (not args.full) and (cache is None or cache["status"] == "hit")

    languages = _load_language_registry(cfg, args.languages)
    with open_database(cfg.db_path) as db:
        record_open(db)
        ingest_repo(db, args.repo, audit_path=cfg.audit_path, incremental=incremental, languages=languages)
        report = build_report(
            db, args.repo, base=args.base, languages=languages, review=want_review, top=args.top
        )
    if cache is not None:
        report["cache"] = cache
    report["gate"] = evaluate_gates(
        report, risk_gate=args.risk_gate, fail_on_impact=args.fail_on_impact
    )
    report = _saved(args, report, args.repo)

    marker_key = args.marker or DEFAULT_MARKER_KEY
    if args.comment_out:
        comment = render_pr_comment(report, marker_key=marker_key, top=args.top)
        out = Path(args.comment_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(comment, encoding="utf-8")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if args.report_format == "json":
            out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        else:
            out.write_text(render_markdown(report), encoding="utf-8")

    if args.as_json:
        print_json(report)
    elif args.out:
        print(f"wrote report to {args.out}")
    else:
        print(render_markdown(report))

    gate = report["gate"]
    if gate["risk"]["failed"]:
        print(
            f"risk gate failed: observed level {gate['risk']['observed_level']} "
            f"(score {gate['risk']['observed_score']}) is at or above the gate at "
            f"{gate['risk']['requested']}. Published cuts: {gate['risk']['cuts']}",
            file=sys.stderr,
        )
    if gate["impact"]["failed"]:
        print(
            f"advisory impact gate failed: {gate['impact']['observed_count']} impacted "
            f"entities exceed the limit of {gate['impact']['requested']}",
            file=sys.stderr,
        )
    return 1 if gate["failed"] else 0


# -- pr-publish --------------------------------------------------------------


def _cmd_pr_publish(cfg, args) -> int:
    import os

    from ..cli.output import print_json
    from .pr_comment import DEFAULT_MARKER_KEY, marker_for
    from .pr_publish import publish_sticky_comment, urllib_transport, validate_comment_body

    body_path = Path(args.body_file)
    if not body_path.is_file():
        print(f"no such body file: {body_path}", file=sys.stderr)
        return 2
    if body_path.stat().st_size > MAX_BODY_FILE_BYTES:
        print(
            f"body file is larger than the {MAX_BODY_FILE_BYTES} byte cap; refusing to read it",
            file=sys.stderr,
        )
        return 2
    body = body_path.read_text(encoding="utf-8", errors="replace")
    marker = marker_for(args.marker or DEFAULT_MARKER_KEY)
    api_base = args.api_base or _default_api_base()

    if not args.allow_egress:
        # The air-gap default. Validation still runs, so a caller can check an
        # artifact locally without any possibility of an outbound call.
        validation = validate_comment_body(body, marker=marker)
        result = {
            "action": "not-attempted",
            "posted": False,
            "marker": marker,
            "validation": validation,
            "why": (
                "no outbound call was made. Posting a pull-request comment is an "
                "explicit opt-in egress: pass --allow-egress in continuous "
                "integration to enable it. The body was validated locally."
            ),
        }
        print_json(result) if args.as_json else print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if validation["valid"] else 1

    # --dry-run still performs the marker LOOKUP, which is an outbound read, so
    # a credential is required whenever egress is enabled at all. Sending a
    # placeholder token at a real API is not a dry run, it is a failed request.
    token = os.environ.get(args.token_env, "")
    if not token.strip():
        print(f"no token in ${args.token_env}; refusing to contact the API", file=sys.stderr)
        return 2

    print(
        f"warning: contacting {api_base} to publish a pull-request comment. This is the "
        "one opt-in outbound call in this tool and it was requested with --allow-egress.",
        file=sys.stderr,
    )
    result = publish_sticky_comment(
        transport=urllib_transport(timeout=args.timeout),
        repo=args.pr_repo,
        pr_number=args.pr_number,
        body=body,
        token=token,
        marker=marker,
        api_base=api_base,
        dry_run=args.dry_run,
    )
    print_json(result) if args.as_json else print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["action"] != "rejected" else 1


def _default_api_base() -> str:
    from .pr_publish import DEFAULT_API_BASE

    return DEFAULT_API_BASE
