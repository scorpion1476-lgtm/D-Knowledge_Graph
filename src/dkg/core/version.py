"""Version compatibility check.

The database records the D-Knowledge_Graph version that created and last opened
it. A newer application refuses to open a database written by an incompatible
future major version rather than silently mis-migrating.
"""

from __future__ import annotations

from dataclasses import dataclass

from dkg import __version__ as APP_VERSION

from .db import Database
from .errors import ConfigError


@dataclass
class VersionInfo:
    app: str
    schema_major: int


CURRENT_SCHEMA_MAJOR = 1


def record_open(db: Database) -> VersionInfo:
    row = db.fetchone("SELECT value FROM meta WHERE key = 'schema_major';")
    if row is None:
        db.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_major', ?);",
            (str(CURRENT_SCHEMA_MAJOR),),
        )
        schema_major = CURRENT_SCHEMA_MAJOR
    else:
        schema_major = int(row["value"])
        if schema_major > CURRENT_SCHEMA_MAJOR:
            raise ConfigError(
                f"database was written by a newer major schema "
                f"({schema_major}) than this build supports "
                f"({CURRENT_SCHEMA_MAJOR}); upgrade the application"
            )
    db.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('last_open_app_version', ?);",
        (APP_VERSION,),
    )
    return VersionInfo(app=APP_VERSION, schema_major=schema_major)
