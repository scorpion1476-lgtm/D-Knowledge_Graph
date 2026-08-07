import json

import pytest

from dkg.export.backup import make_backup, restore_backup
from dkg.export.csv_ import export_csv
from dkg.export.graphml import export_graphml
from dkg.export.json_ import export_json
from dkg.export.markdown import export_markdown
from dkg.ingest.base import ingest_text


def _seed(db):
    ingest_text(db, "Alpha writes about Beta. Beta is fast.", display_name="d1")


def test_json_export_roundtrip(db, tmp_path):
    _seed(db)
    out = tmp_path / "export.json"
    export_json(db, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "sources" in data and "chunks" in data
    assert len(data["chunks"]) >= 1


def test_markdown_export_writes_file(db, tmp_path):
    _seed(db)
    out = tmp_path / "export.md"
    export_markdown(db, out)
    text = out.read_text(encoding="utf-8")
    assert "# D-Knowledge_Graph export" in text


def test_csv_export_creates_files(db, tmp_path):
    _seed(db)
    out = tmp_path / "csv"
    export_csv(db, out)
    assert (out / "sources.csv").exists()


def test_graphml_export_produces_xml(db, tmp_path):
    _seed(db)
    out = tmp_path / "g.graphml"
    export_graphml(db, out)
    assert out.exists()


def test_backup_and_restore(db, tmp_path, cfg):
    _seed(db)
    archive = tmp_path / "backup.tar.gz"
    info = make_backup(db, archive)
    assert archive.exists()
    assert info["sha256"]
    new_home = tmp_path / "new_home"
    result = restore_backup(archive, new_home)
    assert (new_home / "graph.sqlite").exists()
    assert result["restored_to"] == str(new_home)


def test_restore_rejects_tampered_archive(db, tmp_path):
    import tarfile

    from dkg.core.errors import StorageError

    _seed(db)
    archive = tmp_path / "backup.tar.gz"
    make_backup(db, archive)

    # Rewrite the archive so the manifest sha256 no longer matches graph.sqlite.
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(extracted)
    # Corrupt graph.sqlite (append data so hash changes).
    with (extracted / "graph.sqlite").open("ab") as f:
        f.write(b"tampered")
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as tf:
        for name in ("graph.sqlite", "audit.log", "evidence.ledger", "manifest.json"):
            p = extracted / name
            if p.exists():
                tf.add(p, arcname=name)
    with pytest.raises(StorageError, match="hash mismatch"):
        restore_backup(tampered, tmp_path / "restored")


def test_restore_rejects_path_traversal(tmp_path):
    import tarfile

    from dkg.core.errors import StorageError

    # Craft an archive with a manifest and a member using ../ traversal.
    payload = tmp_path / "malicious.tar.gz"
    with tarfile.open(payload, "w:gz") as tf:
        info = tarfile.TarInfo(name="../evil.txt")
        data = b"escape"
        info.size = len(data)
        import io
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(StorageError, match="unsafe path"):
        restore_backup(payload, tmp_path / "home")
