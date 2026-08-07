"""Standard-library RSS 2.0 and Atom 1.0 parser.

Uses ``xml.etree.ElementTree`` with entity expansion disabled. Returns a
uniform list of ``FeedEntry`` records regardless of feed dialect. This
lets D-Knowledge_Graph parse RSS and Atom without the ``feedparser``
extra, while still keeping the extra available for hosts that want its
extended dialect coverage.

Security notes:
- Parses with ``xml.etree.ElementTree`` and rejects any DOCTYPE
  declaration or external entity marker in the raw bytes before parsing
  (see ``_reject_dangerous_xml``), which blocks XXE and billion-laughs.
- Never dereferences external entities.
- Does no network I/O; the caller provides the bytes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ..core.errors import IngestError, SecurityError

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"

_DTD_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_ENTITY_RE = re.compile(rb"<!ENTITY", re.IGNORECASE)


@dataclass
class FeedEntry:
    id: str
    title: str
    summary: str
    link: str = ""
    published: str = ""
    updated: str = ""
    authors: list[str] = field(default_factory=list)


@dataclass
class ParsedFeed:
    dialect: str  # "rss2" | "atom10" | "unknown"
    title: str
    link: str
    entries: list[FeedEntry]


def _reject_dangerous_xml(data: bytes) -> None:
    if _DTD_RE.search(data):
        raise SecurityError("XML input contains a DOCTYPE declaration")
    if _ENTITY_RE.search(data):
        raise SecurityError("XML input contains an ENTITY declaration")


def parse_feed(data: bytes) -> ParsedFeed:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise IngestError("feed data must be non-empty bytes")
    _reject_dangerous_xml(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise IngestError(f"XML parse failed: {e}") from e
    tag = root.tag.lower()
    if tag == "rss":
        return _parse_rss2(root)
    if tag == _ATOM_NS.lower() + "feed" or tag == "feed":
        return _parse_atom(root)
    # Some feeds root at rdf:RDF (RSS 1.0). Treat conservatively.
    if tag.endswith("rdf"):
        return _parse_rss2(root)
    raise IngestError(f"unknown feed root element: {root.tag!r}")


def _parse_rss2(root: ET.Element) -> ParsedFeed:
    channel = root.find("channel")
    if channel is None:
        channel = root
    title = _text(channel.findtext("title"))
    link = _text(channel.findtext("link"))
    entries: list[FeedEntry] = []
    for item in _iter(channel, "item"):
        eid = _text(item.findtext("guid")) or _text(item.findtext("link"))
        entries.append(
            FeedEntry(
                id=eid,
                title=_text(item.findtext("title")),
                summary=_text(item.findtext("description")),
                link=_text(item.findtext("link")),
                published=_text(item.findtext("pubDate")),
                updated=_text(item.findtext(_DC_NS + "date")),
                authors=[a for a in [_text(item.findtext("author"))] if a],
            )
        )
    return ParsedFeed(dialect="rss2", title=title, link=link, entries=entries)


def _parse_atom(root: ET.Element) -> ParsedFeed:
    title = _text(root.findtext(_ATOM_NS + "title"))
    link = ""
    for link_el in root.findall(_ATOM_NS + "link"):
        rel = link_el.get("rel", "alternate")
        if rel == "alternate":
            link = link_el.get("href", "")
            break
    entries: list[FeedEntry] = []
    for entry in _iter(root, _ATOM_NS + "entry"):
        entry_link = ""
        for link_el in entry.findall(_ATOM_NS + "link"):
            if link_el.get("rel", "alternate") == "alternate":
                entry_link = link_el.get("href", "")
                break
        authors = [
            _text(a.findtext(_ATOM_NS + "name"))
            for a in entry.findall(_ATOM_NS + "author")
        ]
        entries.append(
            FeedEntry(
                id=_text(entry.findtext(_ATOM_NS + "id")) or entry_link,
                title=_text(entry.findtext(_ATOM_NS + "title")),
                summary=_text(
                    entry.findtext(_ATOM_NS + "summary")
                    or entry.findtext(_ATOM_NS + "content")
                ),
                link=entry_link,
                published=_text(entry.findtext(_ATOM_NS + "published")),
                updated=_text(entry.findtext(_ATOM_NS + "updated")),
                authors=[a for a in authors if a],
            )
        )
    return ParsedFeed(dialect="atom10", title=title, link=link, entries=entries)


def _iter(parent: ET.Element, tag: str) -> Iterator[ET.Element]:
    yield from parent.findall(tag)


def _text(v: str | None) -> str:
    return (v or "").strip()
