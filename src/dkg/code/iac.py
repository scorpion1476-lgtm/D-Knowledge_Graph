"""Infrastructure-as-code parsing for the source-code plane.

Two formats are handled, both through a real grammar and both emitting the same
Symbol and Reference shapes the rest of the plane emits, so blast radius,
execution flow, and search work over infrastructure the same way they work over
application code.

- Terraform and generic HCL (``.tf``, ``.tfvars``, ``.hcl``) through
  tree-sitter-hcl (Apache-2.0). Every block becomes a symbol: a resource or data
  source under its full ``type.name`` address, a module under its label, and
  variables, outputs, providers, and locals under theirs. A module's ``source``
  becomes an import, and an address referenced from inside another block becomes
  a dependency reference, which is what makes ``dkg impact`` meaningful over a
  Terraform root module.
- Ansible playbooks and role task files (``.yml``, ``.yaml``) through
  tree-sitter-yaml (MIT). A play becomes a class, each task becomes a method of
  its play, the module a task invokes becomes a call, and ``roles``,
  ``include_tasks``, and ``import_tasks`` become imports.

Ansible is detected by content, not by extension: most YAML in a repository is
not Ansible, and claiming every ``.yml`` file is a playbook would fill the graph
with symbols that do not exist. A YAML file that is not recognisably Ansible is
reported as having no code parser rather than parsed into invented symbols.

Terraform address references are read from the parsed expression text rather
than from a resolved reference graph, so they are structural and
over-approximate in the same way the rest of the plane's structural edges are.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.errors import IngestError, UnsupportedFormatError
from .capability import get_language, tree_sitter_available
from .model import ParsedFile, Reference, Symbol

HCL_EXTENSIONS = (".tf", ".tfvars", ".hcl", ".nomad")
ANSIBLE_EXTENSIONS = (".yml", ".yaml")

# Terraform addresses a block by a scheme that depends on the block type, and
# an expression elsewhere in the configuration refers to it by that address. A
# symbol is named by its address so that a reference resolves to it: without
# this, `var.cidr` would never match a block named `cidr`.
#   resource "aws_s3_bucket" "data"  ->  aws_s3_bucket.data
#   data "aws_ami" "base"            ->  data.aws_ami.base
#   variable "cidr"                  ->  var.cidr
#   module "vpc"                     ->  module.vpc
#   output "arn"                     ->  output.arn
#   provider "aws"                   ->  provider.aws
_ADDRESS_PREFIX = {
    "data": "data",
    "variable": "var",
    "module": "module",
    "output": "output",
    "provider": "provider",
    "locals": "local",
    "terraform": "terraform",
}
# A resource block takes both labels and no prefix, which is Terraform's own
# rule and the reason resource addresses read as `type.name`.
_ADDRESSED_BLOCKS = ("resource", "data")
# Block types that become a container symbol rather than a leaf.
_CONTAINER_BLOCKS = ("module",)

# A Terraform address as it appears inside an expression: at least two dotted
# segments, the first of which is a type, resource type, or a well-known scope.
_ADDRESS = re.compile(r"\b([a-z][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_-]*)(?:\.[A-Za-z_][A-Za-z0-9_-]*)*")
# Words that begin a dotted expression but are not a dependency on another block.
_NOT_ADDRESSES = {"each", "count", "self", "path", "terraform", "local"}

_MAX_BYTES = 4 * 1024 * 1024

# Keys that describe a task rather than name the module it runs.
_TASK_KEYWORDS = {
    "name", "when", "with_items", "with_dict", "loop", "loop_control", "register",
    "become", "become_user", "become_method", "tags", "vars", "notify", "ignore_errors",
    "changed_when", "failed_when", "delegate_to", "run_once", "no_log", "until",
    "retries", "delay", "environment", "args", "check_mode", "block", "rescue",
    "always", "any_errors_fatal", "throttle", "connection", "remote_user", "async",
    "poll", "listen", "module_defaults", "collections", "debugger", "timeout",
}
# Keys whose value names another file or role brought into this one.
_INCLUDE_KEYS = {
    "include_tasks", "import_tasks", "include", "include_role", "import_role",
    "import_playbook", "include_vars",
}
_PLAY_KEYS = {"hosts", "tasks", "roles", "pre_tasks", "post_tasks", "handlers"}
_TASK_LIST_KEYS = ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always")


def is_hcl(path: str | Path) -> bool:
    return Path(path).suffix.lower() in HCL_EXTENSIONS


def _read(path: str, text: str | None) -> str:
    if text is not None:
        return text
    raw = Path(path).read_bytes()
    if len(raw) > _MAX_BYTES:
        raise IngestError(f"infrastructure file too large: {len(raw)} bytes")
    return raw.decode("utf-8", "replace")


def _require_tree_sitter() -> Any:
    if not tree_sitter_available():
        raise UnsupportedFormatError(
            "infrastructure-as-code parsing requires the 'code' extra: pip install d-knowledge-graph[code]"
        )
    import tree_sitter

    return tree_sitter


def _node_text(node: Any, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


# -- Terraform and generic HCL ----------------------------------------------


def parse_hcl(path: str | Path, text: str | None = None) -> ParsedFile:
    """Extract blocks and their dependencies from a Terraform or HCL file."""
    path = str(path)
    ts = _require_tree_sitter()
    src = _read(path, text).encode("utf-8")
    parser = ts.Parser(get_language("hcl"))
    root = parser.parse(src).root_node

    pf = ParsedFile(path=path, language="hcl")
    module_q = path
    pf.symbols.append(Symbol("module", Path(path).name, module_q, 1, root.end_point[0] + 1, "", None))
    emitted: set[str] = {module_q}

    def block_labels(node: Any) -> tuple[str, list[str]]:
        kind = ""
        labels: list[str] = []
        for child in node.children:
            if child.type == "identifier" and not kind:
                kind = _node_text(child, src)
            elif child.type in ("string_lit", "quoted_template"):
                labels.append(_node_text(child, src).strip('"'))
            elif child.type == "identifier" and kind:
                labels.append(_node_text(child, src))
        return kind, labels

    def walk(node: Any, parent_q: str, top_level: bool) -> None:
        for child in node.children:
            if child.type != "block":
                walk(child, parent_q, top_level)
                continue
            if not top_level:
                # A nested block (a backend, a dynamic block, a lifecycle rule,
                # a content wrapper) configures the block around it. It has no
                # Terraform address, so emitting it as a symbol would put a node
                # in the graph that nothing can ever refer to.
                walk(child, parent_q, False)
                continue
            kind, labels = block_labels(child)
            if not kind:
                walk(child, parent_q, top_level)
                continue
            name = _block_address(kind, labels)
            symbol_kind = "class" if kind in _CONTAINER_BLOCKS else "type"
            q = f"{path}::{name}"
            if q in emitted:
                # A repeated address is a duplicate definition in the source; the
                # first one wins so the graph holds one node per address.
                walk(child, q, False)
                continue
            pf.symbols.append(
                Symbol(
                    symbol_kind,
                    name,
                    q,
                    child.start_point[0] + 1,
                    child.end_point[0] + 1,
                    _node_text(child, src),
                    module_q,
                )
            )
            emitted.add(q)
            body = child.child_by_field_name("body") or next((c for c in child.children if c.type == "body"), None)
            if body is not None:
                _hcl_body_references(pf, body, src, q, kind)
            walk(child, q, False)

    walk(root, module_q, True)
    _dedupe(pf)
    return pf


def _block_address(kind: str, labels: list[str]) -> str:
    """The Terraform address a block is referred to by elsewhere."""
    if kind == "resource" and len(labels) >= 2:
        return f"{labels[0]}.{labels[1]}"
    if kind in _ADDRESSED_BLOCKS and len(labels) >= 2:
        return f"{_ADDRESS_PREFIX.get(kind, kind)}.{labels[0]}.{labels[1]}"
    prefix = _ADDRESS_PREFIX.get(kind)
    if prefix and labels:
        return f"{prefix}.{labels[0]}"
    if labels:
        return f"{kind}.{labels[0]}"
    return kind


def _hcl_body_references(pf: ParsedFile, body: Any, src: bytes, owner: str, block_kind: str) -> None:
    """Record a module source as an import and every address as a dependency."""
    for attr in _descendants(body, "attribute"):
        name_node = next((c for c in attr.children if c.type == "identifier"), None)
        key = _node_text(name_node, src) if name_node is not None else ""
        value = _node_text(attr, src).partition("=")[2].strip()
        if block_kind == "module" and key == "source":
            pf.references.append(Reference(owner, "imports", value.strip('"').rstrip("/").split("/")[-1]))
            continue
        for match in _ADDRESS.finditer(value):
            head, tail = match.group(1), match.group(2)
            if head in _NOT_ADDRESSES:
                continue
            pf.references.append(Reference(owner, "calls", f"{head}.{tail}"))


def _descendants(node: Any, node_type: str) -> list[Any]:
    out: list[Any] = []
    queue = list(node.children)
    while queue:
        n = queue.pop(0)
        if n.type == node_type:
            out.append(n)
        queue.extend(n.children)
    return out


# -- Ansible ------------------------------------------------------------------


def _yaml_root(path: str, src: bytes) -> Any:
    ts = _require_tree_sitter()
    parser = ts.Parser(get_language("yaml"))
    return parser.parse(src).root_node


def _scalar(node: Any, src: bytes) -> str:
    """The text of a scalar value, with sequence and quoting punctuation removed.

    A block sequence item carries its own leading dash, so reading the item node
    directly would return "- common" where the value is "common".
    """
    if node is not None and node.type in ("block_sequence_item", "block_node", "flow_node"):
        inner = _first_of(node, ("plain_scalar", "single_quote_scalar", "double_quote_scalar"))
        if inner is not None:
            node = inner
    return _node_text(node, src).strip().strip("\"'").lstrip("- ").strip()


def _mapping_pairs(node: Any, src: bytes) -> list[tuple[str, Any]]:
    """Key and value node of every pair in the block mapping under `node`."""
    mapping = _first_of(node, ("block_mapping", "flow_mapping"))
    if mapping is None:
        return []
    pairs: list[tuple[str, Any]] = []
    for child in mapping.children:
        if child.type not in ("block_mapping_pair", "flow_pair"):
            continue
        key_node = child.child_by_field_name("key")
        value_node = child.child_by_field_name("value")
        if key_node is None:
            continue
        pairs.append((_scalar(key_node, src), value_node))
    return pairs


def _sequence_items(node: Any, src: bytes) -> list[Any]:
    del src
    sequence = _first_of(node, ("block_sequence", "flow_sequence"))
    if sequence is None:
        return []
    return [c for c in sequence.children if c.type in ("block_sequence_item", "flow_node")]


def _first_of(node: Any, types: tuple[str, ...]) -> Any | None:
    """First descendant of one of `types`, searched breadth first."""
    if node is None:
        return None
    if node.type in types:
        return node
    queue = list(node.children)
    while queue:
        n = queue.pop(0)
        if n.type in types:
            return n
        queue.extend(n.children)
    return None


def looks_like_ansible(path: str | Path, text: str | None = None) -> bool:
    """True when a YAML file is recognisably an Ansible playbook or task file."""
    if Path(path).suffix.lower() not in ANSIBLE_EXTENSIONS:
        return False
    try:
        body = _read(str(path), text)
    except (OSError, IngestError):
        return False
    # Cheap textual screen first so a large unrelated YAML file is not parsed.
    if not re.search(r"^\s*-\s+(name|hosts|include_tasks|import_tasks|block)\s*:", body, re.MULTILINE):
        return False
    if not tree_sitter_available():
        return False
    try:
        root = _yaml_root(str(path), body.encode("utf-8"))
    except Exception:  # noqa: BLE001 - a YAML file we cannot parse is not Ansible
        return False
    for item in _sequence_items(root, body.encode("utf-8")):
        keys = {k for k, _ in _mapping_pairs(item, body.encode("utf-8"))}
        if keys & _PLAY_KEYS:
            return True
        # A role task file is a bare list of tasks: named entries whose other key
        # is a module rather than a play keyword.
        if "name" in keys and (keys - _TASK_KEYWORDS):
            return True
        if keys & _INCLUDE_KEYS:
            return True
    return False


def parse_ansible(path: str | Path, text: str | None = None) -> ParsedFile:
    """Extract plays, tasks, module invocations, and includes from Ansible YAML."""
    path = str(path)
    body = _read(path, text)
    src = body.encode("utf-8")
    root = _yaml_root(path, src)

    pf = ParsedFile(path=path, language="ansible")
    module_q = path
    pf.symbols.append(Symbol("module", Path(path).name, module_q, 1, root.end_point[0] + 1, "", None))
    emitted: set[str] = {module_q}
    counter = {"play": 0, "task": 0}

    def unique(base: str, bucket: str) -> str:
        """A stable name for an entry, so two tasks can share a name."""
        counter[bucket] += 1
        name = base or f"{bucket}{counter[bucket]}"
        candidate, suffix = name, 2
        while f"{path}::{candidate}" in emitted or any(
            s.qualified.endswith(f".{candidate}") for s in pf.symbols
        ):
            candidate = f"{name}#{suffix}"
            suffix += 1
        return candidate

    def add_task(item: Any, owner: str) -> None:
        pairs = _mapping_pairs(item, src)
        if not pairs:
            return
        keys = {k for k, _ in pairs}
        name = next((_scalar(v, src) for k, v in pairs if k == "name" and v is not None), "")
        task_name = unique(name.replace(" ", "_") or "", "task")
        q = f"{owner}.{task_name}" if owner != module_q else f"{path}::{task_name}"
        pf.symbols.append(
            Symbol(
                "method" if owner != module_q else "function",
                task_name,
                q,
                item.start_point[0] + 1,
                item.end_point[0] + 1,
                _node_text(item, src),
                owner,
            )
        )
        emitted.add(q)
        for key, value in pairs:
            if key in _INCLUDE_KEYS:
                target = _scalar(value, src) if value is not None else ""
                if target:
                    pf.references.append(Reference(q, "imports", Path(target).name))
                continue
            if key in _TASK_KEYWORDS:
                continue
            # The one key that is not a task keyword names the module the task
            # runs, which is the call this task makes.
            pf.references.append(Reference(q, "calls", key.split(".")[-1]))
        # A block/rescue/always task nests further tasks under itself.
        for key, value in pairs:
            if key in ("block", "rescue", "always") and value is not None:
                for nested in _sequence_items(value, src):
                    add_task(nested, q)
        del keys

    def add_play(item: Any) -> None:
        pairs = _mapping_pairs(item, src)
        keys = {k for k, _ in pairs}
        if not (keys & _PLAY_KEYS):
            add_task(item, module_q)
            return
        name = next((_scalar(v, src) for k, v in pairs if k == "name" and v is not None), "")
        if not name:
            name = next((_scalar(v, src) for k, v in pairs if k == "hosts" and v is not None), "")
        play_name = unique(name.replace(" ", "_") or "", "play")
        q = f"{path}::{play_name}"
        pf.symbols.append(
            Symbol("class", play_name, q, item.start_point[0] + 1, item.end_point[0] + 1, _node_text(item, src), module_q)
        )
        emitted.add(q)
        for key, value in pairs:
            if value is None:
                continue
            if key in _TASK_LIST_KEYS:
                for task in _sequence_items(value, src):
                    add_task(task, q)
            elif key == "roles":
                for role in _sequence_items(value, src):
                    role_pairs = _mapping_pairs(role, src)
                    role_name = next(
                        (_scalar(v, src) for k, v in role_pairs if k in ("role", "name") and v is not None),
                        _scalar(role, src),
                    )
                    if role_name:
                        pf.references.append(Reference(q, "imports", role_name))
            elif key in _INCLUDE_KEYS:
                target = _scalar(value, src)
                if target:
                    pf.references.append(Reference(q, "imports", Path(target).name))

    items = _sequence_items(root, src)
    if not items:
        raise UnsupportedFormatError(f"{path}: not an Ansible playbook or task file")
    for item in items:
        add_play(item)
    _dedupe(pf)
    return pf


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
    unique_refs: list[Reference] = []
    for r in pf.references:
        key = (r.from_qualified, r.kind, r.name)
        if key not in seen:
            seen.add(key)
            unique_refs.append(r)
    pf.references = unique_refs
