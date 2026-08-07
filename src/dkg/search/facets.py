"""Faceted counts by source, entity kind, and ingest date buckets."""

from __future__ import annotations

from typing import Literal

from ..core.db import Database

DateGrain = Literal["day", "week", "month", "year"]


def facet_by_date(
    db: Database,
    *,
    grain: DateGrain = "day",
    tenant_id: str = "local",
    limit: int = 500,
) -> list[dict]:
    """Return document counts bucketed by ingest date at the requested grain."""
    if grain == "day":
        fmt = "%Y-%m-%d"
    elif grain == "week":
        # ISO week: YYYY-Www
        fmt = "%Y-W%W"
    elif grain == "month":
        fmt = "%Y-%m"
    elif grain == "year":
        fmt = "%Y"
    else:
        raise ValueError(f"unknown date grain: {grain!r}")
    rows = db.fetchall(
        """
        SELECT strftime(?, ingested_at) AS bucket, COUNT(*) AS n
        FROM documents
        WHERE tenant_id=?
        GROUP BY bucket
        ORDER BY bucket ASC
        LIMIT ?;
        """,
        (fmt, tenant_id, int(limit)),
    )
    return [{"bucket": r["bucket"], "count": int(r["n"])} for r in rows]


def facet_by_entity_kind(db: Database, *, tenant_id: str = "local") -> list[dict]:
    rows = db.fetchall(
        "SELECT kind, COUNT(*) AS n FROM entities WHERE tenant_id=? GROUP BY kind ORDER BY n DESC;",
        (tenant_id,),
    )
    return [{"kind": r["kind"], "count": int(r["n"])} for r in rows]


def facet_by_source_kind(db: Database, *, tenant_id: str = "local") -> list[dict]:
    rows = db.fetchall(
        "SELECT kind, COUNT(*) AS n FROM sources WHERE tenant_id=? GROUP BY kind ORDER BY n DESC;",
        (tenant_id,),
    )
    return [{"kind": r["kind"], "count": int(r["n"])} for r in rows]
