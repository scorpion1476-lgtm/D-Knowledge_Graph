import pytest

from dkg.core.db import open_database
from dkg.core.errors import ConfigError
from dkg.core.version import CURRENT_SCHEMA_MAJOR, record_open


def test_record_open_sets_schema_major(tmp_path):
    with open_database(tmp_path / "graph.sqlite") as db:
        vi = record_open(db)
        assert vi.schema_major == CURRENT_SCHEMA_MAJOR


def test_future_schema_rejected(tmp_path):
    with open_database(tmp_path / "graph.sqlite") as db:
        db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_major', ?);",
            (str(CURRENT_SCHEMA_MAJOR + 1),),
        )
        with pytest.raises(ConfigError):
            record_open(db)
