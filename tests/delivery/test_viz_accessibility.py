"""Offline viewer accessibility, checked automatically (R-22).

The level targeted is named rather than implied: WCAG 2.1 level AA, which sets
4.5:1 for normal text, 3:1 for large text, and 3:1 for non-text contrast on
graphical objects and user interface components.

The check is original code with no new dependency. Contrast is computed here
from the WCAG relative-luminance formula, and the emitted page is parsed with
html.parser from the standard library so the assertions are about the real
markup rather than about a string the exporter happened to contain.

Two things make this a check rather than a gesture. First, every colour pair the
viewer uses is declared in viz.CONTRAST_PAIRS and asserted, and a separate test
proves no colour can appear in the page without being declared, so this is not a
sample. Second, the contrast function is itself tested against known values and
against a pair that must fail, so a checker that passed everything would be
caught.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from dkg.core.db import open_database
from dkg.export.graphdata import load_graph
from dkg.export.viz import (
    ACCESSIBILITY_LEVEL,
    CONTRAST_MIN_LARGE_TEXT,
    CONTRAST_MIN_NON_TEXT,
    CONTRAST_MIN_TEXT,
    CONTRAST_PAIRS,
    declared_colours,
    render_html,
)

# --------------------------------------------------------------------------
# The automated contrast check (original, standard library only)
# --------------------------------------------------------------------------


def _linearise(channel: float) -> float:
    """The WCAG sRGB transfer function for one channel in 0..1."""
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: str) -> float:
    text = colour.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected a six-digit hex colour, got {colour!r}")
    r, g, b = (int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * _linearise(r) + 0.7152 * _linearise(g) + 0.0722 * _linearise(b)


def contrast_ratio(foreground: str, background: str) -> float:
    a, b = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


# --------------------------------------------------------------------------
# A minimal DOM, built with the standard library
# --------------------------------------------------------------------------

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _El:
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_El] = []
        self.data: list[str] = []

    def text(self) -> str:
        out = "".join(self.data)
        for child in self.children:
            out += child.text()
        return " ".join(out.split())

    def find_all(self, tag=None, predicate=None):
        found = []
        stack = list(self.children)
        while stack:
            el = stack.pop()
            if (tag is None or el.tag == tag) and (predicate is None or predicate(el)):
                found.append(el)
            stack.extend(el.children)
        return found


class _Doc(HTMLParser):
    """Enough of a DOM to ask about roles, names, and focusability."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _El("#document", {})
        self._stack = [self.root]
        self.feed(html)
        self.close()
        self.by_id = {el.attrs["id"]: el for el in self.root.find_all() if el.attrs.get("id")}

    def handle_starttag(self, tag, attrs):
        el = _El(tag, dict(attrs), self._stack[-1])
        self._stack[-1].children.append(el)
        if tag not in _VOID:
            self._stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_El(tag, dict(attrs), self._stack[-1]))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        self._stack[-1].data.append(data)

    def find_all(self, tag=None, predicate=None):
        return self.root.find_all(tag, predicate)

    def accessible_name(self, el: _El) -> str:
        label = (el.attrs.get("aria-label") or "").strip()
        if label:
            return label
        referenced = (el.attrs.get("aria-labelledby") or "").split()
        if referenced:
            return " ".join(self.by_id[i].text() for i in referenced if i in self.by_id).strip()
        if el.tag == "input":
            for lab in self.find_all("label"):
                if lab.attrs.get("for") == el.attrs.get("id"):
                    return lab.text()
            return ""
        return el.text()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _seed(db):
    kinds = ("code:class", "code:function", "code:module", "code:method", "code:type", "code:test")
    for t in range(3):
        for i in range(3):
            eid = f"t{t}n{i}"
            db.execute(
                "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) "
                "VALUES (?,?,?,?,?,?);",
                (eid, "local", kinds[(t * 3 + i) % len(kinds)], f"pkg{t}::sym{i}", f"tri{t}sym{i}", "{}"),
            )
    db.execute(
        "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) VALUES (?,?,?,?,?,?);",
        ("zlone", "local", "entity", "lonely::alone", "lonelynode", "{}"),
    )
    n = 0
    for t in range(3):
        ids = [f"t{t}n{i}" for i in range(3)]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            db.execute(
                "INSERT INTO relationships(relationship_id, tenant_id, subject_id, predicate, object_id, "
                "support, weight, evidence_json, metadata_json) VALUES (?,?,?,?,?,?,?,?,?);",
                (f"r{n:03d}", "local", ids[a], "code:calls", ids[b], "supports", 0.9, "{}", "{}"),
            )
            n += 1


@pytest.fixture
def page(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        g = load_graph(db)
        html = render_html(g)
    return html, g


@pytest.fixture
def doc(page):
    return _Doc(page[0])


# --------------------------------------------------------------------------
# Guard the guard: the checker must be able to fail
# --------------------------------------------------------------------------


def test_contrast_function_matches_known_wcag_values():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=1e-6)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=1e-6)
    # #767676 on white is the canonical smallest grey that meets 4.5:1.
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)
    # Order must not matter.
    assert contrast_ratio("#0f172a", "#ffffff") == pytest.approx(contrast_ratio("#ffffff", "#0f172a"))


def test_contrast_checker_rejects_a_failing_pair():
    """A checker that could not fail would prove nothing about the palette."""
    # A pale border on white: nowhere near the 3:1 non-text minimum.
    assert contrast_ratio("#cbd5e1", "#ffffff") < CONTRAST_MIN_NON_TEXT
    # A mid grey that passes large text but fails normal text.
    mid = contrast_ratio("#949494", "#ffffff")
    assert mid < CONTRAST_MIN_TEXT
    assert mid >= CONTRAST_MIN_LARGE_TEXT
    # And the thresholds themselves are the published level's, not invented.
    assert (CONTRAST_MIN_TEXT, CONTRAST_MIN_LARGE_TEXT, CONTRAST_MIN_NON_TEXT) == (4.5, 3.0, 3.0)


def test_the_targeted_level_is_named_in_the_code_and_in_the_page(page):
    html, _ = page
    assert ACCESSIBILITY_LEVEL == "WCAG 2.1 level AA"
    assert ACCESSIBILITY_LEVEL in html


# --------------------------------------------------------------------------
# Contrast, over every pair the viewer actually uses
# --------------------------------------------------------------------------


def test_every_declared_pair_meets_the_level():
    assert len(CONTRAST_PAIRS) >= 10
    failures = []
    for pair in CONTRAST_PAIRS:
        ratio = contrast_ratio(pair.foreground, pair.background)
        if ratio < pair.minimum:
            failures.append(f"{pair.name}: {pair.foreground} on {pair.background} is {ratio:.2f}:1, needs {pair.minimum}:1")
    assert not failures, "colour pairs below " + ACCESSIBILITY_LEVEL + ": " + "; ".join(failures)


def test_no_colour_reaches_the_page_without_being_declared(page):
    """This is what makes the contrast check complete rather than a sample."""
    html, _ = page
    used = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}", html)}
    assert used, "no colour was found in the page at all, so the check would be vacuous"
    undeclared = used - declared_colours()
    assert not undeclared, f"the page uses colours no contrast pair covers: {sorted(undeclared)}"
    # The community palette must really be in use, not merely declared.
    community = {p.foreground.lower() for p in CONTRAST_PAIRS if p.name.startswith("community ")}
    assert len(used & community) >= 3


# --------------------------------------------------------------------------
# Keyboard reachability, roles, and accessible names
# --------------------------------------------------------------------------


def test_every_control_is_reachable_and_has_an_accessible_name(doc):
    controls = doc.find_all(predicate=lambda el: el.tag in {"button", "input", "summary"})
    assert len(controls) >= 10, "the viewer should expose search, view, and legend controls"
    for el in controls:
        assert "disabled" not in el.attrs, f"a {el.tag} is disabled and cannot be operated"
        tabindex = el.attrs.get("tabindex")
        assert tabindex is None or int(tabindex) >= 0, f"a {el.tag} is removed from the tab order"
        name = doc.accessible_name(el)
        assert name, f"a {el.tag} ({el.attrs}) has no accessible name"


def test_the_search_input_has_a_programmatic_label(doc):
    inputs = doc.find_all("input", lambda el: el.attrs.get("type") == "search")
    assert len(inputs) == 1
    field = inputs[0]
    labels = [lab for lab in doc.find_all("label") if lab.attrs.get("for") == field.attrs.get("id")]
    assert labels and labels[0].text(), "the search field has no <label for=...>"
    described = field.attrs.get("aria-describedby")
    assert described and described in doc.by_id and doc.by_id[described].text()


def test_every_node_is_a_named_keyboard_control(doc, page):
    _html, g = page
    nodes = doc.find_all("g", lambda el: "node" in (el.attrs.get("class") or "").split())
    assert len(nodes) == len(g.nodes) > 0
    tab_zero = 0
    for el in nodes:
        assert el.attrs.get("role") == "button", "a node carries no role"
        tabindex = el.attrs.get("tabindex")
        assert tabindex in {"0", "-1"}, f"a node has tabindex {tabindex!r}"
        tab_zero += tabindex == "0"
        name = (el.attrs.get("aria-label") or "").strip()
        assert name, "a node has no accessible name"
        assert "Kind" in name and "connection" in name, f"node name is not informative: {name!r}"
    # Roving tabindex: one stop in the tab order, then arrow keys inside.
    assert tab_zero == 1, f"expected exactly one node in the tab order, found {tab_zero}"


def test_keyboard_alternatives_exist_for_every_pointer_gesture(doc, page):
    html, _ = page
    for control in ("pan-left", "pan-right", "pan-up", "pan-down", "zoom-in", "zoom-out", "view-reset"):
        assert control in doc.by_id, f"{control} has no button, so that gesture is mouse-only"
        assert doc.accessible_name(doc.by_id[control])
    script = html.split('<script id="data"')[1]
    # Arrow keys move between nodes, Shift with an arrow moves a node (the
    # keyboard equivalent of dragging), Enter or Space centres the view.
    for key in ('key==="ArrowRight"', 'key==="ArrowLeft"', 'key==="Home"', 'key==="End"', 'key==="Enter"'):
        assert key in script, f"no keyboard handling for {key}"
    assert "ev.shiftKey" in script, "there is no keyboard equivalent of dragging a node"
    assert 'layer.addEventListener("keydown"' in script


def test_decorative_graphics_are_hidden_from_assistive_technology(doc):
    swatches = doc.find_all("span", lambda el: "sw" in (el.attrs.get("class") or "").split())
    assert swatches
    for el in swatches:
        assert el.attrs.get("aria-hidden") == "true"
    keys = doc.find_all("svg", lambda el: "key" in (el.attrs.get("class") or "").split())
    assert keys
    for el in keys:
        assert el.attrs.get("aria-hidden") == "true"
    edges = doc.by_id.get("edges")
    assert edges is not None and edges.attrs.get("aria-hidden") == "true"


# --------------------------------------------------------------------------
# Text alternative for the drawn graph
# --------------------------------------------------------------------------


def test_the_drawing_has_a_described_by_target(doc):
    svgs = doc.find_all("svg", lambda el: el.attrs.get("id") == "g")
    assert len(svgs) == 1
    drawing = svgs[0]
    assert drawing.attrs.get("role") == "application"
    assert (drawing.attrs.get("aria-label") or "").strip()
    described = drawing.attrs.get("aria-describedby")
    assert described, "the drawing has no description"
    for ref in described.split():
        assert ref in doc.by_id, f"aria-describedby points at a missing id {ref!r}"
        assert len(doc.by_id[ref].text()) > 40, "the description says almost nothing"


def test_the_graph_has_a_text_alternative_listing_every_node(doc, page):
    _html, g = page
    details = doc.find_all("details")
    assert len(details) == 1
    assert details[0].find_all("summary"), "the text alternative has no summary to open it"
    items = details[0].find_all("li")
    assert len(items) == len(g.nodes), "the text alternative does not cover every node"
    listed = " ".join(item.text() for item in items)
    for node in g.nodes:
        assert node["label"] in listed, f"{node['label']!r} is missing from the text alternative"
        assert node["kind"] in listed
    assert "Connects to" in listed, "the text alternative does not convey the relationships"


def test_the_text_alternative_is_in_the_markup_not_built_by_script(page):
    """A reader with scripting off still gets the graph, drawing and all."""
    html, g = page
    body = html.split('<script id="data"')[0]
    assert "<details" in body
    # "<li><strong>" is the text-alternative row; the shape key uses plain <li>.
    assert body.count("<li><strong>") == len(g.nodes)
    # The drawing itself is static markup too, not created at load time.
    assert body.count('class="node"') == len(g.nodes)
    assert "createElementNS" not in html, "the drawing must not be constructed by script"


def test_the_page_declares_a_language_and_a_title(doc, page):
    html, _ = page
    assert '<html lang="en">' in html
    titles = doc.find_all("title")
    assert titles and titles[0].text()
    headings = doc.find_all("h1")
    assert len(headings) == 1 and headings[0].text()


# --------------------------------------------------------------------------
# Visible focus indicator
# --------------------------------------------------------------------------


def test_focus_is_always_visibly_indicated(doc, page):
    html, _ = page
    styles = doc.find_all("style")
    assert len(styles) == 1
    css = styles[0].text()
    tight = "".join(css.split())
    spaced = " ".join(css.split())
    focus_colour = next(p.foreground for p in CONTRAST_PAIRS if p.name == "keyboard focus indicator")
    assert f"outline:3pxsolid{focus_colour}" in tight, "no visible focus outline in the declared focus colour"
    # Controls and nodes both get an indicator, and nodes get a drawn ring as
    # well because an outline on an SVG group is not universally rendered.
    for selector in ("button:focus", "input:focus", "summary:focus", ".node:focus"):
        assert selector in tight, f"{selector} has no focus style"
    assert ".node:focus .ring{display:inline}" in spaced
    assert html.count('class="ring"') >= 1
    assert "outline:none" not in tight, "a focus indicator is being suppressed"
