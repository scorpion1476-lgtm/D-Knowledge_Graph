"""Ingest a source repository into the code graph, with incremental updates.

Full ingest parses every source file. Incremental ingest asks the working copy's
version control which files changed, re-parses those, and reconstructs the
unchanged files' symbols from the graph so cross-file references still resolve.
Git and Subversion working copies both go through that one path. Degrades
cleanly when the code extra is absent.

Three project-owned inputs shape what is collected, and each is reported in the
result rather than applied silently: the indexing ignore file, the opt-in
inclusion of git submodule contents, and the compiler configuration that maps
aliased import specifiers to real modules.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.db import Database
from ..core.errors import UnsupportedFormatError
from ..core.ids import content_id
from . import frameworks
from .aliases import load_compiler_config
from .capability import available_languages, tree_sitter_available
from .changes import detect_changes, is_git_repo, is_svn_checkout
from .config_keys import (
    CONFIG_EXTENSIONS,
    EDGE_CONFIGURES,
    MAX_CONFIG_BYTES,
    is_config_file,
    link_bindings,
    parse_config_file,
)
from .graph import write_code_graph
from .ignores import load_ignore_rules
from .languages import LanguageRegistry
from .model import ParsedFile, Reference, Symbol
from .parser import EXT_LANG, is_parsable, language_for, parse_source
from .postprocess import DEFAULT_LEVEL, run_postprocess

CODE_EXTS = set(EXT_LANG.keys())


def _source_uri(repo: Path) -> str:
    return f"code://{Path(repo).resolve()}"


def _stored_hashes(db: Database, source_uri: str, tenant_id: str) -> dict[str, str]:
    src_id = content_id("src", tenant_id, source_uri)
    rows = db.fetchall(
        "SELECT metadata_json FROM documents WHERE source_id=? AND format LIKE 'code:%';",
        (src_id,),
    )
    out: dict[str, str] = {}
    for r in rows:
        md = json.loads(r["metadata_json"] or "{}")
        if md.get("path") and md.get("file_sha256"):
            out[md["path"]] = md["file_sha256"]
    return out


def _reconstruct_symbols(db: Database, source_uri: str, tenant_id: str, exclude_paths: set[str]) -> list[ParsedFile]:
    """Rebuild symbol-only ParsedFiles for unchanged files from the graph.

    These provide the cross-file reference index during an incremental update
    without re-parsing or rewriting the unchanged files.
    """
    src_id = content_id("src", tenant_id, source_uri)
    docs = db.fetchall(
        "SELECT metadata_json FROM documents WHERE source_id=? AND format LIKE 'code:%';",
        (src_id,),
    )
    by_path: dict[str, ParsedFile] = {}
    for d in docs:
        md = json.loads(d["metadata_json"] or "{}")
        path = md.get("path")
        if not path or path in exclude_paths:
            continue
        pf = ParsedFile(
            path=path,
            language=md.get("language", ""),
            # Fidelity is restored so an incremental update keeps the confidence
            # discount a fallback-parsed file's edges carry.
            fidelity=md.get("fidelity", "grammar"),
        )
        # Rehydrate the persisted references so inbound cross-file edges into a
        # changed file are rebuilt without re-parsing the unchanged files.
        for ref in md.get("references", []):
            if isinstance(ref, list) and len(ref) == 3:
                pf.references.append(Reference(ref[0], ref[1], ref[2]))
        by_path[path] = pf
    if not by_path:
        return []
    ents = db.fetchall(
        "SELECT kind, canonical, display, metadata_json FROM entities WHERE tenant_id=? AND kind LIKE 'code:%';",
        (tenant_id,),
    )
    for e in ents:
        md = json.loads(e["metadata_json"] or "{}")
        path = md.get("path")
        if path in by_path:
            by_path[path].symbols.append(
                Symbol(
                    kind=e["kind"].split(":", 1)[1],
                    name=e["display"],
                    qualified=e["canonical"],
                    start_line=int(md.get("start_line", 0)),
                    end_line=int(md.get("end_line", 0)),
                    text="",
                )
            )
    return list(by_path.values())


def ingest_repo(
    db: Database,
    repo,
    *,
    tenant_id: str = "local",
    audit_path=None,
    incremental: bool = True,
    resolve: bool = False,
    languages: LanguageRegistry | None = None,
    include_submodules: bool = False,
    postprocess: str | None = DEFAULT_LEVEL,
) -> dict:
    repo = Path(repo)
    if not repo.exists():
        raise UnsupportedFormatError(f"repository path does not exist: {repo}")
    registry = languages if languages is not None else LanguageRegistry()
    custom_ready = bool(registry.available_names())
    if not tree_sitter_available() or (not available_languages() and not custom_ready):
        raise UnsupportedFormatError(
            "code ingestion requires the 'code' extra: pip install d-knowledge-graph[code]"
        )
    exts = CODE_EXTS | registry.extensions() | set(CONFIG_EXTENSIONS)
    source_uri = _source_uri(repo)

    # The project's own indexing exclusions, read once. Tracked and indexed are
    # different questions, so this is not .gitignore.
    ignore_rules = load_ignore_rules(repo)
    submodules: list[str] = []

    versioned = is_git_repo(repo) or is_svn_checkout(repo)
    if versioned:
        stored = _stored_hashes(db, source_uri, tenant_id) if incremental else {}
        diff = detect_changes(
            repo,
            stored,
            exts=exts,
            include_submodules=include_submodules,
            ignore_rules=ignore_rules,
        )
        to_parse = diff["changed"]
        removed = set(diff["removed"])
        vcs = diff["vcs"]
        submodules = diff["submodules"]
        mode = f"{vcs}-incremental" if stored else f"{vcs}-full"
        unchanged = diff["unchanged"]
    else:
        walked = [
            str(p.relative_to(repo))
            for p in sorted(repo.rglob("*"))
            if p.is_file()
            and (
                p.suffix.lower() in exts
                or is_config_file(p)
                or (not p.suffix and language_for(p) is not None)
            )
        ]
        to_parse, _dropped = (
            ignore_rules.filter(walked) if ignore_rules.present else (walked, [])
        )
        removed = set()
        stored = {}
        mode = "walk-full"
        unchanged = 0

    parsed: list[ParsedFile] = []
    texts: dict[str, str] = {}
    skipped: list[str] = []
    # Composer autoload rules are a property of the repository, not of one file,
    # so they are read once and applied to every PHP file parsed below.
    autoload = frameworks.load_composer_autoload(repo)
    for rel in to_parse:
        fpath = repo / rel
        # Externalised configuration is parsed into KEY-ONLY nodes before the
        # language check, because a .env or .properties file has no grammar and
        # would otherwise be skipped entirely. The value is discarded at the
        # point of reading and never reaches the graph.
        if is_config_file(rel):
            try:
                raw = fpath.read_bytes()
                if len(raw) <= MAX_CONFIG_BYTES:
                    parsed.append(parse_config_file(rel, raw.decode("utf-8", errors="replace")))
                    # Deliberately NOT added to `texts`: doing so would write the
                    # file's content, values and all, into the chunk table.
            except OSError as e:
                skipped.append(f"{rel}: {e}")
            continue
        # Resolved against the repository path, not the relative one: an
        # extension-less script is identified by its interpreter line, which
        # means the file has to be findable on disk.
        if language_for(fpath, registry) is None:
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            # A content-detected format that turns out not to be one (most YAML
            # is configuration, not Ansible) is passed over quietly rather than
            # reported as a parse failure it never was.
            if not is_parsable(fpath, text):
                continue
            pf = parse_source(rel, text, registry=registry)
            if pf.language == "php" and autoload.prefixes:
                frameworks.apply_autoload(pf, text, autoload)
            parsed.append(pf)
            texts[rel] = text
        except Exception as e:
            skipped.append(f"{rel}: {e}")

    index_only: list[ParsedFile] = []
    replace_paths: set[str] | None = None
    if mode.endswith("-incremental"):
        index_only = _reconstruct_symbols(db, source_uri, tenant_id, exclude_paths=set(to_parse) | removed)
        replace_paths = set(to_parse) | removed

    resolutions = None
    if resolve:
        from .resolve import resolve_all

        resolutions = resolve_all(parsed + index_only, texts)

    # The project's own compiler configuration, read once per repository. An
    # absent one is the common case and costs nothing.
    aliases = load_compiler_config(repo)

    # Configuration bindings: which code reads which key. Both endpoints are
    # known exactly, so they are passed as pre-resolved edges rather than
    # through name matching.
    config_edges = [
        (config_qualified, EDGE_CONFIGURES, code_qualified)
        for config_qualified, code_qualified in link_bindings(parsed + index_only, texts)
    ]

    result = write_code_graph(
        db,
        parsed + index_only,
        texts,
        source_uri=source_uri,
        tenant_id=tenant_id,
        audit_path=audit_path,
        replace_paths=replace_paths,
        resolutions=resolutions,
        aliases=aliases,
        extra_edges=config_edges,
    )
    result.update({
        "mode": mode, "parsed_files": len(parsed), "unchanged_files": unchanged,
        "removed_files": len(removed), "skipped": skipped,
        "resolved": bool(resolutions), "resolved_edges": len(resolutions or {}),
        "path_aliases": aliases.as_report(),
        # The effective exclusion set. An index that silently omitted files
        # would be indistinguishable from one that failed to find them.
        "ignored": ignore_rules.report(),
        "submodules": {
            "included": include_submodules,
            "paths": submodules,
            "why": (
                "off by default: including submodule contents changes the shape "
                "of an ingest, so it is asked for rather than inferred"
            ),
        },
    })
    # Post-processing is a separate named stage, not part of writing the graph.
    # Parsing costs what changed; the derived views cost the whole graph, so a
    # large ingest has to be able to reduce or skip them. The level actually
    # applied is reported, which is not always the level requested.
    result["postprocess"] = run_postprocess(db, level=postprocess, tenant_id=tenant_id)
    return result
