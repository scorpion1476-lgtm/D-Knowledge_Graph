"""Code-graph snapshots and structural diffs between them.

The tests that ingest real source are gated on tree-sitter (the 'code' extra) and
skip honestly when it is absent. The pure-unit diff tests build snapshot dicts by
hand and run everywhere, so the diff logic stays pinned with no parser present.
"""

from __future__ import annotations

import json

import pytest

from dkg.code.diff import (
    SNAPSHOT_KIND,
    SNAPSHOT_VERSION,
    VOLATILE_KEYS,
    diff_snapshots,
    load_snapshot,
    snapshot_code_graph,
)
from dkg.core.errors import ValidationError

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _ingest(db, files, *, replace_paths=None):
    parsed = [parse_source(path, text, language=lang) for path, text, lang in files]
    texts = {path: text for path, text, _lang in files}
    write_code_graph(db, parsed, texts, source_uri="test://diff", replace_paths=replace_paths)


def _body(snapshot):
    """A snapshot with the volatile capture time removed, which is what compares."""
    return {k: v for k, v in snapshot.items() if k not in VOLATILE_KEYS}


CHAIN = (
    "app.py",
    "def leaf():\n    return 1\n"
    "def mid():\n    return leaf()\n"
    "def entry():\n    return mid()\n",
    "python",
)

CHAIN_PLUS_ONE = (
    "app.py",
    "def leaf():\n    return 1\n"
    "def mid():\n    return leaf()\n"
    "def entry():\n    return mid()\n"
    "def added_later():\n    return 7\n",
    "python",
)


# -- hand-built snapshots ---------------------------------------------------


def _node(canonical, kind="code:function", path="a.py", language="python"):
    return {"canonical": canonical, "kind": kind, "path": path, "language": language}


def _edge(frm, to, predicate="code:calls", weight=1.0):
    return {"from": frm, "to": to, "predicate": predicate, "weight": weight}


def _snapshot(nodes, edges, communities, *, tenant_id="local", truncated=False, label=None):
    """Build a valid snapshot dict without touching a database."""
    snap = {
        "kind": SNAPSHOT_KIND,
        "version": SNAPSHOT_VERSION,
        "tenant_id": tenant_id,
        "predicates": ["code:calls", "code:imports", "code:inherits"],
        "resolution": 1.0,
        "counts": {"nodes": len(nodes), "edges": len(edges), "communities": len(set(communities.values()))},
        "nodes": list(nodes),
        "edges": list(edges),
        "communities": dict(communities),
        "truncated": truncated,
        "taken_at": "2026-01-01T00:00:00+00:00",
    }
    if label is not None:
        snap["label"] = label
    return snap


# -- snapshot shape and determinism -----------------------------------------


@requires_ts
def test_snapshot_round_trips_through_json_unchanged(db):
    _ingest(db, [CHAIN])
    snap = snapshot_code_graph(db, label="first")
    assert snap["kind"] == SNAPSHOT_KIND
    assert snap["version"] == SNAPSHOT_VERSION
    assert snap["label"] == "first"
    assert json.loads(json.dumps(snap)) == snap


@requires_ts
def test_snapshot_keys_nodes_and_edges_by_canonical_name(db):
    _ingest(db, [CHAIN])
    snap = snapshot_code_graph(db)
    canonicals = [rec["canonical"] for rec in snap["nodes"]]
    assert "app.py::entry" in canonicals
    assert "app.py" in canonicals  # the module node
    assert canonicals == sorted(canonicals)
    entry = next(rec for rec in snap["nodes"] if rec["canonical"] == "app.py::entry")
    assert entry["path"] == "app.py"
    assert entry["language"] == "python"
    calls = {(e["from"], e["to"]) for e in snap["edges"] if e["predicate"] == "code:calls"}
    assert ("app.py::entry", "app.py::mid") in calls
    assert ("app.py::mid", "app.py::leaf") in calls
    # containment is not a structural reference edge, so it is out of the default
    assert all(e["predicate"] != "code:defines" for e in snap["edges"])
    assert snap["counts"]["nodes"] == len(snap["nodes"])
    assert snap["counts"]["edges"] == len(snap["edges"])
    assert set(snap["communities"]) == set(canonicals)
    assert snap["truncated"] is False


@requires_ts
def test_two_snapshots_of_an_unchanged_graph_are_equal_and_serialise_identically(db):
    _ingest(db, [CHAIN])
    first = snapshot_code_graph(db)
    second = snapshot_code_graph(db)
    assert _body(first) == _body(second)
    assert json.dumps(_body(first)) == json.dumps(_body(second))
    # the only thing that may differ is the deliberately excluded capture time
    assert set(first) - set(_body(first)) == set(VOLATILE_KEYS)


@requires_ts
def test_diff_of_a_graph_against_itself_is_empty(db):
    _ingest(db, [CHAIN])
    snap = snapshot_code_graph(db)
    result = diff_snapshots(snap, snapshot_code_graph(db))
    assert result["added_nodes"] == []
    assert result["removed_nodes"] == []
    assert result["added_edges"] == []
    assert result["removed_edges"] == []
    assert result["changed_edges"] == []
    assert result["community_changes"] == []
    assert result["summary"]["changed"] is False
    assert diff_snapshots(snap, snap)["summary"]["changed"] is False


@requires_ts
def test_snapshot_reports_the_node_cap_as_truncated(db):
    _ingest(db, [CHAIN])
    snap = snapshot_code_graph(db, max_nodes=2)
    assert snap["truncated"] is True
    assert snap["counts"]["nodes"] == 2
    # a diff touching a truncated snapshot says so rather than implying completeness
    result = diff_snapshots(snap, snapshot_code_graph(db))
    assert result["why"]["truncated"] is True


# -- diffs over real ingestion ----------------------------------------------


@requires_ts
def test_adding_a_function_appears_in_added_nodes(db):
    _ingest(db, [CHAIN])
    before = snapshot_code_graph(db)
    _ingest(db, [CHAIN_PLUS_ONE], replace_paths={"app.py"})
    after = snapshot_code_graph(db)

    result = diff_snapshots(before, after)
    added = [rec["canonical"] for rec in result["added_nodes"]]
    assert added == ["app.py::added_later"]
    assert result["removed_nodes"] == []
    assert result["summary"]["added_nodes"] == 1
    assert result["summary"]["changed"] is True


@requires_ts
def test_a_pure_node_addition_does_not_report_every_node_as_regrouped(db):
    # The new function references nothing and nothing references it, so no
    # surviving node's set of community peers can have changed.
    _ingest(db, [CHAIN])
    before = snapshot_code_graph(db)
    _ingest(db, [CHAIN_PLUS_ONE], replace_paths={"app.py"})
    after = snapshot_code_graph(db)

    result = diff_snapshots(before, after)
    assert result["community_changes"] == []
    assert result["summary"]["community_changes"] == 0


@requires_ts
def test_replacing_a_files_content_removes_its_nodes_and_edges(db):
    _ingest(db, [CHAIN, ("util.py", "def helper():\n    return 2\n", "python")])
    before = snapshot_code_graph(db)
    assert any(rec["canonical"] == "app.py::mid" for rec in before["nodes"])

    _ingest(db, [("app.py", "def only_one():\n    return 0\n", "python")], replace_paths={"app.py"})
    after = snapshot_code_graph(db)

    result = diff_snapshots(before, after)
    removed = {rec["canonical"] for rec in result["removed_nodes"]}
    assert {"app.py::entry", "app.py::mid", "app.py::leaf"} <= removed
    assert "util.py::helper" not in removed  # the untouched file is unaffected
    removed_calls = {(e["from"], e["to"]) for e in result["removed_edges"]}
    assert ("app.py::entry", "app.py::mid") in removed_calls
    assert ("app.py::mid", "app.py::leaf") in removed_calls
    assert result["added_nodes"] and result["added_nodes"][0]["canonical"] == "app.py::only_one"


@requires_ts
def test_adding_a_call_edge_appears_in_added_edges(db):
    _ingest(db, [("m.py", "def target():\n    return 1\ndef caller():\n    return 0\n", "python")])
    before = snapshot_code_graph(db)
    assert before["edges"] == []

    _ingest(
        db,
        [("m.py", "def target():\n    return 1\ndef caller():\n    return target()\n", "python")],
        replace_paths={"m.py"},
    )
    after = snapshot_code_graph(db)

    result = diff_snapshots(before, after)
    assert [(e["from"], e["to"], e["predicate"]) for e in result["added_edges"]] == [
        ("m.py::caller", "m.py::target", "code:calls")
    ]
    assert result["removed_edges"] == []
    assert result["changed_edges"] == []


# Two independent call clusters: a1 and a2 call each other, b1 and b2 call each
# other, and nothing crosses between them.
SPLIT_CLUSTERS = (
    "two.py",
    "def a1():\n    return a2()\n"
    "def a2():\n    return a1()\n"
    "def b1():\n    return b2()\n"
    "def b2():\n    return b1()\n",
    "python",
)
# The same four functions, now all calling each other.
MERGED_CLUSTERS = (
    "two.py",
    "def a1():\n    return a2() + b1() + b2()\n"
    "def a2():\n    return a1() + b1() + b2()\n"
    "def b1():\n    return a1() + a2() + b2()\n"
    "def b2():\n    return a1() + a2() + b1()\n",
    "python",
)
# The same four functions with exactly one call bridging the two clusters.
ONE_BRIDGE = (
    "two.py",
    "def a1():\n    return a2()\n"
    "def a2():\n    return a1()\n"
    "def b1():\n    return b2() + a1()\n"
    "def b2():\n    return b1()\n",
    "python",
)


@requires_ts
def test_joining_two_clusters_reports_the_co_membership_gain(db):
    _ingest(db, [SPLIT_CLUSTERS])
    before = snapshot_code_graph(db, predicates=("code:calls",))
    # the two clusters really are separate to start with
    assert before["communities"]["two.py::a1"] != before["communities"]["two.py::b1"]

    _ingest(db, [MERGED_CLUSTERS], replace_paths={"two.py"})
    after = snapshot_code_graph(db, predicates=("code:calls",))
    assert after["communities"]["two.py::a1"] == after["communities"]["two.py::b1"]

    result = diff_snapshots(before, after)
    # no function was added or removed, so every reported change is a regrouping
    assert result["added_nodes"] == []
    assert result["removed_nodes"] == []
    changes = {c["canonical"]: c for c in result["community_changes"]}
    assert sorted(changes) == ["two.py::a1", "two.py::a2", "two.py::b1", "two.py::b2"]
    assert changes["two.py::a1"]["gained"] == ["two.py::b1", "two.py::b2"]
    assert changes["two.py::a1"]["lost"] == []
    assert changes["two.py::b1"]["gained"] == ["two.py::a1", "two.py::a2"]
    # the module node is isolated on calls in both snapshots, so it did not regroup
    assert "two.py" not in changes
    assert result["summary"]["community_changes"] == 4


@requires_ts
def test_a_single_bridging_edge_is_an_edge_change_not_a_regrouping(db):
    # A lone call across two dense clusters is exactly what modularity
    # optimization is supposed to keep separate, so the honest report is a new
    # edge and no community change. Merging the clusters here would be the
    # detector over-reading one reference.
    _ingest(db, [SPLIT_CLUSTERS])
    before = snapshot_code_graph(db, predicates=("code:calls",))
    _ingest(db, [ONE_BRIDGE], replace_paths={"two.py"})
    after = snapshot_code_graph(db, predicates=("code:calls",))

    result = diff_snapshots(before, after)
    assert [(e["from"], e["to"]) for e in result["added_edges"]] == [("two.py::b1", "two.py::a1")]
    assert result["community_changes"] == []
    assert result["summary"]["changed"] is True


# -- pure-unit diff behaviour -----------------------------------------------


def test_changed_edge_weight_is_reported_separately_from_add_and_remove():
    nodes = [_node("a.py::x"), _node("a.py::y")]
    communities = {"a.py::x": 0, "a.py::y": 0}
    before = _snapshot(nodes, [_edge("a.py::x", "a.py::y", weight=0.5)], communities)
    after = _snapshot(nodes, [_edge("a.py::x", "a.py::y", weight=0.95)], communities)

    result = diff_snapshots(before, after)
    assert result["added_edges"] == []
    assert result["removed_edges"] == []
    assert result["changed_edges"] == [
        {
            "from": "a.py::x",
            "to": "a.py::y",
            "predicate": "code:calls",
            "before_weight": 0.5,
            "after_weight": 0.95,
        }
    ]
    assert result["summary"]["changed"] is True


def test_community_index_relabelling_alone_is_not_a_change():
    # The same partition with every index renumbered: co-membership is identical,
    # so nothing may be reported.
    nodes = [_node("a.py::x"), _node("a.py::y"), _node("a.py::z")]
    before = _snapshot(nodes, [], {"a.py::x": 0, "a.py::y": 0, "a.py::z": 1})
    after = _snapshot(nodes, [], {"a.py::x": 7, "a.py::y": 7, "a.py::z": 3})

    result = diff_snapshots(before, after)
    assert result["community_changes"] == []
    assert result["summary"]["changed"] is False


def test_a_real_regrouping_is_reported_as_gained_and_lost():
    nodes = [_node("a.py::x"), _node("a.py::y"), _node("a.py::z")]
    before = _snapshot(nodes, [], {"a.py::x": 0, "a.py::y": 0, "a.py::z": 1})
    after = _snapshot(nodes, [], {"a.py::x": 0, "a.py::y": 1, "a.py::z": 1})

    result = diff_snapshots(before, after)
    assert result["community_changes"] == [
        {"canonical": "a.py::x", "gained": [], "lost": ["a.py::y"]},
        {"canonical": "a.py::y", "gained": ["a.py::z"], "lost": ["a.py::x"]},
        {"canonical": "a.py::z", "gained": ["a.py::y"], "lost": []},
    ]


def test_removed_nodes_are_not_counted_as_community_losses():
    before = _snapshot(
        [_node("a.py::x"), _node("a.py::y"), _node("a.py::gone")],
        [],
        {"a.py::x": 0, "a.py::y": 0, "a.py::gone": 0},
    )
    after = _snapshot([_node("a.py::x"), _node("a.py::y")], [], {"a.py::x": 0, "a.py::y": 0})

    result = diff_snapshots(before, after)
    assert [rec["canonical"] for rec in result["removed_nodes"]] == ["a.py::gone"]
    # x and y still share exactly each other, so neither regrouped
    assert result["community_changes"] == []
    assert result["summary"]["removed_nodes"] == 1


def test_a_node_record_whose_kind_changed_is_a_removal_and_an_addition():
    before = _snapshot([_node("a.py::x", kind="code:function")], [], {"a.py::x": 0})
    after = _snapshot([_node("a.py::x", kind="code:method")], [], {"a.py::x": 0})

    result = diff_snapshots(before, after)
    assert [rec["kind"] for rec in result["removed_nodes"]] == ["code:function"]
    assert [rec["kind"] for rec in result["added_nodes"]] == ["code:method"]
    # it is present on both sides by name, so it is still eligible for community
    # comparison and, unchanged there, reports nothing
    assert result["community_changes"] == []


def test_diff_output_lists_are_sorted_and_deterministic():
    before = _snapshot([_node("a.py::a")], [], {"a.py::a": 0})
    after = _snapshot(
        [_node("a.py::c"), _node("a.py::a"), _node("a.py::b")],
        [_edge("a.py::c", "a.py::a"), _edge("a.py::b", "a.py::a")],
        {"a.py::a": 0, "a.py::b": 0, "a.py::c": 0},
    )
    first = diff_snapshots(before, after)
    second = diff_snapshots(before, after)
    assert json.dumps(first) == json.dumps(second)
    assert [rec["canonical"] for rec in first["added_nodes"]] == ["a.py::b", "a.py::c"]
    assert [(e["from"], e["to"]) for e in first["added_edges"]] == [
        ("a.py::b", "a.py::a"),
        ("a.py::c", "a.py::a"),
    ]


def test_diff_flags_snapshots_built_with_different_parameters():
    before = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    after = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    after["predicates"] = ["code:calls"]

    result = diff_snapshots(before, after)
    assert result["why"]["parameters_match"] is False
    assert any("different predicate selections" in note for note in result["why"]["notes"])
    # the honest default, when nothing else differs, is still no change reported
    assert result["summary"]["changed"] is False


def test_diff_carries_the_structural_caveat_and_both_labels():
    before = _snapshot([_node("a.py::x")], [], {"a.py::x": 0}, label="base")
    after = _snapshot([_node("a.py::x")], [], {"a.py::x": 0}, label="head")

    result = diff_snapshots(before, after)
    assert result["why"]["analysis"] == "structural"
    assert any("structural and over-approximate" in note for note in result["why"]["notes"])
    assert any("co-membership" in note for note in result["why"]["notes"])
    assert result["before"]["label"] == "base"
    assert result["after"]["label"] == "head"


def test_taken_at_is_excluded_from_every_comparison():
    before = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    after = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    after["taken_at"] = "2030-06-06T12:00:00+00:00"

    assert diff_snapshots(before, after)["summary"]["changed"] is False


# -- validation --------------------------------------------------------------


def test_load_snapshot_reads_a_valid_file(tmp_path):
    snap = _snapshot([_node("a.py::x")], [_edge("a.py::x", "a.py::x")], {"a.py::x": 0}, label="on disk")
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(snap), encoding="utf-8")

    loaded = load_snapshot(path)
    assert loaded == snap
    assert diff_snapshots(loaded, snap)["summary"]["changed"] is False


def test_load_snapshot_rejects_malformed_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(ValidationError, match="not valid JSON"):
        load_snapshot(path)


def test_load_snapshot_rejects_a_missing_file(tmp_path):
    with pytest.raises(ValidationError, match="not found"):
        load_snapshot(tmp_path / "absent.json")


def test_load_snapshot_rejects_the_wrong_kind(tmp_path):
    snap = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    snap["kind"] = "dkg.something-else"
    path = tmp_path / "kind.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    with pytest.raises(ValidationError, match="expected kind"):
        load_snapshot(path)


def test_load_snapshot_rejects_the_wrong_version(tmp_path):
    snap = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    snap["version"] = SNAPSHOT_VERSION + 1
    path = tmp_path / "version.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    with pytest.raises(ValidationError, match="expected version"):
        load_snapshot(path)


def test_load_snapshot_rejects_a_missing_required_key(tmp_path):
    snap = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    del snap["communities"]
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    with pytest.raises(ValidationError, match="missing required key 'communities'"):
        load_snapshot(path)


def test_load_snapshot_rejects_a_non_object_document(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValidationError, match="expected a snapshot object"):
        load_snapshot(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda s: s.update({"nodes": "not-a-list"}), "'nodes' must be a list"),
        (lambda s: s.update({"nodes": [{"canonical": "a"}]}), "missing required key 'kind'"),
        (lambda s: s.update({"nodes": [dict(_node("a.py::x"), language=3)]}), "language must be a string"),
        (lambda s: s.update({"edges": [{"from": "a", "to": "b", "predicate": "code:calls"}]}), "missing required key 'weight'"),
        (lambda s: s.update({"edges": [dict(_edge("a", "b"), weight="heavy")]}), "weight must be a number"),
        (lambda s: s.update({"communities": {"a.py::x": "one"}}), "must be an integer"),
        (lambda s: s.update({"communities": []}), "'communities' must be an object"),
        (lambda s: s.update({"truncated": "no"}), "'truncated' must be a boolean"),
        (lambda s: s.update({"tenant_id": ""}), "'tenant_id' must be a non-empty string"),
        (lambda s: s.update({"resolution": "one"}), "'resolution' must be a number"),
        (lambda s: s.update({"predicates": "code:calls"}), "'predicates' must be a list of strings"),
        (lambda s: s.update({"counts": {"nodes": 1}}), "'counts.edges' must be an integer"),
        (lambda s: s.update({"label": 5}), "'label' must be a string when present"),
    ],
)
def test_diff_rejects_malformed_snapshots(mutate, message):
    bad = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    mutate(bad)
    good = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    with pytest.raises(ValidationError, match=message):
        diff_snapshots(bad, good)
    with pytest.raises(ValidationError, match=message):
        diff_snapshots(good, bad)


def test_diff_names_which_side_was_malformed():
    good = _snapshot([_node("a.py::x")], [], {"a.py::x": 0})
    bad = dict(good, kind="wrong")
    with pytest.raises(ValidationError, match="^before:"):
        diff_snapshots(bad, good)
    with pytest.raises(ValidationError, match="^after:"):
        diff_snapshots(good, bad)


@requires_ts
def test_snapshot_rejects_bad_arguments(db):
    _ingest(db, [CHAIN])
    with pytest.raises(ValidationError, match="tenant_id"):
        snapshot_code_graph(db, tenant_id="")
    with pytest.raises(ValidationError, match="resolution must be a number"):
        snapshot_code_graph(db, resolution="high")
    with pytest.raises(ValidationError, match="greater than zero"):
        snapshot_code_graph(db, resolution=0)
    with pytest.raises(ValidationError, match="at least one predicate"):
        snapshot_code_graph(db, predicates=())
    with pytest.raises(ValidationError, match="label must be a string"):
        snapshot_code_graph(db, label=7)


@requires_ts
def test_snapshot_of_an_empty_graph_is_valid_and_diffs_cleanly(db):
    empty = snapshot_code_graph(db)
    assert empty["nodes"] == []
    assert empty["edges"] == []
    assert empty["communities"] == {}
    assert empty["counts"] == {"nodes": 0, "edges": 0, "communities": 0}

    _ingest(db, [CHAIN])
    populated = snapshot_code_graph(db)
    result = diff_snapshots(empty, populated)
    assert result["summary"]["added_nodes"] == len(populated["nodes"])
    assert result["summary"]["removed_nodes"] == 0
    # nothing survives from an empty graph, so there is no co-membership to compare
    assert result["community_changes"] == []


# -- read bounds added after adversarial review -----------------------------


def test_load_snapshot_confines_reads_to_a_root(tmp_path):
    """A caller that can name a path must not get a general filesystem read.

    The MCP surface is the trust boundary against an agent acting on injected
    content, so the snapshot loader takes an optional root and refuses anything
    outside it.
    """
    import json as _json

    from dkg.code.diff import load_snapshot
    from dkg.core.errors import ValidationError

    root = tmp_path / "home"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(_json.dumps({"kind": "secret", "value": "MY-SECRET-VALUE"}), encoding="utf-8")

    with pytest.raises(ValidationError) as exc:
        load_snapshot(outside, root=root)
    message = str(exc.value)
    assert "outside the permitted directory" in message
    # The refusal must not leak what is out there.
    assert "MY-SECRET-VALUE" not in message

    # Traversal out of the root is resolved before the check, so it is refused.
    with pytest.raises(ValidationError):
        load_snapshot(root / ".." / "outside.json", root=root)

    # A symlink pointing out of the root must not slip past.
    link = root / "link.json"
    try:
        link.symlink_to(outside)
    except OSError:  # pragma: no cover - platform without symlink permission
        pass
    else:
        with pytest.raises(ValidationError):
            load_snapshot(link, root=root)


def test_load_snapshot_caps_the_read_size(tmp_path):
    from dkg.code.diff import load_snapshot
    from dkg.core.errors import ValidationError

    big = tmp_path / "big.json"
    big.write_text("x" * 5000, encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        load_snapshot(big, max_bytes=1000)
    assert "over the" in str(exc.value)
    # Unbounded by default would let a named file exhaust memory.
    from dkg.code.diff import MAX_SNAPSHOT_BYTES

    assert MAX_SNAPSHOT_BYTES > 0


def test_load_snapshot_still_works_inside_the_root(db, tmp_path):
    import json as _json

    from dkg.code.diff import load_snapshot, snapshot_code_graph

    root = tmp_path / "home"
    root.mkdir()
    target = root / "snap.json"
    target.write_text(_json.dumps(snapshot_code_graph(db)), encoding="utf-8")
    loaded = load_snapshot(target, root=root)
    assert loaded["kind"] == "dkg.code-graph-snapshot"
