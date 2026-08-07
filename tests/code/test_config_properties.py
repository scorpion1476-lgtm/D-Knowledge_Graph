"""V-04: configuration keys as graph nodes, with the value never stored.

The security property is the one that matters most here, so it is tested from
the outside: fixtures carry distinctive secret-looking values, and the tests
assert those strings appear nowhere in the database at all, not merely that the
node's name is the key.
"""

from __future__ import annotations

import pytest

from dkg.code.config_keys import (
    EDGE_CONFIGURES,
    KIND_CONFIG,
    extract_keys,
    find_bindings,
    is_config_file,
    parse_config_file,
)

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

# Every value here is distinctive so a leak into the database is unmistakable.
SECRET = "s3cr3t-tripwire-value-9f2a"
DB_URL = f"postgres://user:{SECRET}@localhost/app"


def _dump(db) -> str:
    """EVERY column of EVERY table, enumerated from the schema.

    Naming a handful of tables would only prove the value is absent from the
    ones this test happened to think of, which is exactly how a leak into an
    unexpected column survives. The table list comes from sqlite_master so a
    table added later is covered without anyone remembering to add it here.
    """
    tables = [
        r["name"]
        for r in db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
    ]
    chunks: list[str] = []
    for table in tables:
        for row in db.fetchall(f"SELECT * FROM {table};"):
            for value in tuple(row):
                if isinstance(value, bytes):
                    chunks.append(value.decode("utf-8", errors="replace"))
                elif value is not None:
                    chunks.append(str(value))
    return "\n".join(chunks)


# -- file classification ------------------------------------------------------


def test_configuration_files_are_recognised_by_name_and_extension():
    assert is_config_file(".env")
    assert is_config_file(".env.production")
    assert is_config_file("conf/app.properties")
    assert is_config_file("settings.yaml")
    assert is_config_file("config.toml")


def test_a_package_manifest_is_not_application_configuration():
    """Otherwise the graph fills up with dependency names."""
    assert not is_config_file("package.json")
    assert not is_config_file("pyproject.toml")
    assert not is_config_file("tsconfig.json")
    assert not is_config_file("Cargo.toml")
    assert not is_config_file("src/main.py")


# -- key extraction, per format ----------------------------------------------


def test_env_keys_are_extracted_and_values_are_not():
    keys = extract_keys(".env", f"# comment\nexport DATABASE_URL={DB_URL}\nAPI_TOKEN={SECRET}\n")

    assert keys == ["DATABASE_URL", "API_TOKEN"]
    assert SECRET not in "".join(keys)


def test_properties_keys_are_extracted():
    keys = extract_keys("app.properties", f"# c\napp.name=demo\napp.secret={SECRET}\n")

    assert keys == ["app.name", "app.secret"]


def test_ini_keys_are_prefixed_with_their_section():
    keys = extract_keys("app.ini", f"[database]\nurl={DB_URL}\n[server]\nport=8080\n")

    assert keys == ["database.url", "server.port"]


def test_toml_keys_are_prefixed_with_their_table():
    keys = extract_keys("app.toml", f'[auth]\ntoken = "{SECRET}"\nmode = "strict"\n')

    assert keys == ["auth.token", "auth.mode"]


def test_yaml_keys_are_flattened_by_indentation():
    keys = extract_keys("app.yaml", f"database:\n  url: {DB_URL}\n  pool: 5\nlogging:\n  level: info\n")

    assert keys == ["database", "database.url", "database.pool", "logging", "logging.level"]


def test_json_keys_are_flattened_with_dots():
    keys = extract_keys("app.json", f'{{"auth": {{"token": "{SECRET}"}}, "port": 80}}')

    assert keys == ["auth", "auth.token", "port"]


def test_no_extracted_key_ever_carries_a_value():
    for path, text in (
        (".env", f"K={SECRET}\n"),
        ("a.properties", f"k={SECRET}\n"),
        ("a.ini", f"[s]\nk={SECRET}\n"),
        ("a.toml", f'k = "{SECRET}"\n'),
        ("a.yaml", f"k: {SECRET}\n"),
        ("a.json", f'{{"k": "{SECRET}"}}'),
    ):
        assert SECRET not in " ".join(extract_keys(path, text)), path


# -- the parsed file ----------------------------------------------------------


def test_config_symbols_carry_the_key_and_empty_text():
    parsed = parse_config_file(".env", f"DATABASE_URL={DB_URL}\n")

    config_symbols = [s for s in parsed.symbols if s.kind == KIND_CONFIG]
    assert [s.name for s in config_symbols] == ["DATABASE_URL"]
    assert config_symbols[0].qualified == ".env::config:DATABASE_URL"
    # Empty text is what keeps the value out of the chunk table.
    assert all(s.text == "" for s in parsed.symbols)


# -- binding detection --------------------------------------------------------


def test_bindings_are_found_across_the_documented_forms():
    assert find_bindings('os.environ["DATABASE_URL"]') == {"DATABASE_URL"}
    assert find_bindings('os.getenv("API_TOKEN")') == {"API_TOKEN"}
    assert find_bindings('config.get("app.name")') == {"app.name"}
    assert find_bindings("process.env.NODE_ENV") == {"NODE_ENV"}
    assert find_bindings('process.env["NODE_ENV"]') == {"NODE_ENV"}
    assert find_bindings('os.Getenv("PORT")') == {"PORT"}
    assert find_bindings('@Value("${app.name}")') == {"app.name"}
    assert find_bindings("env('APP_KEY')") == {"APP_KEY"}


def test_source_with_no_binding_finds_nothing():
    assert find_bindings("def f():\n    return 1\n") == set()


# -- end to end ---------------------------------------------------------------


@requires_ts
def test_a_config_key_becomes_a_node_linked_to_the_code_that_reads_it(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / ".env").write_text(f"DATABASE_URL={DB_URL}\nUNUSED_KEY=plain\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'import os\n\n\ndef connect():\n    return os.environ["DATABASE_URL"]\n',
        encoding="utf-8",
    )

    ingest_repo(db, tmp_path)

    node = db.fetchone(
        "SELECT canonical, display FROM entities WHERE kind=? AND display=?;",
        ("code:config", "DATABASE_URL"),
    )
    assert node is not None, "the key must become a node"
    assert node["canonical"] == ".env::config:DATABASE_URL"

    edge = db.fetchone(
        "SELECT o.canonical AS target FROM relationships r "
        "JOIN entities s ON s.entity_id = r.subject_id "
        "JOIN entities o ON o.entity_id = r.object_id "
        "WHERE r.predicate=? AND s.canonical=?;",
        (f"code:{EDGE_CONFIGURES}", ".env::config:DATABASE_URL"),
    )
    assert edge is not None, "the key must be linked to the code that binds it"
    assert edge["target"] == "app.py::connect"


@requires_ts
def test_the_value_appears_nowhere_in_the_database(db, tmp_path):
    """The security property, tested from the outside."""
    from dkg.code.ingest import ingest_repo

    (tmp_path / ".env").write_text(f"DATABASE_URL={DB_URL}\nAPI_TOKEN={SECRET}\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(f"auth:\n  token: {SECRET}\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'import os\n\n\ndef connect():\n    return os.environ["DATABASE_URL"]\n',
        encoding="utf-8",
    )

    ingest_repo(db, tmp_path)

    dumped = _dump(db)
    assert SECRET not in dumped, "a configuration value reached the database"
    assert DB_URL not in dumped
    assert "DATABASE_URL" in dumped, "but the key itself is indexed"
    # Guard the guard: a dump that cannot see a value proves nothing, so a
    # deliberate leak into an arbitrary column has to be visible to it.
    db.execute(
        "INSERT INTO sources(source_id, tenant_id, kind, uri, display_name, added_at) "
        "VALUES (?,?,?,?,?,?);",
        ("leak-probe", "local", "probe", "probe://x", SECRET, "2026-08-06T00:00:00Z"),
    )
    assert SECRET in _dump(db), "the dump must be able to see a value at all"


@requires_ts
def test_a_key_nothing_reads_is_still_a_node_with_no_binding(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / ".env").write_text("ORPHAN_KEY=whatever\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    ingest_repo(db, tmp_path)

    node = db.fetchone(
        "SELECT entity_id FROM entities WHERE kind='code:config' AND display='ORPHAN_KEY';"
    )
    assert node is not None
    edges = db.fetchone(
        "SELECT COUNT(*) AS n FROM relationships WHERE subject_id=? AND predicate=?;",
        (node["entity_id"], f"code:{EDGE_CONFIGURES}"),
    )
    assert edges["n"] == 0


@requires_ts
def test_a_repository_with_no_configuration_produces_no_config_nodes(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    ingest_repo(db, tmp_path)

    assert db.fetchone("SELECT COUNT(*) AS n FROM entities WHERE kind='code:config';")["n"] == 0
