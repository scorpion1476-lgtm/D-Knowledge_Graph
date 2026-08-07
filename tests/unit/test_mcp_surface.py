"""The read-only MCP surface: breadth, honesty, and confinement.

The claims worth pinning down:

* the surface reaches the breadth it claims, and every tool on it is a distinct
  capability rather than a split made to reach a count,
* every tool is read-only, in the strong sense that calling all of them leaves
  the database byte-identical,
* the named directed queries genuinely follow different edges and directions,
* verbosity drops DETAIL, never RESULTS, and never the caveat,
* the allowlist restricts the surface and refuses a name it does not know,
* the documentation tool cannot be walked out of its root.
"""

from __future__ import annotations

import json

import pytest

from dkg.code.capability import grammar_available
from dkg.code.graph import write_code_graph
from dkg.code.parser import parse_source
from dkg.core.db import open_database
from dkg.core.errors import DKGError, ValidationError
from dkg.mcp.tools import build_read_registry

needs_python = pytest.mark.skipif(
    not grammar_available("python"), reason="the python grammar is not installed"
)

CODE = {
    "core.py": "def hub(v):\n    return v\n\n\ndef spare(v):\n    return v\n",
    "app.py": (
        "from core import hub\n\n\n"
        "class Base:\n    def run(self):\n        return 1\n\n\n"
        "class Impl(Base):\n    def run(self):\n        return hub(2)\n\n\n"
        "def entry(v):\n    return hub(v)\n"
    ),
    "test_app.py": "from app import entry\n\n\ndef test_entry():\n    return entry(1)\n",
}


@pytest.fixture
def db(tmp_path):
    """An EMPTY database.

    Deliberately empty and deliberately free of any parse: the surface's shape,
    its read-only guarantee, and its confinement are properties of the registry,
    not of the graph, so they must be testable on a build with no code extra
    installed. A fixture that parsed here would turn those into errors rather
    than into an honest skip on such a build.
    """
    with open_database(tmp_path / "g.db") as database:
        yield database


@pytest.fixture
def code_db(tmp_path):
    """A database with the sample code ingested. Needs the python grammar."""
    if not grammar_available("python"):
        pytest.skip("the python grammar is not installed")
    parsed = [parse_source(name, text) for name, text in CODE.items()]
    with open_database(tmp_path / "code.db") as database:
        write_code_graph(database, parsed, CODE, source_uri="code://mcp", tenant_id="local")
        yield database


@pytest.fixture
def registry(db):
    return build_read_registry(db)


@pytest.fixture
def code_registry(code_db):
    return build_read_registry(code_db)


def test_the_surface_reaches_at_least_thirty_read_only_tools(registry):
    assert len(registry.tools) >= 30, f"only {len(registry.tools)} tools"
    assert all(spec.kind == "read" for spec in registry.tools.values())


def test_every_tool_has_a_distinct_name_description_and_schema(registry):
    """A count reached by splitting one capability would show up as duplicates."""
    descriptions = [spec.description for spec in registry.tools.values()]
    assert len(set(descriptions)) == len(descriptions), "two tools share a description"
    for name, spec in registry.tools.items():
        assert spec.description.strip(), name
        assert spec.input_schema.get("type") == "object", name
        assert callable(spec.handler), name


def test_every_listed_tool_is_callable_and_the_listing_matches_the_registry(registry):
    listed = {entry["name"] for entry in registry.list()}
    assert listed == set(registry.tools)
    for entry in registry.list():
        assert entry["kind"] == "read"


def _logical_snapshot(db) -> str:
    """A hash of the database's CONTENTS, not of its file.

    Hashing the .db file does NOT work here and an earlier version of this test
    made exactly that mistake. The database runs in WAL mode, so a committed
    INSERT lands in the -wal sidecar and the main file's bytes are unchanged
    until a checkpoint. A tool that deleted the entire graph would have passed a
    file-hash check. This reads every row of every table instead, so any insert,
    update, or delete changes the result.
    """
    import hashlib as _hashlib

    tables = [
        r["name"]
        for r in db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
    ]
    digest = _hashlib.sha256()
    for table in tables:
        digest.update(table.encode("utf-8"))
        for row in db.fetchall(f"SELECT * FROM {table};"):  # noqa: S608
            digest.update(repr(tuple(row)).encode("utf-8"))
    return f"{len(tables)}:{digest.hexdigest()}"


@needs_python
def test_the_snapshot_helper_actually_detects_a_write(code_db):
    """Guard the guard.

    If _logical_snapshot could not see a write, the read-only test below would
    read green while a tool rewrote the graph. That is precisely the failure the
    file-hash version of this test had, so the detector is now tested first.
    """
    before = _logical_snapshot(code_db)
    code_db.execute(
        "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display) "
        "VALUES ('probe-id','local','code:function','probe.py::probe','probe');"
    )
    after = _logical_snapshot(code_db)
    assert before != after, "the snapshot cannot detect a write; it proves nothing"
    code_db.execute("DELETE FROM entities WHERE entity_id='probe-id';")
    assert _logical_snapshot(code_db) == before, "cleanup did not restore the state"


@needs_python
def test_calling_every_tool_leaves_the_database_unchanged(code_db, code_registry):
    """Read-only in the strong sense: no row is inserted, updated, or deleted."""
    before = _logical_snapshot(code_db)
    arguments = {
        "symbol": "app.py::entry",
        "entity": "app.py::entry",
        "entry": "app.py::entry",
        "query": "hub",
        "claim_id": "none",
        "name": "change-review",
        "document": "SECURITY_MODEL.md",
        "path": "core.py",
        "text": "def x():\n    return 1\n",
        "before": "a.json",
        "after": "b.json",
        "limit": 5,
        "depth": 2,
        "max_nodes": 50,
    }
    called = 0
    refused: list[str] = []
    for name, spec in sorted(code_registry.tools.items()):
        args = {k: v for k, v in arguments.items() if k in spec.input_schema.get("properties", {})}
        try:
            result = spec.handler(args)
        except Exception:  # noqa: BLE001
            # A tool may legitimately refuse these generic arguments. What it
            # must never do is write, which the snapshot below actually checks.
            # (An earlier version asserted the exception was not SystemExit or
            # KeyboardInterrupt, which `except Exception` can never bind, so the
            # assertion was unreachable and the clause was a bare swallow.)
            refused.append(name)
            continue
        called += 1
        assert isinstance(result, dict), name
    assert called >= 20, f"only {called} tools accepted the generic arguments"
    assert called + len(refused) == len(code_registry.tools)
    assert _logical_snapshot(code_db) == before, "a read-only tool modified the database"


# -- named directed relationship queries ---------------------------------------


@needs_python
def test_callers_and_callees_follow_opposite_directions(code_registry):
    callers = code_registry.call("dkg.code.callers", {"symbol": "core.py::hub", "depth": 1})
    callees = code_registry.call("dkg.code.callees", {"symbol": "app.py::entry", "depth": 1})
    assert "app.py::entry" in {s["canonical"] for s in callers["slices"]}
    assert "core.py::hub" in {s["canonical"] for s in callees["slices"]}


@needs_python
def test_inheritance_queries_follow_inherits_in_both_directions(code_registry):
    subs = code_registry.call("dkg.code.implementations", {"symbol": "app.py::Base"})
    bases = code_registry.call("dkg.code.base_types", {"symbol": "app.py::Impl"})
    assert subs["predicate"] == "code:inherits"
    assert subs["direction"] == "incoming"
    assert bases["direction"] == "outgoing"
    assert "app.py::Impl" in {e["canonical"] for e in subs["edges"]}
    assert "app.py::Base" in {e["canonical"] for e in bases["edges"]}


@needs_python
def test_every_directed_result_carries_its_confidence_tier(code_registry):
    result = code_registry.call("dkg.code.implementations", {"symbol": "app.py::Base"})
    for edge in result["edges"]:
        assert edge["confidence"]["tier"] in ("extracted", "inferred", "ambiguous")
        assert edge["confidence"]["why"]


@needs_python
def test_a_directed_query_for_an_unknown_symbol_refuses_rather_than_guesses(code_registry):
    result = code_registry.call("dkg.code.importers", {"symbol": "nope.py::missing"})
    assert result["found"] is False
    assert result["edges"] == []
    assert "nothing was guessed" in result["why"]


@needs_python
def test_the_framework_query_rejects_a_relation_outside_its_vocabulary(code_registry):
    with pytest.raises(ValidationError, match="unknown framework relation"):
        code_registry.call("dkg.code.framework", {"symbol": "app.py::entry", "relation": "calls"})


# -- verbosity -----------------------------------------------------------------


@needs_python
def test_compact_verbosity_drops_detail_but_never_a_result(code_registry):
    full = code_registry.call("dkg.code.criticality", {"entry": "app.py::entry", "depth": 3})
    compact = code_registry.call(
        "dkg.code.criticality", {"entry": "app.py::entry", "depth": 3, "verbosity": "compact"}
    )
    assert len(compact["flows"]) == len(full["flows"])
    assert [f["path"] for f in compact["flows"]] == [f["path"] for f in full["flows"]]
    assert all("components" not in f for f in compact["flows"])
    assert all("components" in f for f in full["flows"])
    assert compact["verbosity"] == "compact"


@needs_python
def test_compact_verbosity_keeps_the_over_approximation_caveat(code_registry):
    """A caveat that vanishes when a caller asks for less is not a caveat."""
    compact = code_registry.call(
        "dkg.code.criticality", {"entry": "app.py::entry", "verbosity": "compact"}
    )
    assert "over-approximate" in compact["why"]


# -- orientation, prompts, docs, repos, memory ---------------------------------


@needs_python
def test_orientation_reports_shape_and_entry_points_with_its_caveat(code_registry):
    result = code_registry.call("dkg.orient", {"limit": 5})
    assert result["counts"]["code_entities"] > 0
    assert result["suggested_next"]
    # entry() is called by the test, so the genuine top-of-chain is the test.
    assert any(e["canonical"] for e in result["entry_points"])
    assert "structural" in result["why"]


def test_prompt_templates_are_listed_and_fetchable(registry):
    """Named independently, so emptying PROMPT_TEMPLATES cannot pass.

    `total == len(PROMPT_TEMPLATES)` was the earlier assertion, which compares
    the constant to itself and holds for any value including zero.
    """
    expected = {"change-review", "architecture-map", "guided-onboarding", "risk-triage"}
    listing = registry.call("dkg.prompts.list", {})
    assert {p["name"] for p in listing["prompts"]} == expected
    assert listing["total"] == len(expected)
    for entry in listing["prompts"]:
        fetched = registry.call("dkg.prompts.get", {"name": entry["name"]})
        # A template that names no tool is not a workflow prompt.
        assert "dkg." in fetched["template"], entry["name"]
        assert len(fetched["template"]) > 80, entry["name"]
        assert fetched["title"] and fetched["description"]


def test_an_unknown_prompt_is_refused(registry):
    with pytest.raises(ValidationError, match="unknown prompt"):
        registry.call("dkg.prompts.get", {"name": "no-such-prompt"})


def test_the_docs_tool_serves_a_packaged_document_and_a_named_section(registry):
    whole = registry.call("dkg.docs.section", {"document": "SECURITY_MODEL.md"})
    assert whole["found"] is True
    assert whole["text"]
    sections = [line for line in whole["text"].splitlines() if line.startswith("## ")]
    if sections:
        heading = sections[0].lstrip("# ").strip()
        part = registry.call(
            "dkg.docs.section", {"document": "SECURITY_MODEL.md", "section": heading}
        )
        assert part["found"] is True
        assert len(part["text"]) < len(whole["text"])


@pytest.mark.parametrize(
    "document",
    [
        "../pyproject.toml",
        "../../etc/passwd",
        "/etc/passwd",
        "..%2f..%2fpyproject.toml",
        "subdir/../../LICENSE",
    ],
)
def test_the_docs_tool_cannot_be_walked_out_of_its_root(registry, document):
    """A caller-named path behind the MCP boundary must stay confined."""
    result = registry.call("dkg.docs.section", {"document": document})
    assert result["found"] is False


def test_an_unknown_document_lists_what_is_available_rather_than_erroring(registry):
    result = registry.call("dkg.docs.section", {"document": "NOT_A_REAL_DOC"})
    assert result["found"] is False
    assert result["available"]


def test_repos_and_memory_listings_are_read_only_and_explain_themselves(registry):
    repos = registry.call("dkg.repos.list", {})
    assert "repos" in repos and isinstance(repos["repos"], list)
    memory = registry.call("dkg.memory.list", {})
    assert "answers" in memory
    assert "not a live one" in memory["why"]


# -- the allowlist ---------------------------------------------------------------


def test_an_allowlist_restricts_the_served_surface(db):
    restricted = build_read_registry(db, allowlist=["dkg.status", "dkg.orient"])
    assert set(restricted.tools) == {"dkg.status", "dkg.orient"}
    # A restricted server does not even advertise what it will not run.
    assert {e["name"] for e in restricted.list()} == {"dkg.status", "dkg.orient"}


def test_an_allowlist_naming_an_unknown_tool_is_refused_not_ignored(db):
    """Silently accepting a typo would leave an operator believing it was served."""
    with pytest.raises(ValidationError, match="do not exist"):
        build_read_registry(db, allowlist=["dkg.status", "dkg.nonexistent"])


@needs_python
def test_the_confidence_profile_reports_the_tier_mix_of_the_graph(code_registry):
    result = code_registry.call("dkg.code.confidence", {})
    assert result["edges"] > 0
    assert set(result["totals"]) == {"extracted", "inferred", "ambiguous"}
    assert sum(result["totals"].values()) == result["edges"]
    assert abs(sum(result["share"].values()) - 1.0) < 0.01


@needs_python
def test_review_context_answers_in_one_call_what_a_reviewer_would_ask_in_six(code_registry):
    result = code_registry.call("dkg.code.review_context", {"symbol": "core.py::hub"})
    assert result["found"] is True
    assert "app.py::entry" in result["callers"]
    assert isinstance(result["questions"], list)
    assert "prompts for a human" in result["why"]


@needs_python
def test_impact_radius_ranks_rather_than_returning_a_flat_set(code_registry):
    result = code_registry.call("dkg.code.impact_radius", {"symbol": "core.py::hub", "depth": 3})
    assert result["found"] is True
    scores = [item["score"] for item in result["impacted"]]
    assert scores == sorted(scores, reverse=True)
    for item in result["impacted"]:
        assert item["score"] == pytest.approx(sum(item["components"].values()), abs=1e-4)
    assert result["weights"]


@needs_python
def test_every_analysis_tool_result_is_json_serialisable(code_registry):
    for name in (
        "dkg.orient",
        "dkg.code.confidence",
        "dkg.code.criticality",
        "dkg.code.traverse",
        "dkg.code.slices",
        "dkg.code.impact_radius",
        "dkg.code.review_context",
    ):
        spec = code_registry.tools[name]
        args = {}
        properties = spec.input_schema.get("properties", {})
        for key, value in (("symbol", "app.py::entry"), ("entry", "app.py::entry")):
            if key in properties:
                args[key] = value
        result = spec.handler(args)
        json.dumps(result)  # raises if anything is not serialisable


# -- the declared schema must be binding, added after an adversarial review --
#
# The registry handed arguments straight to the handler, so a schema declaring
# "limit": {"minimum": 1, "maximum": 100} advertised a bound nothing applied. A
# review asked dkg.search for a billion results and got as many as the corpus
# held, and pushed a 200,000-character query far enough down to surface a raw
# storage error rather than a validation error. The MCP surface is the trust
# boundary against an agent acting on injected content, so this has to hold.


def _read_registry(tmp_path):
    from dkg.core.db import open_database
    from dkg.mcp.tools import build_read_registry

    db_cm = open_database(tmp_path / "bounds.db")
    db = db_cm.__enter__()
    return db_cm, build_read_registry(db)


def test_a_limit_above_the_declared_maximum_is_refused(tmp_path):
    db_cm, reg = _read_registry(tmp_path)
    try:
        with pytest.raises(DKGError) as excinfo:
            reg.call("dkg.search", {"query": "x", "limit": 1_000_000_000})
        assert "at most" in str(excinfo.value)
        # The declared maximum itself must still work, or the bound is wrong.
        assert reg.call("dkg.search", {"query": "x", "limit": 100})["results"] == []
    finally:
        db_cm.__exit__(None, None, None)


def test_a_limit_is_bounded_even_when_the_schema_declares_no_maximum(tmp_path):
    """The bound that matters is the one nobody remembered to declare."""
    db_cm, reg = _read_registry(tmp_path)
    try:
        with pytest.raises(DKGError):
            reg.call("dkg.search.keyword", {"query": "x", "limit": 1_000_000_000})
    finally:
        db_cm.__exit__(None, None, None)


def test_an_enormous_query_string_is_refused_before_it_reaches_storage(tmp_path):
    db_cm, reg = _read_registry(tmp_path)
    try:
        with pytest.raises(DKGError) as excinfo:
            reg.call("dkg.search", {"query": "a" * 200_000})
        assert "characters" in str(excinfo.value)
    finally:
        db_cm.__exit__(None, None, None)


def test_every_read_tool_that_takes_a_limit_actually_bounds_it(tmp_path):
    """Guard the guard: prove the enforcement reaches every tool, not just one.

    A per-tool spot check would pass while most of the surface stayed open, so
    this drives every registered tool that declares an integer argument.
    """
    db_cm, reg = _read_registry(tmp_path)
    try:
        checked = 0
        for name, spec in reg.tools.items():
            for key, rules in (spec.input_schema.get("properties") or {}).items():
                if not isinstance(rules, dict) or rules.get("type") != "integer":
                    continue
                checked += 1
                with pytest.raises(DKGError):
                    reg.call(name, {key: 1_000_000_000})
        assert checked >= 10, f"only {checked} integer arguments exercised; the sweep is too thin"
    finally:
        db_cm.__exit__(None, None, None)
