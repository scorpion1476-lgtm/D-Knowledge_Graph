from dkg.extract.resolver import canonicalise, resolve_names


def test_canonicalise_case_and_punct():
    assert canonicalise("Acme Corp.") == canonicalise("acme corp")
    assert canonicalise("  Acme   Labs  ") == canonicalise("acme laboratories")


def test_canonicalise_suffix_aliases():
    assert canonicalise("Delta Corp") == canonicalise("Delta Corporation")
    assert canonicalise("Gamma Ltd") == canonicalise("Gamma Limited")


def test_resolve_names_groups_variants():
    groups = resolve_names(["Acme Corp", "acme corp.", "Acme Corporation", "Other Co."])
    canonicals = {g.canonical for g in groups}
    assert "acme corporation" in canonicals
    assert "other company" in canonicals
