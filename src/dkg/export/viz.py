"""Offline HTML graph visualization.

Renders the code and document graph as a single self-contained HTML file that
works fully offline: the layout is computed in Python at export time, the whole
drawing is emitted as static SVG markup, and a small original inline script adds
interaction only. There is no external script, stylesheet, font, or image, no
CDN, and no network call of any kind, which is the platform's air-gap default.

Determinism. The force-directed layout runs in Python with a fixed seed and a
bounded iteration count, and every emitted list has an explicit sort key with
ties broken by a canonical id, so the same graph always produces a
byte-identical file. Nothing about the drawing is decided in the browser, so the
picture does not move between loads and two exports can be diffed.

Interaction (R-21). An in-page search filters the drawing to matching nodes and
focuses the first match; a community legend toggles each community's nodes on
and off; node radius is scaled by degree. Community indices are labels for this
one file, produced independently per run, and must never be compared with the
indices in another run.

Accessibility (R-22). The viewer targets WCAG 2.1 level AA. Every control and
every node is reachable and operable from the keyboard and carries an accessible
name, focus is always visibly indicated, the drawn graph has a text alternative
that is present in the markup whether or not the script runs, and every colour
pair the page uses is declared in CONTRAST_PAIRS and asserted against the level's
thresholds by tests/delivery/test_viz_accessibility.py.

Security: the inlined JSON is encoded so it cannot break out of the script
element, and every label reaches the markup through esc_xml, so a hostile label
cannot inject markup.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..core.db import Database
from .graphdata import COMMUNITY_COLOURS as _COMMUNITY_COLOURS
from .graphdata import (
    DEFAULT_MAX_NODES,
    GraphData,
    community_groups,
    degree_map,
    esc_xml,
    layout_positions,
    load_graph,
    radius_for_degree,
    shape_for,
    shape_path,
)

_WIDTH = 1100
_HEIGHT = 720

# The published accessibility level this viewer targets, named here rather than
# implied, together with the contrast thresholds that level sets.
ACCESSIBILITY_LEVEL = "WCAG 2.1 level AA"
CONTRAST_MIN_TEXT = 4.5
CONTRAST_MIN_LARGE_TEXT = 3.0
CONTRAST_MIN_NON_TEXT = 3.0
CONTRAST_THRESHOLDS = {
    "text": CONTRAST_MIN_TEXT,
    "large-text": CONTRAST_MIN_LARGE_TEXT,
    "non-text": CONTRAST_MIN_NON_TEXT,
}

# Every colour the emitted page uses. One background, so a single pair per
# foreground covers it. The accessibility test asserts that no other colour
# appears anywhere in the emitted file, which is what makes this a complete
# check rather than a sample.
COLOUR_BG = "#ffffff"
COLOUR_TEXT = "#0f172a"
COLOUR_MUTED = "#475569"
COLOUR_EDGE = "#64748b"
COLOUR_BORDER = "#64748b"
COLOUR_FOCUS = "#b91c1c"
COLOUR_GLYPH = "#334155"


@dataclass(frozen=True)
class ContrastPair:
    """One foreground-on-background pair the viewer draws, and what it must meet.

    ``requirement`` selects the threshold: "text" is 4.5:1, "large-text" and
    "non-text" (graphical objects and user interface components) are 3:1.
    """

    name: str
    foreground: str
    background: str
    requirement: str

    @property
    def minimum(self) -> float:
        return CONTRAST_THRESHOLDS[self.requirement]


def _contrast_pairs() -> tuple[ContrastPair, ...]:
    pairs = [
        ContrastPair("body and heading text", COLOUR_TEXT, COLOUR_BG, "text"),
        ContrastPair("control label and button text", COLOUR_TEXT, COLOUR_BG, "text"),
        ContrastPair("node label text", COLOUR_TEXT, COLOUR_BG, "text"),
        ContrastPair("hint and status text", COLOUR_MUTED, COLOUR_BG, "text"),
        ContrastPair("relationship line", COLOUR_EDGE, COLOUR_BG, "non-text"),
        ContrastPair("control and panel border", COLOUR_BORDER, COLOUR_BG, "non-text"),
        ContrastPair("keyboard focus indicator", COLOUR_FOCUS, COLOUR_BG, "non-text"),
        ContrastPair("node outline", COLOUR_TEXT, COLOUR_BG, "non-text"),
        ContrastPair("shape key glyph", COLOUR_GLYPH, COLOUR_BG, "non-text"),
    ]
    for index, colour in enumerate(_COMMUNITY_COLOURS):
        pairs.append(ContrastPair(f"community {index + 1} node fill and legend swatch", colour, COLOUR_BG, "non-text"))
    return tuple(pairs)


CONTRAST_PAIRS: tuple[ContrastPair, ...] = _contrast_pairs()


def declared_colours() -> frozenset[str]:
    """Every colour the viewer is allowed to emit, lower case."""
    used = {COLOUR_BG}
    for pair in CONTRAST_PAIRS:
        used.add(pair.foreground.lower())
        used.add(pair.background.lower())
    return frozenset(used)


def _safe_json(obj: object) -> str:
    """JSON for safe inlining inside a <script> element."""
    raw = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    return (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def _build_nodes(g: GraphData, width: int, height: int) -> tuple[list[dict], list[dict], list[dict]]:
    """Layout, size, colour, and group every node. Returns (nodes, edges, groups)."""
    pos = layout_positions(g.nodes, g.edges, width=width, height=height)
    present = {n["id"] for n in g.nodes if n["id"] in pos}
    edges_in = [e for e in g.edges if e["source"] in present and e["target"] in present]
    degrees = degree_map(g.nodes, edges_in)
    max_degree = max(degrees.values()) if degrees else 0
    assignment, groups = community_groups(g.nodes, edges_in)

    group_label = {int(grp["index"]): str(grp["label"]) for grp in groups}
    nodes: list[dict] = []
    index_of: dict[str, int] = {}
    for n in g.nodes:
        nid = n["id"]
        if nid not in pos:
            continue
        x, y = pos[nid]
        community = int(assignment.get(nid, 0))
        degree = int(degrees.get(nid, 0))
        kind = n["kind"]
        label = n["label"]
        index_of[nid] = len(nodes)
        nodes.append(
            {
                "i": len(nodes),
                "id": nid,
                "label": label,
                "kind": kind,
                "community": community,
                "community_label": group_label.get(community, "Community 1"),
                "degree": degree,
                "x": x,
                "y": y,
                "r": radius_for_degree(degree, max_degree),
                "colour": str(groups[community]["colour"]) if groups else _COMMUNITY_COLOURS[0],
                "shape": shape_for(kind),
                "q": f"{label} {kind} {group_label.get(community, '')}".lower(),
            }
        )
    edges = [
        {"s": index_of[e["source"]], "t": index_of[e["target"]], "predicate": e["predicate"]}
        for e in edges_in
        if e["source"] in index_of and e["target"] in index_of
    ]
    edges.sort(key=lambda e: (e["s"], e["t"], e["predicate"]))
    return nodes, edges, groups


def _svg_edges(nodes: list[dict], edges: list[dict]) -> str:
    out = []
    for idx, e in enumerate(edges):
        a, b = nodes[e["s"]], nodes[e["t"]]
        out.append(
            f'<line class="edge" id="e{idx}" data-s="{e["s"]}" data-t="{e["t"]}" '
            f'x1="{a["x"]}" y1="{a["y"]}" x2="{b["x"]}" y2="{b["y"]}"></line>'
        )
    return "".join(out)


def _node_name(n: dict) -> str:
    connections = "1 connection" if n["degree"] == 1 else f"{n['degree']} connections"
    return f"{n['label']}. Kind {n['kind']}. {n['community_label']}. {connections}."


def _svg_nodes(nodes: list[dict]) -> str:
    out = []
    for n in nodes:
        tabindex = "0" if n["i"] == 0 else "-1"
        path = shape_path(n["shape"], n["x"], n["y"], n["r"])
        ring = round(float(n["r"]) + 4.0, 2)
        out.append(
            f'<g class="node" id="n{n["i"]}" data-i="{n["i"]}" role="button" tabindex="{tabindex}" '
            f'aria-label="{esc_xml(_node_name(n))}">'
            f'<circle class="ring" cx="{n["x"]}" cy="{n["y"]}" r="{ring}"></circle>'
            f'<path class="mark" d="{path}" fill="{n["colour"]}"></path>'
            f'<text class="lbl" x="{round(float(n["x"]) + float(n["r"]) + 4.0, 2)}" '
            f'y="{round(float(n["y"]) + 4.0, 2)}">{esc_xml(n["label"])}</text>'
            f"</g>"
        )
    return "".join(out)


def _legend(groups: list[dict]) -> str:
    if not groups:
        return '<p class="muted">No communities: the graph has no nodes.</p>'
    out = []
    for grp in groups:
        count = "1 node" if grp["count"] == 1 else f"{grp['count']} nodes"
        name = f"{grp['label']}, {count}"
        out.append(
            f'<button type="button" class="lg" id="lg{grp["index"]}" data-c="{grp["index"]}" '
            f'aria-pressed="true" aria-label="{esc_xml(name)}. Shown. Activate to hide.">'
            f'<span class="sw" aria-hidden="true" style="background:{grp["colour"]}"></span>'
            f'<span class="lgt">{esc_xml(name)}</span></button>'
        )
    return "".join(out)


_SHAPE_KEY_KINDS = (
    ("code:module", "square"),
    ("code:class", "diamond"),
    ("code:function", "circle"),
    ("code:method", "triangle"),
    ("code:type", "hexagon"),
    ("code:test", "pentagon"),
    ("entity", "circle"),
)


def _shape_key() -> str:
    out = []
    for kind, shape in _SHAPE_KEY_KINDS:
        path = shape_path(shape, 9.0, 9.0, 7.0)
        out.append(
            f'<li><svg class="key" viewBox="0 0 18 18" width="18" height="18" aria-hidden="true" focusable="false">'
            f'<path d="{path}" fill="{COLOUR_GLYPH}"></path></svg>{esc_xml(kind)}</li>'
        )
    return "".join(out)


_ALT_NEIGHBOUR_CAP = 20


def _text_alternative(nodes: list[dict], edges: list[dict]) -> str:
    """The graph as a list, present in the markup whether or not the script runs."""
    if not nodes:
        return "<p>The graph is empty: there is nothing drawn and nothing to describe.</p>"
    neighbours: dict[int, set[int]] = {n["i"]: set() for n in nodes}
    for e in edges:
        if e["s"] != e["t"]:
            neighbours[e["s"]].add(e["t"])
            neighbours[e["t"]].add(e["s"])
    order = sorted(nodes, key=lambda n: (n["label"], n["id"]))
    rows = []
    for n in order:
        linked = sorted(neighbours[n["i"]], key=lambda i: (nodes[i]["label"], nodes[i]["id"]))
        shown = [esc_xml(nodes[i]["label"]) for i in linked[:_ALT_NEIGHBOUR_CAP]]
        if len(linked) > _ALT_NEIGHBOUR_CAP:
            shown.append(f"and {len(linked) - _ALT_NEIGHBOUR_CAP} more")
        connects = f"Connects to {', '.join(shown)}." if shown else "No connections."
        connections = "1 connection" if n["degree"] == 1 else f"{n['degree']} connections"
        rows.append(
            f"<li><strong>{esc_xml(n['label'])}</strong> "
            f"({esc_xml(n['kind'])}, {esc_xml(n['community_label'])}, {connections}). {connects}</li>"
        )
    return '<ol class="altlist">' + "".join(rows) + "</ol>"


def _summary(nodes: list[dict], edges: list[dict], groups: list[dict], truncated: bool) -> str:
    node_word = "symbol" if len(nodes) == 1 else "symbols"
    edge_word = "relationship" if len(edges) == 1 else "relationships"
    group_word = "community" if len(groups) == 1 else "communities"
    text = (
        f"A force-directed drawing of {len(nodes)} {node_word} and {len(edges)} {edge_word}, "
        f"grouped into {len(groups)} {group_word}. Node colour shows the community, node shape shows "
        f"the symbol kind, and node size is scaled by the number of connections. The layout was "
        f"computed before this file was written, so it does not move between loads. Community numbers "
        f"are labels for this file only and cannot be compared with any other export. Community "
        f"membership is structural and advisory."
    )
    if truncated:
        text += (
            " The graph was larger than the export cap, so this drawing is a truncated subset and "
            "is not the whole graph."
        )
    return text


def render_html(g: GraphData, *, width: int = _WIDTH, height: int = _HEIGHT) -> str:
    nodes, edges, groups = _build_nodes(g, width, height)
    payload = {
        "nodes": [
            {
                "i": n["i"],
                "c": n["community"],
                "x": n["x"],
                "y": n["y"],
                "r": n["r"],
                "q": n["q"],
                "label": n["label"],
            }
            for n in nodes
        ],
        "edges": [{"s": e["s"], "t": e["t"]} for e in edges],
        "groups": [{"i": grp["index"], "count": grp["count"]} for grp in groups],
        "width": width,
        "height": height,
        "truncated": g.truncated,
    }
    count = "1 node" if len(nodes) == 1 else f"{len(nodes)} nodes"
    replacements = {
        "__CSS__": _css(),
        "__WIDTH__": str(width),
        "__HEIGHT__": str(height),
        "__LEVEL__": ACCESSIBILITY_LEVEL,
        "__SUMMARY__": esc_xml(_summary(nodes, edges, groups, g.truncated)),
        "__LEGEND__": _legend(groups),
        "__SHAPEKEY__": _shape_key(),
        "__EDGES__": _svg_edges(nodes, edges),
        "__NODES__": _svg_nodes(nodes),
        "__ALT__": _text_alternative(nodes, edges),
        "__STATUS__": f"Showing {count} of {count}.",
        "__DATA__": _safe_json(payload),
    }
    out = _TEMPLATE
    for token, value in replacements.items():
        out = out.replace(token, value)
    return out


def export_html(db: Database, out: Path, *, max_nodes: int = DEFAULT_MAX_NODES) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    g = load_graph(db, max_nodes=max_nodes)
    out.write_text(render_html(g), encoding="utf-8")
    return out


def _css() -> str:
    """Inline stylesheet, built from the same colour constants CONTRAST_PAIRS declares.

    Building it here rather than hard-coding hex in the template is what stops
    the declared pairs and the drawn page from drifting apart.
    """
    css = """
  html,body{margin:0;height:100%;font-family:sans-serif;background:__BG__;color:__TEXT__}
  h1{font-size:15px;margin:0 0 4px 0}
  h2{font-size:12px;margin:8px 0 4px 0;text-transform:none}
  p{margin:4px 0}
  .muted{color:__MUTED__;font-size:11px}
  header{padding:8px 12px;border-bottom:1px solid __BORDER__;background:__BG__}
  #panel{padding:8px 12px;border-bottom:1px solid __BORDER__;background:__BG__}
  .row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:12px}
  input[type=search]{font:inherit;font-size:12px;padding:3px 6px;border:1px solid __BORDER__;
    border-radius:3px;background:__BG__;color:__TEXT__;min-width:220px}
  button{font:inherit;font-size:12px;padding:3px 8px;border:1px solid __BORDER__;border-radius:3px;
    background:__BG__;color:__TEXT__;cursor:pointer}
  .toolbar{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
  .legend{display:flex;flex-wrap:wrap;gap:4px}
  .lg .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;
    vertical-align:middle}
  .lg[aria-pressed=false] .lgt{text-decoration:line-through}
  .lg[aria-pressed=false]{border-style:dashed}
  .shapes{list-style:none;display:flex;flex-wrap:wrap;gap:10px;margin:0;padding:0;font-size:11px;
    color:__MUTED__}
  .shapes li{display:flex;align-items:center;gap:4px}
  #wrap{position:relative;height:62vh;min-height:320px;overflow:hidden;border-bottom:1px solid __BORDER__}
  svg#g{width:100%;height:100%;cursor:grab;background:__BG__;display:block}
  svg#g.drag{cursor:grabbing}
  line.edge{stroke:__EDGE__;stroke-width:1}
  .node .mark{stroke:__TEXT__;stroke-width:.75;cursor:pointer}
  .node .lbl{font-size:11px;fill:__TEXT__;stroke:__BG__;stroke-width:3;paint-order:stroke;
    pointer-events:none;user-select:none}
  .node .ring{display:none;fill:none;stroke:__FOCUS__;stroke-width:2.5}
  .node:focus .ring{display:inline}
  .node:focus-visible .ring{display:inline}
  .node:focus{outline:3px solid __FOCUS__;outline-offset:2px}
  button:focus,input:focus,summary:focus,a:focus,[tabindex]:focus{outline:3px solid __FOCUS__;
    outline-offset:2px}
  .hint{position:absolute;right:8px;bottom:6px;font-size:11px;color:__MUTED__;background:__BG__;
    padding:1px 4px}
  details{padding:8px 12px}
  summary{font-size:12px;cursor:pointer}
  .altlist{font-size:12px;max-height:40vh;overflow:auto;margin:6px 0}
  .why{font-size:11px;color:__MUTED__;padding:0 12px 10px 12px}
"""
    tokens = {
        "__BG__": COLOUR_BG,
        "__TEXT__": COLOUR_TEXT,
        "__MUTED__": COLOUR_MUTED,
        "__EDGE__": COLOUR_EDGE,
        "__BORDER__": COLOUR_BORDER,
        "__FOCUS__": COLOUR_FOCUS,
        "__GLYPH__": COLOUR_GLYPH,
    }
    for token, value in tokens.items():
        css = css.replace(token, value)
    return css


# The template carries no external reference: no link, no src, no import, no
# font, no image, and no fetch. Every SVG element is emitted here, so the
# drawing is present with the script disabled; the script only adds interaction.
_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>D-Knowledge_Graph visualization</title>
<style>__CSS__</style>
</head>
<body>
<header>
  <h1>D-Knowledge_Graph visualization (offline)</h1>
  <p id="graph-summary">__SUMMARY__</p>
  <p class="muted">Keyboard: Tab reaches every control and the drawing. Inside the drawing, arrow keys
  move between nodes, Shift with an arrow key moves the focused node, Enter or Space centres the view
  on it, and Home and End jump to the first and last node. This page targets __LEVEL__.</p>
</header>
<div id="panel">
  <div class="row">
    <label for="q">Search nodes</label>
    <input id="q" type="search" autocomplete="off" aria-describedby="q-help">
    <button type="button" id="q-clear">Clear search</button>
    <span id="q-help" class="muted">Matches the node name, its kind, and its community. Press Enter to
    focus the first match.</span>
  </div>
  <p id="status" class="muted" role="status" aria-live="polite">__STATUS__</p>
  <div class="toolbar" role="group" aria-label="View controls">
    <button type="button" id="pan-left" aria-label="Pan left">Left</button>
    <button type="button" id="pan-right" aria-label="Pan right">Right</button>
    <button type="button" id="pan-up" aria-label="Pan up">Up</button>
    <button type="button" id="pan-down" aria-label="Pan down">Down</button>
    <button type="button" id="zoom-in" aria-label="Zoom in">Zoom in</button>
    <button type="button" id="zoom-out" aria-label="Zoom out">Zoom out</button>
    <button type="button" id="view-reset" aria-label="Reset the view to its starting position and zoom">Reset view</button>
  </div>
  <h2 id="legend-h">Communities</h2>
  <div class="legend" role="group" aria-labelledby="legend-h">__LEGEND__</div>
  <h2 id="shape-h">Shape by symbol kind</h2>
  <ul class="shapes" aria-labelledby="shape-h">__SHAPEKEY__</ul>
</div>
<div id="wrap">
  <svg id="g" viewBox="0 0 __WIDTH__ __HEIGHT__" preserveAspectRatio="xMidYMid meet"
       role="application" aria-label="Knowledge graph drawing"
       aria-describedby="graph-summary">
    <g id="root" transform="translate(0,0) scale(1)">
      <g id="edges" aria-hidden="true">__EDGES__</g>
      <g id="nodes" role="group" aria-label="Graph nodes">__NODES__</g>
    </g>
  </svg>
  <p class="hint">Drag the background to pan, wheel to zoom, drag a node to move it.</p>
</div>
<details id="alt">
  <summary>Text alternative: the graph as a list</summary>
  __ALT__
</details>
<p class="why">Why to read this cautiously: the underlying edges are structural and over-approximate,
and community membership is an advisory structural lens, not an authoritative account of meaning.</p>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
  "use strict";
  var raw=document.getElementById("data");
  if(!raw){ return; }
  var data=JSON.parse(raw.textContent);
  var svg=document.getElementById("g");
  var root=document.getElementById("root");
  var nodes=data.nodes, edges=data.edges;
  var status=document.getElementById("status");
  var search=document.getElementById("q");
  var i, n;
  var lineEls=[];
  var incident=[];
  for(i=0;i<nodes.length;i++){
    n=nodes[i];
    n.el=document.getElementById("n"+i);
    n.dx=0; n.dy=0; n.match=true; n.on=true;
    incident.push([]);
  }
  for(i=0;i<edges.length;i++){
    lineEls.push(document.getElementById("e"+i));
    incident[edges[i].s].push(i);
    if(edges[i].t!==edges[i].s){ incident[edges[i].t].push(i); }
  }
  var groupOn=[];
  for(i=0;i<data.groups.length;i++){ groupOn.push(true); }

  function px(k){ return nodes[k].x+nodes[k].dx; }
  function py(k){ return nodes[k].y+nodes[k].dy; }
  function visible(k){ var m=nodes[k]; return m.match && groupOn[m.c]!==false; }

  function moveNode(k){
    var m=nodes[k];
    m.el.setAttribute("transform","translate("+m.dx.toFixed(2)+","+m.dy.toFixed(2)+")");
    var list=incident[k], j, e, ln;
    for(j=0;j<list.length;j++){
      e=edges[list[j]]; ln=lineEls[list[j]];
      if(!ln){ continue; }
      ln.setAttribute("x1",px(e.s)); ln.setAttribute("y1",py(e.s));
      ln.setAttribute("x2",px(e.t)); ln.setAttribute("y2",py(e.t));
    }
  }

  var current=-1;
  function setTabbable(k){
    var j;
    for(j=0;j<nodes.length;j++){
      if(nodes[j].el){ nodes[j].el.setAttribute("tabindex", j===k ? "0" : "-1"); }
    }
    current=k;
  }
  function firstVisible(){
    var j;
    for(j=0;j<nodes.length;j++){ if(visible(j)){ return j; } }
    return -1;
  }

  function refresh(){
    var shown=0, j;
    for(j=0;j<nodes.length;j++){
      var vis=visible(j);
      if(vis){ shown++; }
      if(nodes[j].el){ nodes[j].el.style.display = vis ? "" : "none"; }
    }
    for(j=0;j<edges.length;j++){
      if(lineEls[j]){
        lineEls[j].style.display = (visible(edges[j].s) && visible(edges[j].t)) ? "" : "none";
      }
    }
    if(status){
      status.textContent="Showing "+shown+(shown===1?" node":" nodes")+" of "+nodes.length+
        (nodes.length===1?" node":" nodes")+".";
    }
    if(current<0 || !visible(current)){ setTabbable(firstVisible()); }
  }

  function applyFilter(text){
    var needle=String(text||"").trim().toLowerCase(), j;
    for(j=0;j<nodes.length;j++){
      nodes[j].match = needle==="" ? true : nodes[j].q.indexOf(needle)>=0;
    }
    refresh();
  }

  var view={x:0,y:0,scale:1};
  function applyView(){
    root.setAttribute("transform","translate("+view.x.toFixed(2)+","+view.y.toFixed(2)+
      ") scale("+view.scale.toFixed(4)+")");
  }
  function zoomBy(f){
    view.scale=Math.max(0.15,Math.min(6,view.scale*f));
    applyView();
  }
  function centreOn(k){
    if(k<0){ return; }
    view.x=data.width/2 - px(k)*view.scale;
    view.y=data.height/2 - py(k)*view.scale;
    applyView();
  }
  function focusNode(k){
    if(k<0){ return; }
    setTabbable(k);
    centreOn(k);
    if(nodes[k].el && nodes[k].el.focus){ nodes[k].el.focus(); }
  }

  if(search){
    search.addEventListener("input",function(){ applyFilter(search.value); });
    search.addEventListener("keydown",function(ev){
      if(ev.key==="Enter"){ ev.preventDefault(); applyFilter(search.value); focusNode(firstVisible()); }
    });
  }
  var clear=document.getElementById("q-clear");
  if(clear){
    clear.addEventListener("click",function(){
      if(search){ search.value=""; }
      applyFilter("");
      if(search){ search.focus(); }
    });
  }

  var legend=document.querySelectorAll(".lg");
  for(i=0;i<legend.length;i++){
    legend[i].addEventListener("click",function(ev){
      var btn=ev.currentTarget;
      var c=parseInt(btn.getAttribute("data-c"),10);
      var next=btn.getAttribute("aria-pressed")!=="true";
      groupOn[c]=next;
      btn.setAttribute("aria-pressed", next ? "true" : "false");
      var name=btn.querySelector(".lgt");
      var base=name ? name.textContent : "Community "+(c+1);
      btn.setAttribute("aria-label", base+(next?". Shown. Activate to hide.":". Hidden. Activate to show."));
      refresh();
    });
  }

  function bind(id,fn){
    var el=document.getElementById(id);
    if(el){ el.addEventListener("click",fn); }
  }
  var PAN=60;
  bind("pan-left",function(){ view.x+=PAN; applyView(); });
  bind("pan-right",function(){ view.x-=PAN; applyView(); });
  bind("pan-up",function(){ view.y+=PAN; applyView(); });
  bind("pan-down",function(){ view.y-=PAN; applyView(); });
  bind("zoom-in",function(){ zoomBy(1.2); });
  bind("zoom-out",function(){ zoomBy(1/1.2); });
  bind("view-reset",function(){ view.x=0; view.y=0; view.scale=1; applyView(); });

  var STEP=12;
  var layer=document.getElementById("nodes");
  if(layer){
    layer.addEventListener("keydown",function(ev){
      var target=ev.target;
      while(target && target!==layer && !(target.classList && target.classList.contains("node"))){
        target=target.parentNode;
      }
      if(!target || target===layer){ return; }
      var k=parseInt(target.getAttribute("data-i"),10);
      var key=ev.key, j;
      if(ev.shiftKey && (key==="ArrowLeft"||key==="ArrowRight"||key==="ArrowUp"||key==="ArrowDown")){
        if(key==="ArrowLeft"){ nodes[k].dx-=STEP; }
        if(key==="ArrowRight"){ nodes[k].dx+=STEP; }
        if(key==="ArrowUp"){ nodes[k].dy-=STEP; }
        if(key==="ArrowDown"){ nodes[k].dy+=STEP; }
        moveNode(k);
        ev.preventDefault();
        return;
      }
      if(key==="ArrowRight"||key==="ArrowDown"){
        for(j=k+1;j<nodes.length;j++){ if(visible(j)){ focusNode(j); break; } }
        ev.preventDefault(); return;
      }
      if(key==="ArrowLeft"||key==="ArrowUp"){
        for(j=k-1;j>=0;j--){ if(visible(j)){ focusNode(j); break; } }
        ev.preventDefault(); return;
      }
      if(key==="Home"){ focusNode(firstVisible()); ev.preventDefault(); return; }
      if(key==="End"){
        for(j=nodes.length-1;j>=0;j--){ if(visible(j)){ focusNode(j); break; } }
        ev.preventDefault(); return;
      }
      if(key==="Enter"||key===" "||key==="Spacebar"){ centreOn(k); ev.preventDefault(); }
    });
  }

  var dragNode=-1, panning=false, last=null;
  function svgPoint(ev){
    var r=svg.getBoundingClientRect();
    return {x:(ev.clientX-r.left)*(data.width/r.width), y:(ev.clientY-r.top)*(data.height/r.height)};
  }
  for(i=0;i<nodes.length;i++){
    (function(k){
      if(!nodes[k].el){ return; }
      nodes[k].el.addEventListener("mousedown",function(ev){
        dragNode=k; last=svgPoint(ev); ev.preventDefault(); ev.stopPropagation();
      });
      nodes[k].el.addEventListener("focus",function(){ current=k; });
    })(i);
  }
  svg.addEventListener("mousedown",function(ev){ panning=true; last=svgPoint(ev); svg.classList.add("drag"); });
  window.addEventListener("mousemove",function(ev){
    if(dragNode>=0){
      var p=svgPoint(ev);
      nodes[dragNode].dx+=(p.x-last.x)/view.scale;
      nodes[dragNode].dy+=(p.y-last.y)/view.scale;
      last=p; moveNode(dragNode);
    } else if(panning){
      var s=svgPoint(ev);
      view.x+=(s.x-last.x); view.y+=(s.y-last.y); last=s; applyView();
    }
  });
  window.addEventListener("mouseup",function(){ dragNode=-1; panning=false; svg.classList.remove("drag"); });
  svg.addEventListener("wheel",function(ev){
    ev.preventDefault();
    zoomBy(ev.deltaY<0?1.1:0.9);
  },{passive:false});

  applyView();
  refresh();
})();
</script>
</body>
</html>
"""
