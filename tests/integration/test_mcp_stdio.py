import json

from dkg.ingest.base import ingest_text
from dkg.mcp.server_stdio import handle_line


def _call(db, method, params=None, rid=1):
    req = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        req["params"] = params
    resp = json.loads(handle_line(db, json.dumps(req)))
    return resp


def test_initialize(db):
    resp = _call(db, "initialize")
    assert resp["result"]["server"] == "d-knowledge-graph"


def test_tools_list_includes_read_tools(db):
    resp = _call(db, "tools/list")
    names = {t["name"] for t in resp["result"]["tools"]}
    assert {"dkg.status", "dkg.search", "dkg.graph.neighbourhood"}.issubset(names)


def test_tools_call_status(db):
    resp = _call(db, "tools/call", {"name": "dkg.status", "arguments": {}})
    assert "result" in resp
    assert set(resp["result"].keys()) == {"documents", "chunks", "entities", "claims"}


def test_tools_call_search(db):
    ingest_text(db, "hello world about D-Knowledge_Graph", display_name="d")
    resp = _call(db, "tools/call", {"name": "dkg.search", "arguments": {"query": "hello", "limit": 5}})
    assert "results" in resp["result"]


def test_bad_json_returns_error(db):
    resp = json.loads(handle_line(db, "{not json"))
    assert "error" in resp
    assert resp["error"]["code"] == -32600


def test_unknown_method(db):
    resp = _call(db, "no/such/method")
    assert resp["error"]["code"] == -32601
