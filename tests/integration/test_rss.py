import pytest


def test_rss_denied_when_network_disabled(db, cfg):
    from dkg.core.errors import PolicyError
    from dkg.ingest.rss import ingest_feed

    with pytest.raises(PolicyError):
        ingest_feed(db, "https://example.com/feed", cfg=cfg)


def test_rss_stdlib_parser_no_extra_required(db, cfg):
    """The default RSS parser is stdlib-only. No feedparser needed."""
    from dkg.ingest.rss_stdlib import parse_feed

    body = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>Nothing</title>
      <item><title>A</title><description>a</description><link>x</link></item>
    </channel></rss>"""
    p = parse_feed(body)
    assert p.dialect == "rss2"
    assert p.entries and p.entries[0].title == "A"


def test_rss_feedparser_backend_only_when_installed(db, cfg):
    from dkg.core.errors import AdapterUnavailableError
    from dkg.ingest.rss import ingest_feed

    cfg.network.allow_outbound = True
    try:
        import feedparser  # noqa: F401

        pytest.skip("feedparser installed; nothing to test on this host")
    except ImportError:
        pass
    with pytest.raises(AdapterUnavailableError):
        ingest_feed(db, "https://example.com/feed", cfg=cfg, prefer="feedparser")
