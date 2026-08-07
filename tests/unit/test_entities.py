from dkg.extract.entities import extract_entities


def test_extracts_url():
    ents = extract_entities("See https://example.com/x for more.")
    urls = [e for e in ents if e.kind == "url"]
    assert urls and urls[0].canonical.startswith("https://")


def test_extracts_org_with_suffix():
    ents = extract_entities("Founded by Acme Labs, then acquired by Delta Corp.")
    kinds = {e.kind for e in ents}
    assert "organisation" in kinds


def test_extracts_version():
    ents = extract_entities("Ships with library v1.2.3 baseline.")
    versions = [e for e in ents if e.kind == "version"]
    assert versions
