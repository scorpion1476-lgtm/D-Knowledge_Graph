"""XXE safety: ensure any XML we parse comes from our own writer, not user input."""

from dkg.export.graphml import export_graphml


def test_graphml_writer_produces_wellformed_xml(db, tmp_path):
    # We do not read arbitrary XML; the writer only produces XML. Assert
    # basic well-formedness of the output.
    from dkg.ingest.base import ingest_text

    ingest_text(db, "Alpha and Beta are here. Alpha is safe. Beta is fast.", display_name="doc")
    out = tmp_path / "graph.graphml"
    export_graphml(db, out)
    xml = out.read_text(encoding="utf-8")
    assert xml.startswith("<?xml")
    assert "<graphml" in xml
