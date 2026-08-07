import zipfile

import pytest

from dkg.core.errors import IngestError, SecurityError
from dkg.ingest.docx_stdlib import read_docx_text

_MIN_DOCX_BODY = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Hello DKG</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>"""


def _make_docx(tmp_path, xml=_MIN_DOCX_BODY, extra=None):
    p = tmp_path / "sample.docx"
    with zipfile.ZipFile(str(p), "w") as zf:
        zf.writestr("word/document.xml", xml.encode("utf-8"))
        if extra:
            for name, data in extra.items():
                zf.writestr(name, data)
    return p


def test_reads_paragraphs(tmp_path):
    p = _make_docx(tmp_path)
    text = read_docx_text(p)
    assert "Hello DKG" in text
    assert "Second paragraph" in text
    assert text.count("\n") >= 1


def test_missing_document_xml(tmp_path):
    p = tmp_path / "bad.docx"
    with zipfile.ZipFile(str(p), "w") as zf:
        zf.writestr("something/else.txt", "x")
    with pytest.raises(IngestError):
        read_docx_text(p)


def test_rejects_dtd(tmp_path):
    xml = '<?xml version="1.0"?><!DOCTYPE w><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>'
    p = _make_docx(tmp_path, xml=xml)
    with pytest.raises(SecurityError):
        read_docx_text(p)


def test_rejects_traversal(tmp_path):
    p = _make_docx(tmp_path, extra={"../evil.txt": "x"})
    with pytest.raises(SecurityError):
        read_docx_text(p)
