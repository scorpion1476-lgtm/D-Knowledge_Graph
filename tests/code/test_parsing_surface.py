"""Interpreter-line detection, fallback confidence, and the config surface.

Three separate claims are checked here, each of which is easy to assert and hard
to notice when it quietly stops being true: an extension-less script is still
parsed, a symbol found without a grammar is marked as such all the way into the
graph, and the custom-language config validates every section it accepts.
"""

from __future__ import annotations

import json

import pytest

from dkg.code.capability import grammar_available
from dkg.code.fallback import FALLBACK_SPECS
from dkg.code.graph import resolve_edges, write_code_graph
from dkg.code.languages import parse_config
from dkg.code.model import CONF_RESOLVED, FALLBACK_CONFIDENCE_FACTOR, FIDELITY_FALLBACK
from dkg.code.parser import language_for, language_from_shebang, parse_source
from dkg.core.db import open_database
from dkg.core.errors import ValidationError

needs_python = pytest.mark.skipif(
    not grammar_available("python"), reason="the python grammar is not installed"
)


# -- interpreter-line detection -------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("#!/usr/bin/env python3", "python"),
        ("#!/usr/bin/python3.12", "python"),
        ("#!/bin/bash", "bash"),
        ("#!/bin/sh", "bash"),
        ("#!/usr/bin/env zsh", "zsh"),
        ("#!/usr/bin/env node", "javascript"),
        ("#!/usr/bin/env ruby", "ruby"),
        ("#!/usr/bin/perl -w", "perl"),
        ("#!/usr/bin/env lua", "lua"),
        ("#!/usr/bin/env Rscript", "r"),
        ("#!/usr/bin/env php", "php"),
        ("#!/usr/bin/env -S python -u", "python"),
    ],
)
def test_interpreter_line_resolves_the_language(line, expected):
    assert language_from_shebang(line) == expected


@pytest.mark.parametrize(
    "line",
    ["", "# not a shebang", "#!/usr/bin/env", "#!", "#!/usr/bin/env brainfuck", "print('hi')"],
)
def test_a_line_that_names_no_known_interpreter_resolves_to_nothing(line):
    assert language_from_shebang(line) is None


def test_an_extension_always_wins_over_the_interpreter_line():
    # A .py file is Python whatever its first line claims.
    assert language_for("deploy.py", text="#!/bin/bash\n") == "python"


def test_a_file_with_an_unclaimed_extension_is_not_sniffed():
    # Only an extension-less file gets the interpreter-line treatment: a .csv
    # that happens to begin with a hash is data, not a script.
    assert language_for("data.csv", text="#!/usr/bin/env python3\n") is None


@needs_python
def test_an_extension_less_script_is_parsed_by_its_interpreter_line():
    parsed = parse_source("bin/deploy", "#!/usr/bin/env python3\n\n\ndef release():\n    return 1\n")
    assert parsed.language == "python"
    assert ("function", "release") in {(s.kind, s.name) for s in parsed.symbols}


@needs_python
def test_extension_less_scripts_are_picked_up_by_ingestion(tmp_path):
    from dkg.code.changes import list_tracked_files

    repo = tmp_path / "repo"
    (repo / "bin").mkdir(parents=True)
    (repo / "bin" / "deploy").write_text("#!/usr/bin/env python3\ndef go():\n    return 1\n", encoding="utf-8")
    (repo / "bin" / "notes").write_text("just a text file\n", encoding="utf-8")
    (repo / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    import subprocess

    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603, S607
    tracked = list_tracked_files(repo, exts={".py"})
    assert "bin/deploy" in tracked
    assert "app.py" in tracked
    # A plain text file with no extension and no interpreter line stays out.
    assert "bin/notes" not in tracked


# -- fallback fidelity carries into the graph -----------------------------------
#
# R is the vehicle here. With the optional 'code-bundle' extra installed it
# parses with a real grammar, so these tests force the degraded lane explicitly
# rather than depending on which extras this environment happens to have. The
# claim under test is not "R is a fallback language" (it no longer is when the
# extra is present); it is that a parse done WITHOUT a grammar is labelled that
# way and scores lower, all the way into the graph.


@pytest.fixture
def without_grammar_bundle(monkeypatch):
    """Make the parser behave as though the grammar bundle were not installed."""
    import dkg.code.parser as parser_mod

    real = parser_mod.grammar_available
    monkeypatch.setattr(
        parser_mod,
        "grammar_available",
        lambda language: False if language in FALLBACK_SPECS else real(language),
    )


def test_a_fallback_parse_is_marked_as_one(without_grammar_bundle):
    parsed = parse_source("model.R", "fit <- function(x) {\n  summary(x)\n}\n")
    assert parsed.fidelity == FIDELITY_FALLBACK


def test_the_same_source_parses_with_a_grammar_when_the_bundle_is_present():
    """The other half of the pair: the extra actually changes the fidelity."""
    if not grammar_available("r"):
        pytest.skip("the code-bundle extra is not installed in this environment")
    parsed = parse_source("model.R", "fit <- function(x) {\n  summary(x)\n}\n")
    assert parsed.fidelity == "grammar"
    assert [(s.kind, s.name) for s in parsed.symbols if s.kind != "module"] == [("function", "fit")]


@needs_python
def test_a_grammar_parse_is_not_marked_as_a_fallback():
    parsed = parse_source("a.py", "def go():\n    return 1\n")
    assert parsed.fidelity == "grammar"


def test_an_edge_from_a_fallback_parse_scores_below_the_same_parsed_edge(without_grammar_bundle):
    fallback = parse_source(
        "lib.R", "helper <- function(x) {\n  x\n}\n\nrun <- function(x) {\n  helper(x)\n}\n"
    )
    edges = resolve_edges([fallback])
    calls = [e for e in edges if e.predicate == "calls" and e.to_qualified.endswith("::helper")]
    assert calls, "the fallback parse should still produce a call edge"
    # The same edge from a grammar parse would score CONF_RESOLVED.
    assert calls[0].confidence == pytest.approx(CONF_RESOLVED * FALLBACK_CONFIDENCE_FACTOR)
    assert calls[0].confidence < CONF_RESOLVED


def test_fidelity_reaches_the_graph_so_a_consumer_can_filter(tmp_path, without_grammar_bundle):
    text = "helper <- function(x) {\n  x\n}\n"
    parsed = parse_source("lib.R", text)
    with open_database(tmp_path / "g.db") as db:
        write_code_graph(db, [parsed], {"lib.R": text}, source_uri="code://t", tenant_id="local")
        rows = db.fetchall(
            "SELECT metadata_json FROM entities WHERE tenant_id='local' AND kind LIKE 'code:%';"
        )
    assert rows
    fidelities = {json.loads(r["metadata_json"])["fidelity"] for r in rows}
    assert fidelities == {FIDELITY_FALLBACK}


# -- custom-language config validation -------------------------------------------


def _base(**extra):
    entry = {
        "name": "demo",
        "grammar_module": "tree_sitter_demo",
        "licence": "MIT",
        "extensions": [".demo"],
        "symbols": {"function": ["function_definition"]},
    }
    entry.update(extra)
    return {"languages": [entry]}


def test_config_accepts_every_documented_section():
    registry, warnings = parse_config(
        _base(
            name_node_types=["identifier"],
            name_skip_types=["type_node"],
            owner_field="table",
            default_names={"constructor_definition": "constructor"},
            calls={
                "node_types": ["call"],
                "name_field": "callee",
                "prev_sibling": True,
                "require_child": ["argument_list"],
                "import_keywords": ["require"],
            },
            imports={"node_types": ["import"], "name_field": "source"},
            inherits={"field": "superclass", "node_types": ["base_clause"]},
            bindings={"node_types": ["binding"], "values": {"lambda": "function"}, "first_child": True},
            keywords={
                "node_types": ["call"],
                "symbols": {"defmodule": "class"},
                "imports": ["import"],
                "name_field": "arguments",
            },
            scope={"node_types": ["impl"], "name_field": "type"},
            body_node_types=["function_body"],
            skip_node_types=["signature"],
            test_node_types=["test_declaration"],
        )
    )
    assert warnings == []
    spec = registry.get("demo")
    assert spec is not None
    assert spec.owner_field == "table"
    assert spec.call_prev_sibling is True
    assert spec.import_keywords == ("require",)
    assert spec.inherits_node_types == ("base_clause",)
    assert spec.binding_value_types == {"lambda": "function"}
    assert spec.binding_first_child is True
    assert spec.keyword_symbols == {"defmodule": "class"}
    assert spec.body_node_types == ("function_body",)
    assert spec.default_names == {"constructor_definition": "constructor"}
    assert spec.name_skip_types == ("type_node",)


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        ({"calls": ["not", "an", "object"]}, "calls"),
        ({"imports": "nope"}, "imports"),
        ({"bindings": {"values": {"lambda": "not_a_category"}}}, "bindings.values"),
        ({"keywords": {"symbols": {"def": "not_a_category"}}}, "keywords.symbols"),
    ],
)
def test_config_rejects_a_malformed_section_and_says_where(bad, fragment):
    with pytest.raises(ValidationError) as excinfo:
        parse_config(_base(**bad))
    assert fragment in str(excinfo.value)


def test_config_still_rejects_the_original_required_keys():
    with pytest.raises(ValidationError):
        parse_config({"languages": [{"name": "demo", "grammar_module": "x", "extensions": [".d"], "symbols": {}}]})
