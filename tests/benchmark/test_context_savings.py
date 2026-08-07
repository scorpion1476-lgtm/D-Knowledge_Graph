"""U-13: the estimated context-savings record on the four result surfaces.

The properties this suite holds are the ones that make the number honest: the
breakdown sums exactly, the baseline is the files the answer names rather than
the whole repository, an unreadable file is not counted as free, the record says
ESTIMATED, and the cross-check publishes the calibration error rather than
silently replacing the estimate.
"""

from __future__ import annotations

import pytest

from dkg.context.savings import (
    RECORD_KEY,
    attach_savings,
    paths_in,
    savings_record,
)
from dkg.context.tokens import estimate_tokens, tokenizer_available

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _repo(root):
    files = {
        "lib.py": "def helper():\n" + "".join(f"    x{i} = {i}\n" for i in range(60)) + "    return 1\n",
        "app.py": "from lib import helper\n\n\ndef run():\n    return helper()\n",
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    return files


# -- path extraction ----------------------------------------------------------


def test_paths_are_read_from_explicit_fields_and_from_canonical_names():
    payload = {
        "impacted": [{"canonical": "src/a.py::thing"}, {"path": "src/b.py"}],
        "nested": {"deeper": [{"canonical": "src/c.py::x"}]},
    }

    assert paths_in(payload) == ["src/a.py", "src/b.py", "src/c.py"]


def test_a_bare_symbol_name_is_not_mistaken_for_a_path():
    assert paths_in({"canonical": "GET /users"}) == []
    assert paths_in({"name": "some name with spaces"}) == []


# -- the record ---------------------------------------------------------------


def test_the_breakdown_sums_exactly_to_the_graph_cost(tmp_path):
    _repo(tmp_path)
    payload = {
        "impacted": [{"canonical": "lib.py::helper"} for _ in range(10)],
        "totals": {"count": 10},
        "why": {"note": "structural and over-approximate"},
    }

    record = savings_record(payload, repo_root=tmp_path)

    assert sum(c["tokens"] for c in record["breakdown"]) == record["graph_tokens"]
    assert any(c["category"] == "serialisation" for c in record["breakdown"])
    assert {c["category"] for c in record["breakdown"]} >= {"impacted", "totals", "why"}


def test_the_baseline_is_the_files_the_answer_names(tmp_path):
    files = _repo(tmp_path)
    payload = {"impacted": [{"canonical": "lib.py::helper"}]}

    record = savings_record(payload, repo_root=tmp_path)

    assert record["baseline_files"] == ["lib.py"]
    assert record["baseline_tokens"] == estimate_tokens(files["lib.py"])
    # app.py is in the repository and is NOT in the baseline, because the
    # answer does not name it.
    assert "app.py" not in record["baseline_files"]


def test_the_record_says_it_is_estimated(tmp_path):
    _repo(tmp_path)

    record = savings_record({"a": 1}, repo_root=tmp_path)

    assert record["estimated"] is True
    assert "not a tokenizer" in record["estimator"]
    assert "ESTIMATED" in record["why"]["labelled"]


def test_an_unreadable_file_is_excluded_and_reported_not_counted_as_free(tmp_path):
    _repo(tmp_path)
    payload = {"impacted": [{"canonical": "lib.py::helper"}, {"canonical": "gone.py::missing"}]}

    record = savings_record(payload, repo_root=tmp_path)

    assert record["baseline_files"] == ["lib.py"]
    excluded = {e["path"]: e["reason"] for e in record["baseline_files_excluded"]}
    assert "gone.py" in excluded
    assert "not a readable file" in excluded["gone.py"]


def test_a_path_escaping_the_root_is_refused(tmp_path):
    inner = tmp_path / "repo"
    inner.mkdir()
    _repo(inner)
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    payload = {"impacted": [{"path": "../outside.py"}]}

    record = savings_record(payload, repo_root=inner)

    assert record["baseline_files"] == []
    assert any("escapes" in e["reason"] for e in record["baseline_files_excluded"])


def test_the_saving_is_the_difference_and_the_percentage_agrees(tmp_path):
    _repo(tmp_path)
    payload = {"impacted": [{"canonical": "lib.py::helper"}]}

    record = savings_record(payload, repo_root=tmp_path)

    assert record["saved_tokens"] == record["baseline_tokens"] - record["graph_tokens"]
    expected = round(100.0 * record["saved_tokens"] / record["baseline_tokens"], 2)
    assert record["saved_percent"] == expected
    assert record["saved_tokens"] > 0, "a one-line answer about a 60-line file should save"


def test_a_negative_saving_is_reported_rather_than_floored(tmp_path):
    """A large answer about a tiny file costs more than reading it."""
    (tmp_path / "tiny.py").write_text("x = 1\n", encoding="utf-8")
    payload = {
        "impacted": [{"canonical": "tiny.py::x", "detail": "y" * 400} for _ in range(20)],
    }

    record = savings_record(payload, repo_root=tmp_path)

    assert record["saved_tokens"] < 0
    assert record["saved_percent"] < 0
    assert "reported as it is" in record["why"]["negative_saving"]


def test_a_result_naming_no_file_has_a_zero_baseline_and_does_not_divide_by_it(tmp_path):
    record = savings_record({"totals": {"n": 0}}, repo_root=tmp_path)

    assert record["baseline_tokens"] == 0
    assert record["saved_percent"] == 0.0


# -- the cross-check ----------------------------------------------------------


def test_the_cross_check_is_off_by_default(tmp_path):
    _repo(tmp_path)

    record = savings_record({"impacted": [{"canonical": "lib.py::helper"}]}, repo_root=tmp_path)

    assert record["cross_check"] is None


def test_the_cross_check_publishes_the_calibration_error(tmp_path):
    _repo(tmp_path)
    payload = {"impacted": [{"canonical": "lib.py::helper"}]}

    record = savings_record(payload, repo_root=tmp_path, verify=True)
    check = record["cross_check"]

    assert check is not None
    if not tokenizer_available():
        assert check["ran"] is False
        assert "nothing to calibrate" in check["reason"]
        return
    assert check["ran"] is True
    assert check["tokenizer"]
    assert isinstance(check["calibration_error"]["baseline_percent"], float)
    assert isinstance(check["calibration_error"]["graph_percent"], float)
    assert "Published rather" in check["calibration_error"]["note"]


def test_the_cross_check_never_replaces_the_estimate(tmp_path):
    _repo(tmp_path)
    payload = {"impacted": [{"canonical": "lib.py::helper"}]}

    plain = savings_record(dict(payload), repo_root=tmp_path)
    verified = savings_record(dict(payload), repo_root=tmp_path, verify=True)

    assert verified["baseline_tokens"] == plain["baseline_tokens"]
    assert verified["graph_tokens"] == plain["graph_tokens"]
    assert verified["estimated"] is True


# -- attachment ---------------------------------------------------------------


def test_attaching_can_be_declined(tmp_path):
    payload = {"a": 1}

    assert RECORD_KEY not in attach_savings(payload, repo_root=tmp_path, enabled=False)
    assert RECORD_KEY in attach_savings(payload, repo_root=tmp_path, enabled=True)


# -- the four surfaces --------------------------------------------------------


@requires_ts
def test_the_mcp_impact_review_change_and_architecture_results_carry_a_record(db, tmp_path):
    from dkg.mcp.tools import build_read_registry

    files = _repo(tmp_path)
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, files, source_uri=f"code://{tmp_path}")

    registry = build_read_registry(db, code_root=tmp_path)
    for name, args in (
        ("dkg.code.impact", {"file": "lib.py"}),
        ("dkg.code.questions", {}),
        ("dkg.code.change", {}),
        ("dkg.code.architecture", {}),
    ):
        result = registry.call(name, args)
        assert RECORD_KEY in result, name
        record = result[RECORD_KEY]
        assert record["estimated"] is True, name
        assert sum(c["tokens"] for c in record["breakdown"]) == record["graph_tokens"], name


@requires_ts
def test_the_mcp_record_can_be_declined_per_call(db, tmp_path):
    from dkg.mcp.tools import build_read_registry

    files = _repo(tmp_path)
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, files, source_uri=f"code://{tmp_path}")
    registry = build_read_registry(db, code_root=tmp_path)

    result = registry.call("dkg.code.impact", {"file": "lib.py", "context_savings": False})

    assert RECORD_KEY not in result


@requires_ts
def test_the_cli_impact_and_questions_results_carry_a_record(db, tmp_path, capsys, monkeypatch):
    import json

    from dkg.cli.entry import main

    files = _repo(tmp_path)
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, files, source_uri=f"code://{tmp_path}")
    # The db fixture's home IS tmp_path/.dkg, so the CLI opens the same
    # database the graph was written into.
    monkeypatch.setenv("DKG_HOME", str(tmp_path / ".dkg"))

    assert main(["code-impact", "--file", "lib.py", "--repo", str(tmp_path)]) == 0
    impact = json.loads(capsys.readouterr().out)
    assert RECORD_KEY in impact
    assert impact[RECORD_KEY]["estimated"] is True

    assert main(["code-questions"]) == 0
    questions = json.loads(capsys.readouterr().out)
    assert RECORD_KEY in questions


@requires_ts
def test_the_cli_record_can_be_declined(db, tmp_path, capsys, monkeypatch):
    import json

    from dkg.cli.entry import main

    files = _repo(tmp_path)
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, files, source_uri=f"code://{tmp_path}")
    monkeypatch.setenv("DKG_HOME", str(tmp_path / ".dkg"))

    assert main(["--no-savings", "code-impact", "--file", "lib.py", "--repo", str(tmp_path)]) == 0

    assert RECORD_KEY not in json.loads(capsys.readouterr().out)
