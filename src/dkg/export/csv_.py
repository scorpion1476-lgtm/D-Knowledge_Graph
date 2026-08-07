"""CSV export: one file per table into a target directory."""

from __future__ import annotations

import csv
from pathlib import Path

from ..core.db import Database

TABLES = ("sources", "documents", "chunks", "entities", "mentions", "claims", "relationships")


def export_csv(db: Database, out: Path, *, source_id: str | None = None) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        rows = db.fetchall(f"SELECT * FROM {table};")
        target = out / f"{table}.csv"
        with target.open("w", encoding="utf-8", newline="") as f:
            if not rows:
                continue
            fieldnames = list(rows[0].keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow(dict(r))
    return out
