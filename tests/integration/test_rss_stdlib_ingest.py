
from dkg.ingest.rss_stdlib import parse_feed


def test_stdlib_rss_ingest_end_to_end(db, cfg):
    """Feed body is provided directly; skip network call."""
    body = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>DKG feed</title>
      <link>https://example.com</link>
      <item>
        <title>Alpha released</title>
        <link>https://example.com/a</link>
        <description>Alpha is fast.</description>
      </item>
      <item>
        <title>Beta released</title>
        <link>https://example.com/b</link>
        <description>Beta is small.</description>
      </item>
    </channel></rss>"""
    p = parse_feed(body)
    from dkg.ingest.base import ingest_text

    for e in p.entries:
        ingest_text(db, text=f"# {e.title}\n\n{e.summary}", display_name=e.title, kind="feed-entry")
    row = db.fetchone("SELECT COUNT(*) AS n FROM documents;")
    assert row["n"] == 2
