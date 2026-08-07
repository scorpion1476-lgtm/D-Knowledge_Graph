"""Rendering of the pull-request review comment from an analysis report.

The comment carries six things, all of them assembled in ``report.build_review``
and merely laid out here: an overall risk level, a table of the changed symbols
ordered by risk with file and line locations and test-coverage status, the
affected execution flows ordered by criticality, the test gaps, the estimated
token saving, and the standing advisory caveat. A hidden marker sits on the
first line so the publication path can find its own comment again.

ESCAPING IS THE POINT OF THIS MODULE, so it is worth being explicit about the
threat. Every symbol name, path, and flow step in the comment came out of a
repository that, on a pull request from a fork, an attacker controls completely.
A file named ``a|b`` breaks a markdown table row in two. A class named
``<img onerror=...>`` is live HTML in a GitHub comment. A function named
``x-->`` closes the hidden marker and lets everything after it out of the
comment. A newline inside a cell injects an entire extra table row.

So the escape is an ALLOWLIST, not a blocklist: a character survives only if it
is alphanumeric or one of a handful of punctuation marks that carry no markdown
or HTML meaning. Everything else becomes a numeric character reference, which
renders as the original character and cannot be parsed as markup. Control and
format characters, which includes the ones that would reorder a line visually,
collapse to a space rather than being re-encoded. Blocklists in this position
fail by omission; an allowlist fails by being ugly.

Output is deterministic: the caller supplies lists that are already sorted with
explicit keys, and nothing here reorders or re-times anything.
"""

from __future__ import annotations

import unicodedata

# The hidden marker. A namespace plus a caller-chosen key, so two different
# reviews can each keep their own sticky comment on the same pull request.
MARKER_NAMESPACE = "dkg-pr-review"
DEFAULT_MARKER_KEY = "dkg-code-review"

# Characters that survive escaping unchanged. Everything else in a cell becomes
# a numeric character reference. Deliberately short: `_` is emphasis in
# markdown, `*` is emphasis, `[` and `(` are link syntax, `<` is HTML, `|` ends
# a table cell, `` ` `` opens code, `#` is a heading, `!` makes a link an image,
# and `\` escapes the next character. The hyphen is allowed because a cell never
# starts a line (a table row starts with a pipe) and because `-->` cannot form
# once `>` is escaped, so it can neither open a list nor close the marker. The
# semicolon and the apostrophe are allowed because neither carries markdown or
# HTML meaning in text, no rendered value is ever placed inside an HTML
# attribute, and a character reference cannot be forged out of them: `&` and `#`
# are both escaped, so a value can never produce the `&#` that would start one.
_SAFE_PUNCTUATION = " .,/:-;'"

# Per-cell source length cap, applied BEFORE escaping so the truncation can
# never land inside a character reference and leave a half-written entity.
MAX_CELL_CHARS = 200

# Rows rendered per table. A bound, not a preference: a comment is a fixed-size
# surface and GitHub rejects one over roughly 65 thousand characters.
DEFAULT_TOP = 10

_ELLIPSIS = "..."


def marker_for(key: str = DEFAULT_MARKER_KEY) -> str:
    """The hidden HTML-comment marker for one review key.

    The key is escaped like any other untrusted value, so a hostile key cannot
    close the comment early and turn the marker into visible markup.
    """
    return f"<!-- {MARKER_NAMESPACE}:{_escape_marker_key(key)} -->"


def _escape_marker_key(key: str) -> str:
    """Reduce a key to characters that cannot terminate an HTML comment."""
    cleaned = "".join(
        ch for ch in str(key or "") if ch.isascii() and (ch.isalnum() or ch in "._-")
    )
    return cleaned[:64] or DEFAULT_MARKER_KEY


def escape_cell(value: object, *, max_chars: int = MAX_CELL_CHARS) -> str:
    """Render an untrusted value as a markdown table cell that cannot escape it.

    Allowlist: alphanumerics and a few inert punctuation marks pass through.
    Everything else becomes ``&#NNN;``, which a markdown or HTML parser sees as
    text and a reader sees as the original character. Control, format, and
    line-separator characters collapse to a space instead, because re-encoding
    them would faithfully reproduce something whose only effect is to mislead
    the eye. Runs of whitespace collapse to one space, so no cell can contain a
    newline and therefore no cell can inject a row.
    """
    text = "" if value is None else str(value)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]

    pieces: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category[0] == "C" or category in ("Zl", "Zp"):
            pieces.append(" ")
            continue
        if ch.isalnum() or ch in _SAFE_PUNCTUATION:
            pieces.append(ch)
            continue
        pieces.append(f"&#{ord(ch)};")

    collapsed = " ".join("".join(pieces).split())
    if truncated:
        collapsed = f"{collapsed}{_ELLIPSIS}"
    return collapsed or "(empty)"


def _num(value: object, places: int = 4) -> str:
    """A number as a plain fixed-precision string, or an escaped fallback."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return escape_cell(value)
    if isinstance(value, int):
        return str(value)
    return f"{value:.{places}f}"


def _yes_no(value: object) -> str:
    return "yes" if value else "no"


def render_review_sections(report: dict, *, top: int = DEFAULT_TOP) -> list[str]:
    """The review body as markdown lines, without the marker or the heading.

    Shared with the plain markdown report so a reader of either surface sees the
    same tables built by the same code.
    """
    review = report.get("review") or {}
    gate = report.get("gate") or {}
    top = max(1, min(int(top), 200))

    lines: list[str] = []
    lines += _risk_section(review, gate)
    lines += _symbols_section(review, top)
    lines += _flows_section(review, top)
    lines += _gaps_section(review, top)
    lines += _saving_section(review)
    lines += _why_section(review)
    return lines


def render_pr_comment(
    report: dict, *, marker_key: str = DEFAULT_MARKER_KEY, top: int = DEFAULT_TOP
) -> str:
    """The whole sticky comment, marker first.

    The marker is the FIRST line so the publication path can validate a
    downloaded artifact by looking at its start rather than searching a body an
    attacker may have appended to.
    """
    scope = (report.get("review") or {}).get("scope") or {}
    base = scope.get("base_ref")
    header = [
        marker_for(marker_key),
        "## D-Knowledge_Graph pull-request review",
        "",
        f"- Base ref: `{escape_cell(base)}`" if base else "- Base ref: none supplied",
        f"- Changed files: {len(scope.get('changed_files') or [])}",
        "",
    ]
    body = render_review_sections(report, top=top)
    return "\n".join([*header, *body]) + "\n"


# -- sections ----------------------------------------------------------------


def _risk_section(review: dict, gate: dict) -> list[str]:
    risk = review.get("risk") or {}
    levels = risk.get("levels") or {}
    cuts = levels.get("cuts") or {}
    names = levels.get("names") or sorted(cuts)

    lines = [
        "### Overall risk",
        "",
        f"**Level: {escape_cell(risk.get('level', 'unknown'))}** "
        f"(score {_num(risk.get('score', 0.0))} of 1.0)",
        "",
        "Published thresholds for this repository:",
        "",
        "| level | opens at score |",
        "| --- | --- |",
    ]
    for name in names:
        lines.append(f"| {escape_cell(name)} | {_num(cuts.get(name, 0.0))} |")
    derivation = levels.get("derivation")
    if derivation:
        lines += ["", f"_{escape_cell(derivation, max_chars=600)}_"]

    risk_gate = gate.get("risk") or {}
    if risk_gate:
        verdict = "FAILED" if risk_gate.get("failed") else "passed"
        state = "on" if risk_gate.get("enabled") else "off (the default)"
        lines += [
            "",
            f"- Risk gate: {state}, requested at "
            f"`{escape_cell(risk_gate.get('requested', 'off'))}`, {verdict}.",
            f"- {escape_cell(risk_gate.get('why', ''), max_chars=400)}",
        ]
    impact_gate = gate.get("impact") or {}
    if impact_gate.get("enabled"):
        verdict = "FAILED" if impact_gate.get("failed") else "passed"
        lines.append(
            f"- Impact gate (deprecated): limit "
            f"{_num(impact_gate.get('requested'))}, observed "
            f"{_num(impact_gate.get('observed_count'))}, {verdict}."
        )
    return [*lines, ""]


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _symbols_section(review: dict, top: int) -> list[str]:
    # Sorted here as well as in the builder. The order is part of what the
    # requirement asks for, so the surface that renders it applies the key
    # rather than trusting whoever assembled the list.
    rows = sorted(
        review.get("changed_symbols") or [],
        key=lambda r: (-_as_float(r.get("score")), str(r.get("canonical", ""))),
    )[:top]
    lines = [
        "### Changed symbols by risk",
        "",
        "| symbol | location | risk | level | tests |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append("| (no symbol in the graph matched this change set) | - | - | - | - |")
    for row in rows:
        lines.append(
            "| "
            + escape_cell(row.get("canonical"))
            + " | "
            + escape_cell(row.get("location"))
            + " | "
            + _num(row.get("score", 0.0))
            + " | "
            + escape_cell(row.get("level"))
            + " | "
            + escape_cell(row.get("test_status"))
            + " |"
        )
    if review.get("changed_symbols_truncated"):
        lines += ["", f"_More symbols were scored than the {top} shown._"]
    return [*lines, ""]


def _flows_section(review: dict, top: int) -> list[str]:
    rows = sorted(
        review.get("flows") or [],
        key=lambda r: (-_as_float(r.get("criticality")), [str(s) for s in (r.get("path") or [])]),
    )[:top]
    lines = [
        "### Affected execution flows by criticality",
        "",
        "| criticality | depth | files | tested | flow |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append("| - | - | - | - | (no execution flow reached from the changed symbols) |")
    for row in rows:
        # Each step is escaped on its own and joined with a separator this
        # module owns, so a step containing the separator cannot forge one.
        path = " -&gt; ".join(escape_cell(step, max_chars=80) for step in (row.get("path") or []))
        lines.append(
            "| "
            + _num(row.get("criticality", 0.0))
            + " | "
            + _num(row.get("depth", 0))
            + " | "
            + _num(row.get("files_touched", 0))
            + " | "
            + _yes_no(row.get("tested"))
            + " | "
            + (path or "(empty)")
            + " |"
        )
    return [*lines, ""]


def _gaps_section(review: dict, top: int) -> list[str]:
    gaps = review.get("test_gaps") or {}
    hotspots = sorted(
        gaps.get("untested_hotspots") or [],
        key=lambda r: (-_as_float(r.get("inbound_calls")), str(r.get("canonical", ""))),
    )[:top]
    isolated = sorted(
        gaps.get("isolated_symbols") or [], key=lambda r: str(r.get("canonical", ""))
    )[:top]
    lines = [
        "### Test gaps",
        "",
        "| gap | symbol | detail |",
        "| --- | --- | --- |",
    ]
    if not hotspots and not isolated:
        lines.append("| - | (no test gap in scope) | - |")
    for row in hotspots:
        lines.append(
            "| untested hotspot | "
            + escape_cell(row.get("canonical"))
            + " | "
            + f"{_num(row.get('inbound_calls', 0))} inbound calls, no test edge |"
        )
    for row in isolated:
        lines.append(
            "| isolated symbol | "
            + escape_cell(row.get("canonical"))
            + " | no reference edge in either direction |"
        )
    why = gaps.get("why")
    if why:
        lines += ["", f"_{escape_cell(why, max_chars=600)}_"]
    return [*lines, ""]


def _saving_section(review: dict) -> list[str]:
    saving = review.get("token_saving") or {}
    lines = [
        "### Estimated token saving",
        "",
        "| measure | value |",
        "| --- | --- |",
        f"| baseline tokens, reading the files this review names | {_num(saving.get('baseline_tokens', 0))} |",
        f"| review tokens | {_num(saving.get('graph_tokens', 0))} |",
        f"| saved tokens | {_num(saving.get('saved_tokens', 0))} |",
        f"| saved percent | {_num(saving.get('saved_percent', 0.0), 2)} |",
        "",
        "_ESTIMATED, not tokenizer-measured. The baseline is the cost of reading "
        "the source files this review names, not the whole repository: a "
        "structural answer removes the need to read everything else, not the "
        "need to read the code it points at. A negative saving is reported as "
        "it is._",
        "",
    ]
    return lines


def _why_section(review: dict) -> list[str]:
    why = review.get("why") or {}
    advisory = why.get("advisory") or (
        "ADVISORY. The underlying edges are structural and over-approximate."
    )
    ordering = why.get("ordering")
    lines = ["### Why this is advisory", "", escape_cell(advisory, max_chars=1200)]
    if ordering:
        lines += ["", escape_cell(ordering, max_chars=600)]
    return [*lines, ""]
