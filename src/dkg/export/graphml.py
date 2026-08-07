"""GraphML export using xml.etree.ElementTree.

The subset written here is compatible with common graph tools that read the
GraphML spec at http://graphml.graphdrawing.org/ ; it declares one node key
(``label``) and one edge key (``predicate``).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

from ..core.db import Database


def export_graphml(db: Database, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    root = Element(
        "graphml",
        attrib={
            "xmlns": "http://graphml.graphdrawing.org/xmlns",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:schemaLocation": (
                "http://graphml.graphdrawing.org/xmlns "
                "http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd"
            ),
        },
    )
    _key(root, "node", "label", "string")
    _key(root, "node", "kind", "string")
    _key(root, "edge", "predicate", "string")
    _key(root, "edge", "support", "string")

    graph = SubElement(root, "graph", attrib={"id": "dkg", "edgedefault": "directed"})

    for e in db.fetchall("SELECT entity_id, kind, display FROM entities;"):
        n = SubElement(graph, "node", attrib={"id": e["entity_id"]})
        _data(n, "label", e["display"])
        _data(n, "kind", e["kind"])

    for i, r in enumerate(db.fetchall(
        "SELECT relationship_id, subject_id, object_id, predicate, support FROM relationships;"
    )):
        edge = SubElement(
            graph,
            "edge",
            attrib={
                "id": r["relationship_id"] or f"edge_{i}",
                "source": r["subject_id"],
                "target": r["object_id"],
            },
        )
        _data(edge, "predicate", r["predicate"])
        _data(edge, "support", r["support"] or "supports")

    ElementTree(root).write(str(out), encoding="utf-8", xml_declaration=True)
    return out


def _key(root: Element, for_: str, name: str, typ: str) -> None:
    SubElement(
        root,
        "key",
        attrib={"id": name, "for": for_, "attr.name": name, "attr.type": typ},
    )


def _data(parent: Element, name: str, value: str) -> None:
    d = SubElement(parent, "data", attrib={"key": name})
    d.text = value
