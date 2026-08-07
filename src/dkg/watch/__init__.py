"""Multi-repo registry and local watch daemon (delivery plane).

A registry of repositories or corpora plus a bounded, non-interactive local
daemon that re-ingests incrementally when a registered repository changes. The
core works without this: the daemon is only started on demand, uses the optional
``watch`` extra (watchfiles) when present, and otherwise falls back to a
standard-library polling watcher.
"""
