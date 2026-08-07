"""Framework-aware parsing for the source-code plane.

Structural parsing alone misses the edges a PHP framework project actually runs
on. A class is loaded by an autoload rule rather than by an import statement, a
controller is reached through a route table rather than through a call, a model
extends a base class that is only meaningful because of what the framework does
with it, and a view is named by a dotted string rather than by a path. This
module adds those edges on top of the ordinary parse, so blast radius and
execution flow over a framework project reach the files a structural parse
alone leaves unconnected.

What is added, all from files already on disk and with no network access:

- Composer PSR-4 autoload resolution. ``composer.json`` maps a namespace prefix
  to a directory, so a fully qualified class name resolves to the file that
  defines it. That turns a ``use App\\Models\\Post;`` statement into an import
  edge pointing at a real file instead of a bare name.
- Blade template references. A ``view('admin.users.index')`` call names
  ``resources/views/admin/users/index.blade.php``, and a template's own
  ``@extends`` and ``@include`` directives name further templates. Both become
  edges between the calling code and the template file.
- Route definitions. ``Route::get('/users', [UserController::class, 'index'])``
  and its closure and string forms become a route symbol and an edge from that
  route to the action it dispatches to.
- Model inheritance. A class extending the framework's Eloquent base is recorded
  as a model, and its relationship declarations (``hasMany``, ``belongsTo``, and
  the rest) become edges to the related model.

Every edge here is structural and over-approximate in the same way the rest of
the plane's edges are: a route registered by a variable, a view name built at
runtime, or a class resolved through a container binding is not seen. That is
stated in the output rather than left implicit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import ParsedFile, Reference, Symbol

# The base classes that mark a class as a framework model. Matching is on the
# last segment so both the bare and fully qualified forms are recognised.
MODEL_BASES = ("Model", "Authenticatable", "Pivot", "Eloquent")

# Relationship methods whose first argument names another model.
RELATION_METHODS = (
    "hasOne", "hasMany", "belongsTo", "belongsToMany", "hasOneThrough",
    "hasManyThrough", "morphTo", "morphOne", "morphMany", "morphToMany",
)

_ROUTE_VERBS = ("get", "post", "put", "patch", "delete", "options", "any", "match", "resource", "apiResource")

_ROUTE_CALL = re.compile(
    r"Route\s*::\s*(?P<verb>" + "|".join(_ROUTE_VERBS) + r")\s*\(\s*(?P<args>.*?)\)\s*(?:;|->)",
    re.DOTALL,
)
_ROUTE_URI = re.compile(r"""^\s*\[?\s*['"](?P<uri>[^'"]*)['"]""")
_ACTION_ARRAY = re.compile(r"""\[\s*(?P<class>[A-Za-z_\\][\w\\]*)\s*::\s*class\s*,\s*['"](?P<method>\w+)['"]\s*\]""")
_ACTION_STRING = re.compile(r"""['"](?P<class>[A-Za-z_][\w\\]*)@(?P<method>\w+)['"]""")
_ACTION_SINGLE = re.compile(r"""\[?\s*(?P<class>[A-Za-z_\\][\w\\]*)\s*::\s*class\s*\]?\s*\)?\s*$""")

# Blade comments and escaped directives, removed before any directive is read so
# a commented or escaped reference never becomes an edge.
_BLADE_COMMENT = re.compile(r"\{\{--.*?--\}\}", re.DOTALL)
_ESCAPED_DIRECTIVE = re.compile(r"@@\w+")
# A PHP comment, removed for the same reason before route and view calls are
# read out of PHP source.
_PHP_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_PHP_LINE_COMMENT = re.compile(r"(?<![:'\"])//[^\n]*|^[ \t]*#[^\n]*", re.MULTILINE)

_VIEW_CALL = re.compile(r"""\bview\s*\(\s*['"](?P<name>[\w./-]+)['"]""")
_VIEW_FACADE = re.compile(r"""\bView\s*::\s*make\s*\(\s*['"](?P<name>[\w./-]+)['"]""")
_BLADE_DIRECTIVE = re.compile(r"""@(?P<directive>extends|include|includeIf|component|each)\s*\(\s*['"](?P<name>[\w./-]+)['"]""")

_USE_STATEMENT = re.compile(r"^\s*use\s+(?P<fqcn>[A-Za-z_][\w\\]*)\s*(?:as\s+\w+)?\s*;", re.MULTILINE)
_NAMESPACE = re.compile(r"^\s*namespace\s+(?P<ns>[A-Za-z_][\w\\]*)\s*;", re.MULTILINE)
_CLASS_DECL = re.compile(r"^\s*(?:final\s+|abstract\s+)*class\s+(?P<name>\w+)(?:\s+extends\s+(?P<base>[\w\\]+))?", re.MULTILINE)
_RELATION_CALL = re.compile(
    r"""\$this\s*->\s*(?P<relation>""" + "|".join(RELATION_METHODS) + r""")\s*\(\s*(?P<target>[A-Za-z_\\][\w\\]*)\s*::\s*class"""
)

BLADE_SUFFIX = ".blade.php"
DEFAULT_VIEW_ROOTS = ("resources/views", "views", "resources/templates")


@dataclass
class ComposerAutoload:
    """PSR-4 namespace prefixes mapped to directories, read from composer.json."""

    # Longest prefix first, so App\Models\ wins over App\ for App\Models\Post.
    prefixes: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    # PSR-0 rules are recorded separately because their path rule differs.
    psr0: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    files: tuple[str, ...] = ()
    source: str = ""

    def resolve(self, fqcn: str) -> str | None:
        """The repository-relative file a fully qualified class name maps to."""
        name = fqcn.lstrip("\\")
        for prefix, dirs in self.prefixes:
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
            relative = rest.replace("\\", "/")
            if not relative:
                continue
            for directory in dirs:
                return f"{directory.rstrip('/')}/{relative}.php"
        for prefix, dirs in self.psr0:
            if prefix and not name.startswith(prefix):
                continue
            # PSR-0 turns underscores in the class name into directories too.
            head, _, tail = name.rpartition("\\")
            relative = f"{head.replace(chr(92), '/')}/{tail.replace('_', '/')}" if head else tail.replace("_", "/")
            for directory in dirs:
                return f"{directory.rstrip('/')}/{relative.lstrip('/')}.php"
        return None


def load_composer_autoload(repo: str | Path) -> ComposerAutoload:
    """Read the PSR-4 and PSR-0 autoload rules from a repository's composer.json."""
    repo = Path(repo)
    path = repo / "composer.json"
    if not path.exists():
        return ComposerAutoload()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A malformed composer.json means no autoload rules, not a crash. The
        # structural parse still stands on its own.
        return ComposerAutoload(source=str(path))
    prefixes: list[tuple[str, tuple[str, ...]]] = []
    psr0: list[tuple[str, tuple[str, ...]]] = []
    files: list[str] = []
    for section in ("autoload", "autoload-dev"):
        block = doc.get(section)
        if not isinstance(block, dict):
            continue
        for rule, target in (block.get("psr-4") or {}).items():
            dirs = tuple(target) if isinstance(target, list) else (str(target),)
            prefixes.append((str(rule), dirs))
        for rule, target in (block.get("psr-0") or {}).items():
            dirs = tuple(target) if isinstance(target, list) else (str(target),)
            psr0.append((str(rule), dirs))
        for entry in block.get("files") or []:
            files.append(str(entry))
    prefixes.sort(key=lambda item: len(item[0]), reverse=True)
    psr0.sort(key=lambda item: len(item[0]), reverse=True)
    return ComposerAutoload(prefixes=prefixes, psr0=psr0, files=tuple(files), source=str(path))


def view_path(name: str, roots: tuple[str, ...] = DEFAULT_VIEW_ROOTS) -> list[str]:
    """Candidate template paths for a dotted Blade view name."""
    relative = name.replace(".", "/").replace("//", "/")
    return [f"{root}/{relative}{BLADE_SUFFIX}" for root in roots]


def _last_segment(fqcn: str) -> str:
    return fqcn.rstrip("\\").split("\\")[-1]


def _action_of(args: str) -> tuple[str, str] | None:
    """The controller class and method a route's action argument names."""
    m = _ACTION_ARRAY.search(args)
    if m is not None:
        return _last_segment(m.group("class")), m.group("method")
    m = _ACTION_STRING.search(args)
    if m is not None:
        return _last_segment(m.group("class")), m.group("method")
    m = _ACTION_SINGLE.search(args)
    if m is not None:
        # A single-action controller is invoked through __invoke.
        return _last_segment(m.group("class")), "__invoke"
    return None


def strip_php_comments(text: str) -> str:
    """Blank out PHP comments, keeping every byte offset and line intact.

    Offsets are preserved by replacing each comment with spaces of the same
    length, so a line number computed against the stripped text is still the
    line number in the file.
    """
    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return _PHP_LINE_COMMENT.sub(blank, _PHP_BLOCK_COMMENT.sub(blank, text))


def strip_blade_comments(text: str) -> str:
    """Blank out Blade comments and escaped directives, preserving offsets."""
    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return _ESCAPED_DIRECTIVE.sub(blank, _BLADE_COMMENT.sub(blank, text))


def extract_routes(path: str | Path, text: str) -> ParsedFile:
    """Route definitions and the actions they dispatch to, from one PHP file."""
    path = str(path)
    text = strip_php_comments(text)
    pf = ParsedFile(path=path, language="php")
    module_q = path
    seen: set[str] = set()
    for match in _ROUTE_CALL.finditer(text):
        args = match.group("args")
        uri_match = _ROUTE_URI.match(args)
        if uri_match is None:
            continue
        verb = match.group("verb")
        uri = uri_match.group("uri") or "/"
        name = f"{verb.upper()} {uri}"
        q = f"{path}::route:{name}"
        if q in seen:
            continue
        seen.add(q)
        line = text.count("\n", 0, match.start()) + 1
        pf.symbols.append(
            Symbol("function", name, q, line, line, match.group(0).strip(), module_q)
        )
        action = _action_of(args)
        if action is not None:
            controller, method = action
            # routes_to, not calls: the framework dispatches to this handler
            # from a URL at runtime, which is a different fact from a call.
            pf.references.append(Reference(q, "routes_to", method))
            pf.references.append(Reference(q, "routes_to", controller))
    return pf


def extract_view_references(path: str | Path, text: str, owner: str | None = None) -> list[Reference]:
    """Blade templates named by PHP code or by another template's directives."""
    path = str(path)
    source = owner or path
    text = strip_blade_comments(strip_php_comments(text))
    refs: list[Reference] = []
    for pattern in (_VIEW_CALL, _VIEW_FACADE):
        for match in pattern.finditer(text):
            refs.append(Reference(source, "renders", _view_symbol(match.group("name"))))
    for match in _BLADE_DIRECTIVE.finditer(text):
        refs.append(Reference(source, "renders", _view_symbol(match.group("name"))))
    return refs


def _view_symbol(name: str) -> str:
    """The graph name for a Blade template, stable across the ways it is named."""
    return f"view:{name.replace('/', '.')}"


def extract_models(path: str | Path, text: str) -> tuple[list[Symbol], list[Reference]]:
    """Model classes and the relationships they declare, from one PHP file."""
    path = str(path)
    symbols: list[Symbol] = []
    refs: list[Reference] = []
    for match in _CLASS_DECL.finditer(text):
        base = match.group("base")
        if not base or _last_segment(base) not in MODEL_BASES:
            continue
        name = match.group("name")
        q = f"{path}::{name}"
        line = text.count("\n", 0, match.start()) + 1
        symbols.append(Symbol("type", f"model:{name}", f"{q}#model", line, line, match.group(0).strip(), q))
        body = text[match.end():]
        for rel in _RELATION_CALL.finditer(body):
            # relates_to, not calls: an Eloquent association declares a link
            # between two models rather than invoking anything.
            refs.append(Reference(q, "relates_to", _last_segment(rel.group("target"))))
    return symbols, refs


def resolve_use_imports(text: str, autoload: ComposerAutoload) -> list[tuple[str, str]]:
    """Each ``use`` statement paired with the file its class resolves to.

    A class the autoload rules do not cover, typically a vendor class, is
    reported with an empty path rather than guessed at.
    """
    out: list[tuple[str, str]] = []
    for match in _USE_STATEMENT.finditer(text):
        fqcn = match.group("fqcn")
        out.append((fqcn, autoload.resolve(fqcn) or ""))
    return out


def file_namespace(text: str) -> str:
    m = _NAMESPACE.search(text)
    return m.group("ns") if m else ""


def enrich_php_file(
    path: str | Path,
    text: str,
    parsed: ParsedFile,
    autoload: ComposerAutoload | None = None,
) -> ParsedFile:
    """Add the framework-aware symbols and edges to an ordinary PHP parse.

    The structural parse is not replaced. Everything added here sits alongside
    it, so a project that uses no framework is unaffected.
    """
    path = str(path)
    autoload = autoload if autoload is not None else ComposerAutoload()
    # Comments are blanked out, not removed, so every line number below still
    # matches the file. A commented-out route or view never becomes an edge.
    text = strip_php_comments(text)
    routes = extract_routes(path, text)
    parsed.symbols.extend(routes.symbols)
    parsed.references.extend(routes.references)

    model_symbols, model_refs = extract_models(path, text)
    parsed.symbols.extend(model_symbols)
    parsed.references.extend(model_refs)

    # Attribute a view reference to the enclosing method where one can be found
    # by line, so the edge starts at the code that renders rather than at the file.
    line_owner = _line_owner_index(parsed)
    for pattern in (_VIEW_CALL, _VIEW_FACADE):
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            owner = line_owner(line) or path
            parsed.references.append(Reference(owner, "renders", _view_symbol(match.group("name"))))

    for fqcn, target in resolve_use_imports(text, autoload):
        if target:
            # A resolved autoload target is a stronger, file-level import than
            # the bare class name the structural parse already recorded.
            parsed.references.append(Reference(path, "imports", Path(target).stem))
        del fqcn
    _dedupe(parsed)
    return parsed


def apply_autoload(parsed: ParsedFile, text: str, autoload: ComposerAutoload) -> ParsedFile:
    """Add the import edges that Composer's autoload rules make resolvable.

    Kept separate from enrich_php_file because it needs the repository root that
    holds composer.json, which parsing a single file does not have.
    """
    for _fqcn, target in resolve_use_imports(text, autoload):
        if target:
            parsed.references.append(Reference(parsed.path, "imports", Path(target).stem))
    _dedupe(parsed)
    return parsed


def enrich_blade_file(path: str | Path, text: str) -> ParsedFile:
    """A Blade template as a graph node plus the templates it pulls in."""
    path = str(path)
    name = blade_view_name(path)
    module_q = path
    text = strip_blade_comments(text)
    pf = ParsedFile(path=path, language="blade")
    pf.symbols.append(
        Symbol("module", Path(path).name, module_q, 1, max(text.count("\n") + 1, 1), "", None)
    )
    pf.symbols.append(
        Symbol("type", _view_symbol(name), f"{path}::{_view_symbol(name)}", 1, 1, "", module_q)
    )
    for match in _BLADE_DIRECTIVE.finditer(text):
        pf.references.append(
            Reference(f"{path}::{_view_symbol(name)}", "renders", _view_symbol(match.group("name")))
        )
    _dedupe(pf)
    return pf


def blade_view_name(path: str | Path, roots: tuple[str, ...] = DEFAULT_VIEW_ROOTS) -> str:
    """The dotted view name a Blade template file is addressed by."""
    text = str(path).replace("\\", "/")
    if text.endswith(BLADE_SUFFIX):
        text = text[: -len(BLADE_SUFFIX)]
    for root in roots:
        marker = f"{root}/"
        index = text.find(marker)
        if index != -1:
            text = text[index + len(marker):]
            break
    return text.strip("/").replace("/", ".")


def is_blade_template(path: str | Path) -> bool:
    return str(path).endswith(BLADE_SUFFIX)


def _line_owner_index(parsed: ParsedFile):
    """A lookup from a source line to the innermost symbol containing it."""
    spans = [
        (s.start_line, s.end_line, s.qualified)
        for s in parsed.symbols
        if s.kind in ("function", "method", "test") and s.end_line >= s.start_line
    ]
    spans.sort(key=lambda item: (item[1] - item[0], item[0]))

    def lookup(line: int) -> str | None:
        for start, end, qualified in spans:
            if start <= line <= end:
                return qualified
        return None

    return lookup


def _dedupe(pf: ParsedFile) -> None:
    seen_q: set[str] = set()
    kept: list[Symbol] = []
    for s in pf.symbols:
        if s.qualified in seen_q:
            continue
        seen_q.add(s.qualified)
        kept.append(s)
    pf.symbols = kept
    seen: set[tuple[str, str, str]] = set()
    unique: list[Reference] = []
    for r in pf.references:
        key = (r.from_qualified, r.kind, r.name)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    pf.references = unique
