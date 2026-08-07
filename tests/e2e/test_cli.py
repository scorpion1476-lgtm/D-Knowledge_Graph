import json

import pytest

from dkg.cli.entry import main


def _run(argv, capsys):
    rc = main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_help_runs(capsys):
    rc, out, err = _run(["help"], capsys)
    assert rc == 0


def test_status_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DKG_HOME", str(tmp_path))
    rc, out, err = _run(["--json", "status"], capsys)
    assert rc == 0
    payload = json.loads(out)
    assert payload["app_version"]
    # A fresh home has empty tables; the count queries in _cmd_status must
    # narrow the Row result and return integer zeros (not crash or return None).
    for key in ("documents", "chunks", "entities", "claims"):
        assert payload[key] == 0
        assert isinstance(payload[key], int)


def test_ingest_and_search(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DKG_HOME", str(tmp_path))
    f = tmp_path / "doc.md"
    f.write_text("# hello\n\nAlpha is fast. Beta reports gains.", encoding="utf-8")
    _run(["ingest", str(f)], capsys)
    rc, out, _ = _run(["--json", "search", "alpha", "--mode", "hybrid"], capsys)
    assert rc == 0
    payload = json.loads(out)
    assert payload["query"] == "alpha"


def test_capabilities_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DKG_HOME", str(tmp_path))
    rc, out, _ = _run(["--json", "capabilities"], capsys)
    assert rc == 0
    payload = json.loads(out)
    assert "capabilities" in payload


def test_unknown_command_returns_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DKG_HOME", str(tmp_path))
    # argparse raises SystemExit with a non-zero status for an invalid command.
    with pytest.raises(SystemExit) as info:
        _run(["definitely-not-a-command"], capsys)
    assert info.value.code != 0


def test_ingest_missing_file_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DKG_HOME", str(tmp_path))
    missing = tmp_path / "does_not_exist.md"
    rc, out, err = _run(["ingest", str(missing)], capsys)
    assert rc != 0


def test_search_empty_query_rejected_or_returns_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DKG_HOME", str(tmp_path))
    rc, out, err = _run(["--json", "search", "", "--mode", "hybrid"], capsys)
    # Either a graceful empty result (rc==0 with empty results) or a rejection
    # (rc != 0). Both are acceptable failure-path outcomes for empty input.
    if rc == 0:
        payload = json.loads(out)
        assert payload.get("results", []) == []
