"""R-13: watching ONE repository, with no registry and no multi-repo daemon.

The requirement is specific about the independence: a single-repository watch
must not require registering the repository first, and must not require running
the multi-repository daemon. Before this existed, `dkg daemon` was the only
watcher, so watching one directory meant writing a registry file into the DKG
home, which is a side effect nobody asked for and which could collide with a
registry the user already maintained.

What is deliberately NOT re-implemented is the watching itself. A second
watcher beside the tested one would mean two code paths where only one is
exercised, so this shares WatchDaemon and hands it a registry that holds one
entry and never touches disk. These tests therefore concentrate on the two
things that are actually new: that nothing is persisted, and that the command
stops cleanly.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from dkg.core.errors import ValidationError
from dkg.watch.registry import REGISTRY_FILENAME, Registry, TransientRegistry

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    return root


# -- the transient registry ---------------------------------------------------


def test_it_holds_the_one_repository_it_was_given(tmp_path):
    repo = _repo(tmp_path / "proj")

    reg = TransientRegistry.for_repo(repo)

    assert len(reg) == 1
    assert reg.list()[0].path == str(repo.resolve())
    assert reg.list()[0].name == "proj"


def test_a_name_can_be_given_explicitly(tmp_path):
    repo = _repo(tmp_path / "proj")

    reg = TransientRegistry.for_repo(repo, name="custom")

    assert reg.list()[0].name == "custom"


def test_it_writes_no_file_anywhere(tmp_path):
    """The whole point: watching one directory persists nothing."""
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path / "proj")

    reg = TransientRegistry.for_repo(repo)
    reg.add("second", repo)  # even a mutation must not persist

    assert list(home.iterdir()) == []
    assert not (home / REGISTRY_FILENAME).exists()
    assert reg.path is None


def test_it_does_not_disturb_an_existing_registry(tmp_path):
    """A user's real registry must survive a single-repository watch untouched."""
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path / "proj")
    other = _repo(tmp_path / "other")

    persistent = Registry.in_home(home)
    persistent.add("kept", other)
    before = (home / REGISTRY_FILENAME).read_text(encoding="utf-8")

    TransientRegistry.for_repo(repo).add("ephemeral", repo)

    after = (home / REGISTRY_FILENAME).read_text(encoding="utf-8")
    assert after == before
    assert [e.name for e in Registry.in_home(home).list()] == ["kept"]


def test_a_missing_path_is_refused_with_a_reason(tmp_path):
    with pytest.raises(ValidationError, match="does not exist"):
        TransientRegistry.for_repo(tmp_path / "nope")


def test_a_file_is_not_a_repository(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="not a directory"):
        TransientRegistry.for_repo(target)


# -- the daemon over one repository -------------------------------------------


def test_one_pass_ingests_the_repository(tmp_path, cfg):
    repo = _repo(tmp_path / "proj")
    calls: list[str] = []

    from dkg.watch.daemon import WatchDaemon

    daemon = WatchDaemon(
        cfg.db_path,
        TransientRegistry.for_repo(repo),
        reingest_fn=lambda db, path, audit: (calls.append(path) or {"mode": "stub", "parsed_files": 1}),
        use_watchfiles=False,
    )

    results = daemon.poll_once()

    assert calls == [str(repo.resolve())]
    assert results[repo.name]["changed"] is True


def test_a_second_pass_with_no_change_does_nothing(tmp_path, cfg):
    repo = _repo(tmp_path / "proj")
    calls: list[str] = []

    from dkg.watch.daemon import WatchDaemon

    daemon = WatchDaemon(
        cfg.db_path,
        TransientRegistry.for_repo(repo),
        reingest_fn=lambda db, path, audit: (calls.append(path) or {"mode": "stub"}),
        use_watchfiles=False,
    )
    daemon.poll_once()

    results = daemon.poll_once()

    assert len(calls) == 1, "an unchanged repository must not be re-ingested"
    assert results[repo.name]["changed"] is False


def test_a_change_triggers_exactly_one_more_ingest(tmp_path, cfg):
    repo = _repo(tmp_path / "proj")
    calls: list[str] = []

    from dkg.watch.daemon import WatchDaemon

    daemon = WatchDaemon(
        cfg.db_path,
        TransientRegistry.for_repo(repo),
        reingest_fn=lambda db, path, audit: (calls.append(path) or {"mode": "stub"}),
        use_watchfiles=False,
    )
    daemon.poll_once()

    (repo / "lib.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    daemon.poll_once()

    assert len(calls) == 2


def test_it_stops_cleanly_with_no_orphaned_thread(tmp_path, cfg):
    """The requirement says "and stops cleanly". A leaked thread is not that."""
    repo = _repo(tmp_path / "proj")
    before = threading.active_count()

    from dkg.watch.daemon import WatchDaemon

    daemon = WatchDaemon(
        cfg.db_path,
        TransientRegistry.for_repo(repo),
        reingest_fn=lambda db, path, audit: {"mode": "stub"},
        poll_interval=0.02,
        use_watchfiles=False,
    )
    daemon.run(max_seconds=0.15)

    assert daemon.is_running() is False
    assert threading.active_count() == before, "a watcher thread outlived the run"


# -- the command ---------------------------------------------------------------


@requires_ts
def test_the_command_runs_one_pass_and_reports_json(tmp_path, monkeypatch, capsys):
    from dkg.cli.entry import main

    home = tmp_path / "home"
    repo = _repo(tmp_path / "proj")
    monkeypatch.setenv("DKG_HOME", str(home))

    assert main(["init"]) == 0
    capsys.readouterr()

    assert main(["watch", "--repo", str(repo), "--once", "--poll"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["repo"] == str(repo.resolve())
    assert payload["backend"] == "polling"
    assert payload["health"]["healthy"] is True


@requires_ts
def test_the_command_leaves_no_registry_behind(tmp_path, monkeypatch, capsys):
    from dkg.cli.entry import main

    home = tmp_path / "home"
    repo = _repo(tmp_path / "proj")
    monkeypatch.setenv("DKG_HOME", str(home))
    assert main(["init"]) == 0
    capsys.readouterr()

    main(["watch", "--repo", str(repo), "--once", "--poll"])
    capsys.readouterr()

    assert not (home / REGISTRY_FILENAME).exists(), (
        "watching one repository must not register it"
    )


@requires_ts
def test_the_command_needs_no_registered_repository(tmp_path, monkeypatch, capsys):
    """`dkg daemon` refuses with an empty registry. `dkg watch` must not."""
    from dkg.cli.entry import main

    home = tmp_path / "home"
    repo = _repo(tmp_path / "proj")
    monkeypatch.setenv("DKG_HOME", str(home))
    assert main(["init"]) == 0
    capsys.readouterr()

    # The multi-repository daemon refuses outright with an empty registry.
    assert main(["daemon", "--once"]) != 0
    assert "no repositories registered" in capsys.readouterr().err

    # The single-repository watch does not care that the registry is empty.
    assert main(["watch", "--repo", str(repo), "--once", "--poll"]) == 0


@requires_ts
def test_a_bounded_run_terminates(tmp_path, monkeypatch, capsys):
    from dkg.cli.entry import main

    home = tmp_path / "home"
    repo = _repo(tmp_path / "proj")
    monkeypatch.setenv("DKG_HOME", str(home))
    assert main(["init"]) == 0
    capsys.readouterr()

    assert main(["--json", "watch", "--repo", str(repo), "--max-seconds", "0.2", "--poll"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False
