"""CLI surface for configuring AI coding tools to talk to the read-only server.

This is the `mcp-install`, `mcp-uninstall`, `mcp-detect`, and `mcp-tools`
surface, kept out of the entry module so the installer owns its own parser and
handler. See `dkg.mcp.configure` for what is actually written, and
`dkg.mcp.platforms` for where.

The configuration root defaults to the user home because that is where these
tools keep their configuration, but it stays an explicit, overridable argument,
and the default is resolved here in the CLI layer rather than in the library.
Nothing under `dkg.mcp.configure`, `dkg.mcp.platforms`, `dkg.mcp.artifacts`, or
`dkg.mcp.rules` can reach a home directory on its own.
"""

from __future__ import annotations

import argparse
from pathlib import Path

COMMANDS = ("mcp-install", "mcp-uninstall", "mcp-tools", "mcp-detect")

_OS_CHOICES = ("current", "darwin", "linux", "win32")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config-root", default=None, help="configuration root (defaults to the user home)")
    p.add_argument(
        "--target-os",
        default="current",
        choices=_OS_CHOICES,
        help="resolve platform-specific configuration paths for this operating system",
    )


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "mcp-install",
        help="write the read-only MCP server entry, hooks, commands, and rules for an AI coding tool",
    )
    p.add_argument("tool", nargs="?", default=None, help="target tool (see 'dkg mcp-tools'); omit with --all")
    _add_common(p)
    p.add_argument("--all", action="store_true", help="configure every supported tool detected under the config root")
    p.add_argument(
        "--all-supported",
        action="store_true",
        help="with --all, configure every supported tool whether or not it was detected",
    )
    p.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")
    p.add_argument("--command", default=None, help="override the launch command instead of detecting it")
    p.add_argument("--no-hooks", action="store_true", help="do not write the tool's hook definitions")
    p.add_argument("--no-commands", action="store_true", help="do not write the tool's command or skill package")
    p.add_argument("--no-rules", action="store_true", help="do not inject the managed guidance block")
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing server entry that this project did not write (refused by default)",
    )

    p = sub.add_parser(
        "mcp-uninstall",
        help="remove the MCP server entry, hooks, commands, and rules this project wrote",
    )
    p.add_argument("tool", nargs="?", default=None, help="unbind this tool only; omit with --all")
    _add_common(p)
    p.add_argument("--all", action="store_true", help="unbind every supported tool under the config root")
    p.add_argument(
        "--all-repos",
        action="store_true",
        help="also unbind every repository in the watch registry, using each as a config root",
    )
    p.add_argument("--server-only", action="store_true", help="remove only the MCP server entry")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument(
        "--keep-data",
        dest="keep_data",
        action="store_true",
        default=True,
        help="keep the graph data (the default)",
    )
    scope.add_argument(
        "--purge-data",
        dest="keep_data",
        action="store_false",
        help="also delete the DKG home named by --home; refused unless it holds a graph database",
    )
    p.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")

    p = sub.add_parser("mcp-tools", help="list the AI coding tools mcp-install can configure")
    p.add_argument(
        "--target-os",
        default="current",
        choices=_OS_CHOICES,
        help="resolve platform-specific configuration paths for this operating system",
    )

    p = sub.add_parser("mcp-detect", help="report which supported AI coding tools are present")
    _add_common(p)


def dispatch(cfg, args) -> int | None:
    if args.cmd not in COMMANDS:
        return None
    if args.cmd == "mcp-tools":
        return _cmd_mcp_tools(cfg, args)
    if args.cmd == "mcp-detect":
        return _cmd_mcp_detect(cfg, args)
    if args.cmd == "mcp-install":
        return _cmd_mcp_install(cfg, args)
    return _cmd_mcp_uninstall(cfg, args)


def _platform_key(args) -> str | None:
    return None if args.target_os == "current" else args.target_os


def _config_root(args) -> Path:
    return Path(args.config_root) if args.config_root else Path.home()


def _cmd_mcp_tools(cfg, args) -> int:
    from ..cli.output import print_json, print_table
    from .configure import supported_tools

    tools = supported_tools(platform_key=_platform_key(args))
    if args.as_json:
        print_json({"tools": tools})
    else:
        print_table(
            ["name", "tool", "config file", "key", "hooks", "commands", "rules"],
            [
                [
                    t["name"],
                    t["display"],
                    t["relative_path"],
                    t["servers_key"],
                    "yes" if t["hooks_path"] else "no",
                    "yes" if t["commands_path"] else "no",
                    "yes" if t["rules_path"] else "no",
                ]
                for t in tools
            ],
        )
    return 0


def _cmd_mcp_detect(cfg, args) -> int:
    from ..cli.entry import _budgeted
    from ..cli.output import print_json
    from .configure import detect_installed

    root = _config_root(args)
    records = detect_installed(config_root=root, platform_key=_platform_key(args))
    payload = {
        "config_root": str(root),
        "present": sorted(r["name"] for r in records if r["present"]),
        "absent": sorted(r["name"] for r in records if not r["present"]),
        "tools": records,
    }
    print_json(_budgeted(args, payload))
    return 0


def _cmd_mcp_install(cfg, args) -> int:
    from ..cli.entry import _budgeted
    from ..cli.output import print_json
    from ..core.errors import ValidationError
    from .configure import install_all, install_bundle

    if bool(args.tool) == bool(args.all):
        raise ValidationError("name exactly one tool, or pass --all; see 'dkg mcp-tools'")

    root = _config_root(args)
    common = {
        "config_root": root,
        "dkg_home": cfg.home,
        "command": args.command,
        "platform_key": _platform_key(args),
        "with_hooks": not args.no_hooks,
        "with_commands": not args.no_commands,
        "with_rules": not args.no_rules,
        "force": args.force,
        "dry_run": args.dry_run,
    }
    if args.all:
        result = install_all(only_detected=not args.all_supported, **common)
    else:
        result = install_bundle(args.tool, **common)
    print_json(_budgeted(args, result))
    return 0


def _cmd_mcp_uninstall(cfg, args) -> int:
    from ..cli.entry import _budgeted
    from ..cli.output import print_json
    from ..core.errors import ValidationError
    from ..watch.registry import Registry
    from .configure import purge_data, uninstall, uninstall_all, uninstall_bundle

    if bool(args.tool) == bool(args.all):
        raise ValidationError("name exactly one tool, or pass --all; see 'dkg mcp-tools'")

    root = _config_root(args)
    key = _platform_key(args)
    roots = [root]
    registered: list[str] = []
    if args.all_repos:
        # Resolved here, in the CLI layer. The library is never handed a path it
        # could have derived itself from the environment.
        registered = sorted(entry.path for entry in Registry.in_home(cfg.home).list())
        roots.extend(Path(p) for p in registered)

    if args.all:
        result = uninstall_all(config_roots=roots, platform_key=key, dry_run=args.dry_run)
    else:
        remove = uninstall if args.server_only else uninstall_bundle
        per_root = [
            remove(args.tool, config_root=root, platform_key=key, dry_run=args.dry_run)
            for root in roots
        ]
        # The primary config root's result is the top level, so a plain
        # single-root uninstall keeps reporting removed, path and reason where
        # they have always been. Extra roots, if any, are alongside it.
        result = dict(per_root[0])
        result["config_roots"] = [str(root) for root in roots]
        result["results"] = per_root
    result["scope"] = {
        "all_tools": bool(args.all),
        "all_repos": bool(args.all_repos),
        "registered_repos": registered,
        "server_only": bool(args.server_only),
        "keep_data": bool(args.keep_data),
    }
    result["data"] = (
        {"kept": True, "dkg_home": str(cfg.home), "reason": "--keep-data is the default scope"}
        if args.keep_data
        else purge_data(dkg_home=cfg.home, dry_run=args.dry_run)
    )
    print_json(_budgeted(args, result))
    return 0
