"""End-to-end offline behaviour test.

Runs a full workflow (ingest, search, graph, export, backup, restore, audit
verify) without touching the network. Any attempt to hit outbound HTTP would
fail because network.allow_outbound is False by default.
"""

from __future__ import annotations

import pytest

from dkg.agents.base import Task
from dkg.agents.coordinator import Coordinator
from dkg.core.audit import AuditEntry, AuditLog
from dkg.export.backup import make_backup, restore_backup
from dkg.export.json_ import export_json
from dkg.graph.query import neighbourhood
from dkg.ingest.base import ingest_text


def test_offline_end_to_end(db, cfg, tmp_path):
    ingest_text(db, "Alpha discovered Beta. Beta is fast. Gamma is not fast.", display_name="d1")
    ingest_text(db, "Alpha writes about Beta. Delta contradicts Alpha.", display_name="d2")

    # search
    coord = Coordinator(db, cfg=cfg)
    r = coord.run_parallel([Task(kind="research.gather", input={"query": "beta"})])[0]
    assert r["ok"]

    # graph
    n = neighbourhood(db, "beta", depth=1)
    assert isinstance(n["nodes"], list)

    # export
    export_json(db, tmp_path / "out.json")
    assert (tmp_path / "out.json").exists()

    # backup + restore
    make_backup(db, tmp_path / "b.tar.gz")
    restore_backup(tmp_path / "b.tar.gz", tmp_path / "new_home")
    assert (tmp_path / "new_home" / "graph.sqlite").exists()

    # audit
    log = AuditLog(db, cfg.audit_path)
    log.record(AuditEntry(action="status.get", outcome="ok"))
    ok, _ = log.verify_chain()
    assert ok


def test_offline_outbound_network_denied(db, cfg):
    # In offline mode (allow_outbound defaults to False), submitting a task
    # that requires outbound network raises PolicyError before any socket is
    # opened.
    from dkg.core.errors import PolicyError

    coord = Coordinator(db, cfg=cfg)
    with pytest.raises(PolicyError):
        coord.submit(Task(kind="ingest.web", input={"url": "https://example.invalid/"}))


def test_offline_restore_rejects_missing_manifest(tmp_path):
    # Offline restore path: a tar.gz without a manifest.json is refused,
    # preventing partial restores of corrupted backups.
    import tarfile

    from dkg.core.errors import StorageError

    archive = tmp_path / "empty.tar.gz"
    with tarfile.open(archive, "w:gz"):
        # Empty archive - no manifest.
        pass
    with pytest.raises(StorageError, match="manifest missing"):
        restore_backup(archive, tmp_path / "home")
