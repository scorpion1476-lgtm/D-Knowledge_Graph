import pytest

from dkg.core.errors import IngestError, SecurityError
from dkg.ingest.rss_stdlib import parse_feed

RSS2 = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <link>https://example.com</link>
  <item>
    <title>Entry one</title>
    <link>https://example.com/1</link>
    <description>First body</description>
    <guid>id-1</guid>
    <pubDate>Wed, 30 Jul 2026 08:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Entry two</title>
    <link>https://example.com/2</link>
    <description>Second body</description>
  </item>
</channel></rss>"""


ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <link href="https://example.com" rel="alternate"/>
  <entry>
    <id>urn:test:1</id>
    <title>Alpha</title>
    <link rel="alternate" href="https://example.com/a"/>
    <summary>alpha body</summary>
    <updated>2026-07-30T08:00:00Z</updated>
    <author><name>Ada</name></author>
  </entry>
</feed>"""


def test_parse_rss2():
    p = parse_feed(RSS2)
    assert p.dialect == "rss2"
    assert p.title == "Test Feed"
    assert len(p.entries) == 2
    assert p.entries[0].id == "id-1"
    assert p.entries[0].link == "https://example.com/1"


def test_parse_atom():
    p = parse_feed(ATOM)
    assert p.dialect == "atom10"
    assert p.title == "Atom Feed"
    assert p.entries[0].id == "urn:test:1"
    assert p.entries[0].authors == ["Ada"]


def test_rejects_doctype():
    bad = b'<?xml version="1.0"?><!DOCTYPE rss><rss version="2.0"><channel></channel></rss>'
    with pytest.raises(SecurityError):
        parse_feed(bad)


def test_rejects_entity_declaration():
    bad = b'<?xml version="1.0"?><!ENTITY x "y"><rss version="2.0"><channel></channel></rss>'
    with pytest.raises(SecurityError):
        parse_feed(bad)


def test_empty_input():
    with pytest.raises(IngestError):
        parse_feed(b"")


def test_unknown_root():
    with pytest.raises(IngestError):
        parse_feed(b"<foo></foo>")
