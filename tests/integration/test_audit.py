from dkg.core.audit import AuditEntry, AuditLog


def test_audit_chain_verifies(db, cfg):
    log = AuditLog(db, cfg.audit_path)
    log.record(AuditEntry(action="status.get", outcome="ok"))
    log.record(AuditEntry(action="search.hybrid", outcome="ok", details={"query": "hi"}))
    log.record(AuditEntry(action="ingest.file", outcome="ok"))
    ok, break_at = log.verify_chain()
    assert ok
    assert break_at is None


def test_audit_chain_detects_tampering(db, cfg):
    log = AuditLog(db, cfg.audit_path)
    log.record(AuditEntry(action="a", outcome="ok"))
    log.record(AuditEntry(action="b", outcome="ok"))
    # Tamper: rewrite the action field of the first row.
    db.execute("UPDATE audit_log SET action = 'MODIFIED' WHERE action = 'a';")
    ok, break_at = log.verify_chain()
    assert not ok
    assert break_at is not None


def test_audit_journal_written(db, cfg):
    log = AuditLog(db, cfg.audit_path)
    log.record(AuditEntry(action="status.get", outcome="ok"))
    assert cfg.audit_path.exists()
    lines = cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1


def test_audit_chain_verify_flags_row_mismatch(db, cfg):
    log = AuditLog(db, cfg.audit_path)
    log.record(AuditEntry(action="first", outcome="ok"))
    log.record(AuditEntry(action="second", outcome="ok"))
    # Rewrite the outcome of the first row, breaking its recorded hash.
    db.execute("UPDATE audit_log SET outcome='rejected' WHERE action='first';")
    ok, break_at = log.verify_chain()
    assert not ok
    assert break_at is not None


def test_audit_journal_missing_when_no_writes(db, cfg):
    # Fresh log with no records: chain verifies trivially and journal file
    # is absent (never created without a write).
    log = AuditLog(db, cfg.audit_path)
    ok, break_at = log.verify_chain()
    assert ok
    assert break_at is None
    assert not cfg.audit_path.exists()
