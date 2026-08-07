"""Q-14: resolve aliased imports through a compiler configuration file."""

from __future__ import annotations

import pytest

from dkg.code.aliases import (
    AliasConfig,
    load_compiler_config,
    resolve_specifier,
    strip_jsonc,
)

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import resolve_edges
    from dkg.code.parser import parse_source


TSCONFIG = """{
  // The project's own module namespace.
  "compilerOptions": {
    "baseUrl": "src",
    "paths": {
      "@app/*": ["app/*"],
      "@lib": ["lib/index.ts"],
    }
  }
}
"""


# -- the tolerant reader -----------------------------------------------------


def test_strip_jsonc_removes_comments_and_trailing_commas():
    cleaned = strip_jsonc('{"a": 1, /* b */ "c": 2, // tail\n "d": [3,],\n}')

    import json

    assert json.loads(cleaned) == {"a": 1, "c": 2, "d": [3]}


def test_strip_jsonc_does_not_cut_a_string_containing_a_comment_marker():
    cleaned = strip_jsonc('{"url": "https://example.invalid/x", "n": 1}')

    import json

    assert json.loads(cleaned) == {"url": "https://example.invalid/x", "n": 1}


def test_an_absent_configuration_is_not_an_error(tmp_path):
    config = load_compiler_config(tmp_path)

    assert config.present is False
    assert config.error == ""
    assert config.as_report()["config_path"] is None


def test_an_unparseable_configuration_is_reported_not_raised(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{ this is not json", encoding="utf-8")

    config = load_compiler_config(tmp_path)

    assert config.present is False
    assert "not valid JSON" in config.error


def test_a_configuration_over_the_byte_cap_is_reported(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}" + " " * 2_000_000, encoding="utf-8")

    config = load_compiler_config(tmp_path)

    assert config.present is False
    assert "cap" in config.error


def test_configuration_is_read_with_its_base_url_and_aliases(tmp_path):
    (tmp_path / "tsconfig.json").write_text(TSCONFIG, encoding="utf-8")

    config = load_compiler_config(tmp_path)

    assert config.present is True
    assert config.base_url == "src"
    assert config.paths["@app/*"] == ("app/*",)
    assert config.paths["@lib"] == ("lib/index.ts",)
    assert config.as_report()["alias_count"] == 2


def test_jsconfig_is_used_when_there_is_no_tsconfig(tmp_path):
    (tmp_path / "jsconfig.json").write_text(
        '{"compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["source/*"]}}}',
        encoding="utf-8",
    )

    config = load_compiler_config(tmp_path)

    assert config.config_path == "jsconfig.json"
    assert config.paths["~/*"] == ("source/*",)


# -- resolution --------------------------------------------------------------


def test_a_wildcard_alias_resolves_to_a_real_module():
    config = AliasConfig(config_path="tsconfig.json", base_url="src", paths={"@app/*": ("app/*",)})
    modules = {"src/app/utils.ts", "src/app/other.ts"}

    assert resolve_specifier("@app/utils", config, modules) == "src/app/utils.ts"


def test_an_alias_pointing_at_no_real_module_resolves_to_nothing():
    """A mapping that points nowhere is not a resolution."""
    config = AliasConfig(config_path="tsconfig.json", base_url="src", paths={"@app/*": ("app/*",)})

    assert resolve_specifier("@app/missing", config, {"src/app/utils.ts"}) is None


def test_a_directory_alias_resolves_through_an_index_file():
    config = AliasConfig(config_path="tsconfig.json", base_url="src", paths={"@app/*": ("app/*",)})
    modules = {"src/app/widgets/index.ts"}

    assert resolve_specifier("@app/widgets", config, modules) == "src/app/widgets/index.ts"


def test_base_url_alone_resolves_a_bare_specifier():
    config = AliasConfig(config_path="tsconfig.json", base_url="src", paths={})

    assert resolve_specifier("lib/thing", config, {"src/lib/thing.ts"}) == "src/lib/thing.ts"


def test_the_longest_literal_prefix_wins_between_overlapping_patterns():
    config = AliasConfig(
        config_path="tsconfig.json",
        base_url="",
        paths={"@app/*": ("generic/*",), "@app/core/*": ("core/*",)},
    )
    modules = {"generic/core/thing.ts", "core/thing.ts"}

    assert resolve_specifier("@app/core/thing", config, modules) == "core/thing.ts"


def test_an_exact_pattern_beats_a_wildcard_one():
    config = AliasConfig(
        config_path="tsconfig.json",
        base_url="",
        paths={"@lib/*": ("wild/*",), "@lib": ("exact/index.ts",)},
    )

    assert resolve_specifier("@lib", config, {"exact/index.ts", "wild.ts"}) == "exact/index.ts"


def test_an_absent_configuration_resolves_nothing():
    assert resolve_specifier("@app/utils", AliasConfig(), {"src/app/utils.ts"}) is None


# -- integration with edge resolution ----------------------------------------


@requires_ts
def test_an_aliased_import_becomes_one_resolved_edge(tmp_path):
    """Before and after the configuration, so the edge depends on it."""
    files = {
        "src/app/utils.ts": "export function helper(): number { return 1; }\n",
        "src/main.ts": 'import { helper } from "@app/utils";\n\nexport function run() { return helper(); }\n',
    }
    parsed = [parse_source(rel, text) for rel, text in files.items()]

    without = resolve_edges(parsed, None, None)
    aliased = [
        e for e in without if e.predicate == "imports" and e.to_qualified == "src/app/utils.ts"
    ]
    assert aliased == [], "with no configuration the specifier cannot resolve"

    config = AliasConfig(
        config_path="tsconfig.json", base_url="src", paths={"@app/*": ("app/*",)}
    )
    with_config = resolve_edges(parsed, None, config)
    resolved = [
        e for e in with_config if e.predicate == "imports" and e.to_qualified == "src/app/utils.ts"
    ]

    assert len(resolved) == 1, [(e.from_qualified, e.to_qualified) for e in with_config]
    assert resolved[0].confidence == 0.9


@requires_ts
def test_an_unaliased_import_still_resolves_by_stem(tmp_path):
    """The configuration must add resolution, not replace it."""
    files = {
        "src/app/utils.ts": "export function helper(): number { return 1; }\n",
        "src/main.ts": 'import { helper } from "utils";\n\nexport function run() { return helper(); }\n',
    }
    parsed = [parse_source(rel, text) for rel, text in files.items()]
    config = AliasConfig(
        config_path="tsconfig.json", base_url="src", paths={"@app/*": ("app/*",)}
    )

    edges = resolve_edges(parsed, None, config)

    assert any(e.predicate == "imports" for e in edges)


@requires_ts
def test_ingest_reports_the_configuration_it_read(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "tsconfig.json").write_text(TSCONFIG, encoding="utf-8")
    (tmp_path / "src" / "app" / "utils.ts").write_text(
        "export function helper(): number { return 1; }\n", encoding="utf-8"
    )
    (tmp_path / "src" / "main.ts").write_text(
        'import { helper } from "@app/utils";\n\nexport function run() { return helper(); }\n',
        encoding="utf-8",
    )

    result = ingest_repo(db, tmp_path)

    report = result["path_aliases"]
    assert report["config_path"] == "tsconfig.json"
    assert report["base_url"] == "src"
    assert report["alias_count"] == 2
    assert report["error"] is None

    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM relationships r "
        "JOIN entities e ON e.entity_id = r.object_id "
        "WHERE r.predicate='code:imports' AND e.canonical='src/app/utils.ts';"
    )
    assert row["n"] >= 1, "the aliased import must have produced an edge into the real module"


@requires_ts
def test_ingest_without_a_configuration_reports_none(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    result = ingest_repo(db, tmp_path)

    assert result["path_aliases"]["config_path"] is None
    assert result["path_aliases"]["alias_count"] == 0
