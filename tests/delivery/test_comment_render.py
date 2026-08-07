"""The rendered pull-request review comment (R-16).

Six required contents, checked one by one: an overall risk level, a table of
changed symbols ordered by risk with file and line locations and test-coverage
status, the affected execution flows ordered by criticality, the test gaps, the
estimated token saving, and escaping strong enough that a hostile identifier
cannot inject markup.

The escaping tests use identifiers a fork author could genuinely create: a name
carrying a table pipe, an HTML tag, a script tag, backticks, a newline, link and
image syntax, and an HTML-comment terminator that would otherwise close the
hidden marker. Every one of them is asserted not to escape its cell.

Dependency-free: the renderer takes a plain dictionary, so this runs in the base
environment with no code extra and no network.
"""

from __future__ import annotations

import re

from dkg.code.pr_comment import (
    DEFAULT_MARKER_KEY,
    escape_cell,
    marker_for,
    render_pr_comment,
)

# Each of these is a separate injection technique, kept separate so a failure
# names the one that got through.
HOSTILE_PIECES = {
    "table pipe": "a|b",
    "html tag": "<b onmouseover=alert(1)>x</b>",
    "script tag": "<script>alert('pwn')</script>",
    "backticks": "`rm -rf /`",
    "newline": "line one\nline two",
    "carriage return": "one\rtwo",
    "link syntax": "[click](https://evil.example/steal)",
    "image syntax": "![img](https://evil.example/track.gif)",
    "comment terminator": "--> visible text <!--",
    "emphasis": "**bold** _italic_",
    "heading": "\n# injected heading",
    "table row": "\n| pwned | pwned | pwned | pwned | pwned |",
    "html entity": "&lt;script&gt;",
    "backslash": "back\\slash",
}
HOSTILE = "".join(HOSTILE_PIECES.values())


def _review(*, symbol="app.py::run", path="app.py"):
    return {
        "scope": {
            "base_ref": "0123456789abcdef",
            "changed_files": [path],
            "changed_symbol_count": 2,
            "note": "the change set is the source files git reports changed",
        },
        "risk": {
            "level": "elevated",
            "score": 0.6123,
            "levels": {
                "names": ["low", "moderate", "elevated", "high"],
                "cuts": {"low": 0.0, "moderate": 0.2, "elevated": 0.5, "high": 0.8},
                "derivation": "nearest-rank percentiles of this graph's own score distribution",
            },
            "weights": {"caller_count": 0.25},
        },
        # Deliberately OUT of risk order, so a renderer that merely echoed the
        # input order would fail the ordering assertion below.
        "changed_symbols": [
            {
                "canonical": "lib.py::helper",
                "kind": "code:function",
                "path": "lib.py",
                "start_line": 3,
                "end_line": 9,
                "location": "lib.py:3-9",
                "score": 0.2011,
                "level": "moderate",
                "tested": True,
                "test_status": "test edge present",
                "callers": 1,
                "entry_points_reaching": 0,
            },
            {
                "canonical": symbol,
                "kind": "code:function",
                "path": path,
                "start_line": 12,
                "end_line": 40,
                "location": f"{path}:12-40",
                "score": 0.6123,
                "level": "elevated",
                "tested": False,
                "test_status": "no test edge",
                "callers": 4,
                "entry_points_reaching": 2,
            },
        ],
        "changed_symbols_truncated": False,
        # Also out of criticality order.
        "flows": [
            {
                "entry": symbol,
                "path": [symbol, "lib.py::helper"],
                "criticality": 0.3102,
                "depth": 1,
                "peak_fan_in": 1,
                "files_touched": 2,
                "tested": True,
            },
            {
                "entry": symbol,
                "path": [symbol, "lib.py::helper", "lib.py::inner"],
                "criticality": 0.7204,
                "depth": 2,
                "peak_fan_in": 4,
                "files_touched": 2,
                "tested": False,
            },
        ],
        "test_gaps": {
            "scoped_to_change_set": True,
            "untested_hotspots": [
                {"canonical": "lib.py::helper", "path": "lib.py", "inbound_calls": 2},
                {"canonical": symbol, "path": path, "inbound_calls": 7},
            ],
            "isolated_symbols": [{"canonical": "dead.py::orphan", "path": "dead.py"}],
            "repository_summary": {"total_symbols": 12},
            "thresholds": {"inbound_calls_min": 2},
            "why": "the absence of a test edge is not proof the symbol is untested at runtime",
        },
        "token_saving": {
            "estimated": True,
            "baseline_tokens": 1200,
            "graph_tokens": 431,
            "saved_tokens": 769,
            "saved_percent": 64.08,
        },
        "why": {
            "advisory": "ADVISORY. The underlying edges are structural and over-approximate.",
            "ordering": "changed symbols by descending risk score then canonical name",
        },
    }


def _report(*, symbol="app.py::run", path="app.py", gate=None):
    return {
        "repo": "/tmp/example",
        "generated_at": "2026-08-07T00:00:00+00:00",
        "summary": {"files": 2, "total_symbols": 4, "total_edges": 3, "symbols_by_kind": {}, "edges_by_predicate": {}},
        "impact": None,
        "review": _review(symbol=symbol, path=path),
        "gate": gate
        or {
            "risk": {
                "requested": "off",
                "enabled": False,
                "observed_level": "elevated",
                "observed_score": 0.6123,
                "cuts": {"low": 0.0, "moderate": 0.2, "elevated": 0.5, "high": 0.8},
                "derivation": "nearest-rank percentiles",
                "failed": False,
                "why": "the risk gate is off by default; the score above is reported anyway",
            },
            "impact": {
                "requested": None,
                "enabled": False,
                "observed_count": None,
                "failed": False,
                "deprecated": True,
                "why": "deprecated",
            },
            "failed": False,
        },
    }


def _table_rows(text: str, heading: str) -> list[str]:
    """Every `|`-delimited line under one heading, header rows included."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    rows = []
    for line in lines[start + 1 :]:
        if line.startswith("###"):
            break
        if line.startswith("|"):
            rows.append(line)
    return rows


# -- the six required contents -----------------------------------------------


def test_comment_reports_an_overall_risk_level_and_its_thresholds():
    out = render_pr_comment(_report())
    assert "### Overall risk" in out
    assert "**Level: elevated**" in out
    assert "0.6123" in out
    # The thresholds behind the level are published in the comment itself.
    threshold_rows = _table_rows(out, "### Overall risk")
    body = [r for r in threshold_rows if not r.startswith("| ---") and "opens at" not in r]
    assert [r.split("|")[1].strip() for r in body] == ["low", "moderate", "elevated", "high"]
    assert "0.8000" in out  # the high cut


def test_changed_symbol_table_has_locations_coverage_and_risk_order():
    out = render_pr_comment(_report())
    rows = _table_rows(out, "### Changed symbols by risk")
    body = rows[2:]  # drop the header and the separator
    assert len(body) == 2
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in body]
    # Ordered by descending risk, not by the order they were handed over.
    assert cells[0][0] == "app.py::run"
    assert cells[1][0] == "lib.py::helper"
    # File AND line locations.
    assert cells[0][1] == "app.py:12-40"
    assert cells[1][1] == "lib.py:3-9"
    # Test-coverage status.
    assert cells[0][4] == "no test edge"
    assert cells[1][4] == "test edge present"


def test_flows_are_reported_ordered_by_criticality():
    out = render_pr_comment(_report())
    rows = _table_rows(out, "### Affected execution flows by criticality")
    body = rows[2:]
    assert len(body) == 2
    scores = [float(r.strip("|").split("|")[0].strip()) for r in body]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 0.7204
    assert "app.py::run -&gt; lib.py::helper -&gt; lib.py::inner" in out


def test_test_gaps_are_reported():
    out = render_pr_comment(_report())
    assert "### Test gaps" in out
    rows = _table_rows(out, "### Test gaps")
    body = rows[2:]
    kinds = [r.strip("|").split("|")[0].strip() for r in body]
    assert kinds == ["untested hotspot", "untested hotspot", "isolated symbol"]
    # Heaviest inbound pressure first.
    names = [r.strip("|").split("|")[1].strip() for r in body]
    assert names[0] == "app.py::run"


def test_estimated_token_saving_is_reported_and_labelled_estimated():
    out = render_pr_comment(_report())
    assert "### Estimated token saving" in out
    assert "769" in out
    assert "64.08" in out
    assert "ESTIMATED, not tokenizer-measured" in out


def test_comment_carries_exactly_one_marker_on_its_first_line():
    out = render_pr_comment(_report())
    marker = marker_for(DEFAULT_MARKER_KEY)
    assert out.split("\n")[0] == marker
    assert out.count(marker) == 1
    assert out.count("<!--") == 1
    assert out.count("-->") == 1


# -- escaping ----------------------------------------------------------------


def test_hostile_symbol_and_path_cannot_break_out_of_their_cell():
    out = render_pr_comment(_report(symbol=HOSTILE, path=HOSTILE))
    rows = _table_rows(out, "### Changed symbols by risk")
    body = rows[2:]
    # A newline or a pipe that survived would have added rows or columns.
    assert len(body) == 2, f"a hostile identifier changed the row count: {body}"
    for row in body:
        assert row.count("|") == 6, f"a hostile identifier changed the column count: {row}"
    # The hostile text is present as escaped text, not as its raw form.
    assert HOSTILE not in out
    assert "<script>" not in out
    assert "</script>" not in out
    assert "<b onmouseover" not in out


def test_hostile_identifier_cannot_inject_markup_anywhere_in_the_comment():
    out = render_pr_comment(_report(symbol=HOSTILE, path=HOSTILE))
    lowered = out.lower()
    for needle in ("<script", "<iframe", "<img ", "<b ", "</b>"):
        assert needle not in lowered, f"raw markup survived: {needle}"
    # Stronger than a blocklist: the ONLY angle brackets a rendered comment may
    # contain are the two in the hidden marker.
    without_marker = out.replace(marker_for(DEFAULT_MARKER_KEY), "", 1)
    assert "<" not in without_marker
    assert ">" not in without_marker
    # Link and image syntax cannot survive: `[` and `(` are both escaped.
    assert "](http" not in out
    assert "![" not in out
    # The hidden marker cannot be closed early by a symbol name.
    assert out.count("-->") == 1
    assert out.count("<!--") == 1


def test_every_hostile_piece_is_neutralised_individually():
    # A numeric character reference legitimately contains `&`, `#`, and `;`, so
    # the references are removed before the check: what must not survive is a
    # dangerous character OUTSIDE a reference, where a parser would act on it.
    reference = re.compile(r"&#\d{1,7};")
    # The stripper must remove references and nothing else, or the checks below
    # would pass on an empty string.
    assert reference.sub("", escape_cell("plain text 42")) == "plain text 42"
    for label, piece in HOSTILE_PIECES.items():
        cell = escape_cell(piece)
        residue = reference.sub("", cell)
        for dangerous in "\n\r|<>`[]()*_#!\\&":
            assert dangerous not in residue, f"{label}: {dangerous!r} survived in {cell!r}"


def test_escape_bounds_a_very_long_identifier():
    cell = escape_cell("A" * 10_000)
    # The cap applies to the SOURCE, so no character reference can be cut in
    # half; the escaped result is bounded too.
    assert len(cell) <= 250
    assert cell.endswith("...")


def test_control_and_format_characters_collapse_rather_than_being_re_encoded():
    # A right-to-left override reproduces a display attack faithfully if it is
    # merely turned into a character reference, so it collapses to a space
    # instead. Written as escapes so this source file carries no bidirectional
    # control character of its own.
    cell = escape_cell("safe\u202edetrevni\u0000end\u2028tail")
    assert "\u202e" not in cell
    assert "&#8238;" not in cell
    assert "\u0000" not in cell
    assert "\u2028" not in cell
    assert cell == "safe detrevni end tail"


def test_marker_key_cannot_close_the_marker():
    marker = marker_for("evil --> <script>alert(1)</script> <!--")
    assert marker.count("-->") == 1
    assert marker.count("<!--") == 1
    assert "<script" not in marker


# -- determinism -------------------------------------------------------------


def test_render_is_deterministic_for_the_same_report():
    report = _report()
    assert render_pr_comment(report) == render_pr_comment(report)


def test_gate_verdict_is_shown_when_the_gate_is_on():
    gate = _report()["gate"]
    gate["risk"].update({"requested": "elevated", "enabled": True, "failed": True})
    gate["failed"] = True
    out = render_pr_comment(_report(gate=gate))
    assert re.search(r"Risk gate: on, requested at `elevated`, FAILED", out)
