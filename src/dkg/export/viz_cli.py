"""CLI surface for the offline graph viewer.

The viewer file itself is written by `dkg export --format html`. This module
owns the commands that are about *viewing* rather than exporting, which today is
one command: `dkg viz-serve`, the bounded loopback server described in
`dkg.export.serve`.

The server is opt-in and never started by any other command. It binds a loopback
address only, refuses anything else before a socket exists, and serves exactly
the one generated file. See `dkg.export.serve` for every bound it enforces.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .serve import (
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_MAX_REQUESTS,
    DEFAULT_REQUEST_TIMEOUT,
    NonLoopbackBindError,
    ServerLimits,
    ViewerServer,
)

COMMANDS: tuple[str, ...] = ("viz-serve",)

DEFAULT_VIEWER_NAME = "viewer.html"


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "viz-serve",
        help="serve a generated offline viewer from a bounded, loopback-only local server",
    )
    p.add_argument(
        "--file",
        dest="viz_file",
        metavar="PATH",
        default=None,
        help=(
            "an already-generated viewer HTML file to serve; when omitted, one is generated "
            "from the current database into the DKG home"
        ),
    )
    p.add_argument(
        "--host",
        dest="viz_host",
        metavar="ADDRESS",
        default="127.0.0.1",
        help="loopback bind address; any non-loopback address is refused (default: 127.0.0.1)",
    )
    p.add_argument(
        "--port",
        dest="viz_port",
        metavar="PORT",
        type=int,
        required=True,
        help="the port to bind, always explicit; nothing is chosen for you",
    )
    p.add_argument(
        "--max-requests",
        dest="viz_max_requests",
        metavar="N",
        type=int,
        default=DEFAULT_MAX_REQUESTS,
        help=f"stop after serving this many requests (default: {DEFAULT_MAX_REQUESTS})",
    )
    p.add_argument(
        "--request-timeout",
        dest="viz_request_timeout",
        metavar="SECONDS",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help=f"per-connection socket timeout in seconds (default: {DEFAULT_REQUEST_TIMEOUT})",
    )
    p.add_argument(
        "--max-request-bytes",
        dest="viz_max_request_bytes",
        metavar="BYTES",
        type=int,
        default=DEFAULT_MAX_REQUEST_BYTES,
        help=f"reject a request larger than this many bytes (default: {DEFAULT_MAX_REQUEST_BYTES})",
    )
    p.add_argument(
        "--max-nodes",
        dest="viz_max_nodes",
        metavar="N",
        type=int,
        default=None,
        help="node cap when generating the viewer; ignored when --file is given",
    )


def dispatch(cfg, args) -> int | None:
    if args.cmd not in COMMANDS:
        return None
    return _cmd_viz_serve(cfg, args)


def _viewer_path(cfg, args) -> Path:
    if args.viz_file:
        path = Path(args.viz_file)
        if not path.is_file():
            raise FileNotFoundError(f"no viewer file at {path}")
        return path
    from ..core.db import open_database
    from .graphdata import DEFAULT_MAX_NODES
    from .viz import export_html

    out = Path(cfg.home) / DEFAULT_VIEWER_NAME
    max_nodes = args.viz_max_nodes if args.viz_max_nodes is not None else DEFAULT_MAX_NODES
    with open_database(cfg.db_path) as db:
        export_html(db, out, max_nodes=max_nodes)
    return out


def _cmd_viz_serve(cfg, args) -> int:
    from ..cli.output import print_json

    limits = ServerLimits(
        max_requests=args.viz_max_requests,
        request_timeout=args.viz_request_timeout,
        max_request_bytes=args.viz_max_request_bytes,
    )
    try:
        path = _viewer_path(cfg, args)
        server = ViewerServer(path, host=args.viz_host, port=args.viz_port, limits=limits)
    except (NonLoopbackBindError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    server.start()
    if not args.as_json:
        print(f"serving {path} at {server.url}")
        print(
            f"loopback only, at most {limits.max_requests} requests, "
            f"{limits.request_timeout:g}s per-connection timeout; press Ctrl-C to stop"
        )
    try:
        while server.running:
            server.wait(timeout=limits.poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        served = server.requests_served
        server.stop()

    if args.as_json:
        print_json(
            {
                "ok": True,
                "file": str(path),
                "url": server.url,
                "host": server.host,
                "port": server.port,
                "requests_served": served,
                "max_requests": limits.max_requests,
            }
        )
    else:
        print(f"stopped after {served} request(s)")
    return 0
