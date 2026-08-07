"""Per-surface CLI extension points.

Every subcommand still has exactly one parser and exactly one handler. This
module only moves *where* a surface registers itself, so that independently
developed surfaces do not all have to edit the single entry module. Each
extension module exposes two functions and nothing else:

    register(sub)          add its subparsers to the shared subparser action
    dispatch(cfg, args)    return an exit code for its own commands, else None

Returning None is what keeps the contract honest: a module that does not
recognise the command must say so rather than guess, and `dispatch` below stops
at the first module that claims it. An unclaimed command falls through to the
entry module's own chain and is reported as unknown there, exactly as before.

The module list is explicit and the imports are not wrapped in a try block. A
swallowed ImportError here would silently remove a whole command surface and
leave the CLI reporting "unknown command" for a subcommand the build ships,
which is the kind of quiet gate this project does not allow.
"""

from __future__ import annotations

import argparse
import importlib
from types import ModuleType

# Order fixes the order subcommands appear in `dkg help`, so it is stable rather
# than incidental. Adding a surface means adding one line here.
EXTENSION_MODULES: tuple[str, ...] = (
    "dkg.mcp.install_cli",
    "dkg.code.pr_cli",
    "dkg.export.viz_cli",
)


def _loaded() -> list[ModuleType]:
    return [importlib.import_module(name) for name in EXTENSION_MODULES]


def register(sub: argparse._SubParsersAction) -> None:
    for module in _loaded():
        module.register(sub)


def dispatch(cfg, args) -> int | None:
    for module in _loaded():
        code: int | None = module.dispatch(cfg, args)
        if code is not None:
            return int(code)
    return None
