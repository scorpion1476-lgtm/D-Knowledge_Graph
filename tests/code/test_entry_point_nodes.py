"""V-05: synthesised entry-point nodes for routed and scheduled invocations."""

from __future__ import annotations

import pytest

from dkg.code.entrypoints import (
    EDGE_DISPATCHES,
    EDGE_ROUTES_TO,
    KIND_ENTRYPOINT,
    KIND_ROUTE,
    SUPPORTED,
    detect,
    report,
)
from dkg.code.model import NODE_KINDS

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")


DECORATOR_ROUTES = (
    "from framework import App\n"
    "\n"
    "app = App(__name__)\n"
    "\n"
    "\n"
    '@app.get("/users")\n'
    "def list_users():\n"
    "    return fetch_users()\n"
    "\n"
    "\n"
    "def fetch_users():\n"
    "    return []\n"
)

SHARED_TASK = (
    "from queue_lib import shared_task\n"
    "\n"
    "\n"
    "@shared_task\n"
    "def nightly_rollup():\n"
    "    return compute()\n"
    "\n"
    "\n"
    "def compute():\n"
    "    return 1\n"
)


# -- the node vocabulary ------------------------------------------------------


def test_route_and_entrypoint_are_part_of_the_node_vocabulary():
    assert KIND_ROUTE in NODE_KINDS
    assert KIND_ENTRYPOINT in NODE_KINDS


def test_the_supported_frameworks_are_named_so_a_gap_is_visible():
    assert SUPPORTED
    assert "python-decorator-routes" in SUPPORTED
    assert "express" in SUPPORTED
    assert "LOWER BOUND" in report()["why"]
    assert set(report()["kinds"]) == {KIND_ROUTE, KIND_ENTRYPOINT}


# -- detection ----------------------------------------------------------------


def test_a_decorator_route_becomes_a_route_node_linked_to_its_handler():
    symbols, references = detect("api.py", DECORATOR_ROUTES, "python")

    assert [s.kind for s in symbols] == [KIND_ROUTE]
    assert symbols[0].name == "GET /users"
    assert symbols[0].qualified == "api.py::route:GET /users"
    assert symbols[0].start_line == 6
    assert [(r.kind, r.name) for r in references] == [(EDGE_ROUTES_TO, "list_users")]


def test_a_shared_task_becomes_an_entrypoint_node():
    symbols, references = detect("jobs.py", SHARED_TASK, "python")

    assert [s.kind for s in symbols] == [KIND_ENTRYPOINT]
    assert symbols[0].name == "task nightly_rollup"
    assert [(r.kind, r.name) for r in references] == [(EDGE_DISPATCHES, "nightly_rollup")]


def test_an_express_route_is_detected():
    source = 'const app = express();\napp.post("/orders", createOrder);\n'

    symbols, references = detect("server.js", source, "javascript")

    assert symbols[0].kind == KIND_ROUTE
    assert symbols[0].name == "POST /orders"
    assert references[0].name == "createOrder"


def test_a_go_handler_registration_is_detected():
    source = 'func main() {\n\thttp.HandleFunc("/health", healthHandler)\n}\n'

    symbols, references = detect("main.go", source, "go")

    assert symbols[0].kind == KIND_ROUTE
    assert references[0].name == "healthHandler"


def test_a_django_urlconf_entry_is_detected():
    source = 'urlpatterns = [\n    path("admin/", admin_view),\n]\n'

    symbols, references = detect("urls.py", source, "python")

    assert symbols[0].kind == KIND_ROUTE
    assert references[0].name == "admin_view"


def test_a_dotted_handler_is_reduced_to_its_bare_name():
    source = 'app.get("/x", handlers.index);\n'

    _symbols, references = detect("s.js", source, "javascript")

    assert references[0].name == "index"


def test_a_pattern_does_not_fire_for_the_wrong_language():
    """The Python route pattern must not match a JavaScript file."""
    symbols, _references = detect("api.js", DECORATOR_ROUTES, "javascript")

    assert symbols == []


def test_source_with_no_registration_produces_nothing():
    symbols, references = detect("plain.py", "def f():\n    return 1\n", "python")

    assert symbols == [] and references == []


def test_the_same_route_declared_twice_produces_one_node():
    source = DECORATOR_ROUTES + "\n" + DECORATOR_ROUTES

    symbols, _references = detect("api.py", source, "python")

    assert len(symbols) == 1


# -- end to end ---------------------------------------------------------------


@requires_ts
def test_a_route_node_reaches_its_handler_in_the_graph(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / "api.py").write_text(DECORATOR_ROUTES, encoding="utf-8")

    ingest_repo(db, tmp_path)

    node = db.fetchone(
        "SELECT entity_id, canonical FROM entities WHERE kind='code:route';"
    )
    assert node is not None, "the route must become a node"
    assert node["canonical"] == "api.py::route:GET /users"

    edge = db.fetchone(
        "SELECT o.canonical AS target FROM relationships r "
        "JOIN entities o ON o.entity_id = r.object_id "
        "WHERE r.predicate='code:routes_to' AND r.subject_id=?;",
        (node["entity_id"],),
    )
    assert edge is not None
    assert edge["target"] == "api.py::list_users"


@requires_ts
def test_a_scheduled_job_node_reaches_its_handler_in_the_graph(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / "jobs.py").write_text(SHARED_TASK, encoding="utf-8")

    ingest_repo(db, tmp_path)

    node = db.fetchone("SELECT entity_id FROM entities WHERE kind='code:entrypoint';")
    assert node is not None
    edge = db.fetchone(
        "SELECT o.canonical AS target FROM relationships r "
        "JOIN entities o ON o.entity_id = r.object_id "
        "WHERE r.predicate='code:dispatches' AND r.subject_id=?;",
        (node["entity_id"],),
    )
    assert edge is not None
    assert edge["target"] == "jobs.py::nightly_rollup"


@requires_ts
def test_a_flow_now_starts_at_the_route_rather_than_at_a_guess(db, tmp_path):
    """The point of the node: an execution flow gets a real starting point."""
    from dkg.code.catalogue import list_flows
    from dkg.code.ingest import ingest_repo

    (tmp_path / "api.py").write_text(DECORATOR_ROUTES, encoding="utf-8")

    ingest_repo(db, tmp_path, postprocess="standard")

    names = {f["name"] for f in list_flows(db)["flows"]}
    assert "api.py::route:GET /users" in names, names


@requires_ts
def test_a_routed_handler_is_not_reported_as_dead_code(db, tmp_path):
    """Nothing in the source calls a request handler; the framework does."""
    from dkg.code.deadcode import dead_code_candidates
    from dkg.code.ingest import ingest_repo

    (tmp_path / "api.py").write_text(DECORATOR_ROUTES, encoding="utf-8")

    ingest_repo(db, tmp_path)

    result = dead_code_candidates(db)
    candidates = {c["canonical"] for c in result["candidates"]}
    assert "api.py::list_users" not in candidates
    excluded = {e["canonical"] for e in result["entry_points_excluded"]}
    assert "api.py::list_users" in excluded


@requires_ts
def test_a_repository_with_no_framework_synthesises_no_entry_points(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / "plain.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    ingest_repo(db, tmp_path)

    assert (
        db.fetchone(
            "SELECT COUNT(*) AS n FROM entities WHERE kind IN ('code:route','code:entrypoint');"
        )["n"]
        == 0
    )
