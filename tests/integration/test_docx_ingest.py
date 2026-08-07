import zipfile

from dkg.ingest.base import ingest_path

_DOC = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Alpha writes about Beta.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Beta is fast and reliable.</w:t></w:r></w:p>
  </w:body>
</w:document>"""


def test_ingest_docx_via_stdlib(db, tmp_path):
    p = tmp_path / "note.docx"
    with zipfile.ZipFile(str(p), "w") as zf:
        zf.writestr("word/document.xml", _DOC.encode("utf-8"))
    r = ingest_path(db, p)
    assert r["documents_added"] == 1
    assert r["chunks_added"] >= 1
