"""The grammar bundle is taken on audited evidence, and the audit is checked.

The bundle compiles every grammar it ships into one shared object, so installing
the ``code-bundle`` extra ships all of them, not only the five this project
enables. The permissive-only rule therefore applies to the whole bundle.

``scripts/audit_grammar_bundle.py`` resolves each grammar's licence from its own
upstream repository at the exact revision the bundle pins and writes
``docs/grammar_bundle_licences.json``. These tests hold that committed artifact
to the claims made about it in THIRD_PARTY_NOTICES.md, so the claim cannot drift
away from the measurement, and check that the five enabled languages are pinned
consistently between the audit and the code.

They read the committed artifact and make no network call, so they run in the
air-gapped default like everything else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dkg.code.capability import (
    BUNDLE_EXTRA,
    BUNDLE_GRAMMAR_SOURCES,
    BUNDLE_GRAMMARS,
    GRAMMAR_EXTRAS,
    GRAMMAR_LICENCES,
)

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "docs" / "grammar_bundle_licences.json"
SOURCES = ROOT / "docs" / "grammar_bundle_sources.json"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"

# Licence families the permissive-only rule admits: Apache-2.0, MIT, BSD, ISC,
# HPND, or a public-domain equivalent. Anything outside this set must fail.
FORBIDDEN_SUBSTRINGS = ("GPL", "AGPL", "LGPL", "MPL", "EPL", "CDDL", "SSPL", "BUSL")


@pytest.fixture(scope="module")
def audit() -> dict:
    assert AUDIT.exists(), f"the grammar licence audit is missing: {AUDIT}"
    return json.loads(AUDIT.read_text(encoding="utf-8"))


def test_the_audit_covers_every_grammar_the_bundle_ships(audit):
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    assert audit["audited_languages"] == len(sources["languages"])
    assert {g["language"] for g in audit["grammars"]} == set(sources["languages"])


def test_no_grammar_in_the_bundle_is_copyleft(audit):
    """The claim the extra rests on. If this fails the extra must be dropped."""
    assert audit["copyleft"] == [], f"copyleft grammars in the bundle: {audit['copyleft']}"
    for grammar in audit["grammars"]:
        # An absent SPDX would match no forbidden substring and so pass this
        # loop unchecked. Fail on it here too, rather than relying on the
        # separate unresolved test to have run.
        assert grammar.get("spdx"), (
            f"{grammar['language']} has no resolved licence, so the substring "
            "check below would pass it without checking anything"
        )
        spdx = (grammar.get("spdx") or "").upper()
        for forbidden in FORBIDDEN_SUBSTRINGS:
            if forbidden == "GPL" and "LGPL" in spdx:
                continue  # counted by its own entry
            # "Apache-2.0 WITH LLVM-exception" is Apache plus an ADDITIONAL
            # permission, so it is more permissive, not less.
            if forbidden in spdx and "WITH LLVM-EXCEPTION" not in spdx:
                pytest.fail(f"{grammar['language']} resolves to {grammar['spdx']}")


def test_every_grammar_resolved_to_a_licence(audit):
    """Nothing may be left unresolved, because unresolved bypasses the copyleft check.

    The copyleft test below matches SPDX substrings. A grammar recorded with a
    null SPDX matches nothing, so it passes that test without ever being
    checked. Tolerating "a couple" of those, as an earlier version of this test
    did, left room for a secretly-copyleft grammar to ship unnoticed. The bar is
    therefore zero, and the reported list must agree exactly with what is in the
    records rather than merely being a superset of it.
    """
    unresolved = sorted(g["language"] for g in audit["grammars"] if not g.get("spdx"))
    assert unresolved == sorted(audit["unresolved"]), (
        "the summary's unresolved list disagrees with the per-grammar records"
    )
    assert unresolved == [], (
        f"unresolved grammars bypass the copyleft check entirely: {unresolved}. "
        "Resolve them or drop the bundle."
    )


def test_each_enabled_grammar_is_pinned_to_an_audited_revision(audit):
    """The five the project actually uses, checked against the audit itself."""
    by_language = {g["language"]: g for g in audit["grammars"]}
    for language, (spdx, repo, rev) in BUNDLE_GRAMMAR_SOURCES.items():
        bundle_name = BUNDLE_GRAMMARS[language]
        record = by_language[bundle_name]
        assert record["repo"] == repo, language
        assert record["rev"] == rev, language
        assert record["spdx"] == spdx, language
        assert len(rev) == 40, f"{language} is not pinned to a full revision"


def test_the_enabled_languages_agree_across_every_table():
    """One fact, three tables. Disagreement would make one of them a lie."""
    for language, (spdx, _repo, _rev) in BUNDLE_GRAMMAR_SOURCES.items():
        assert language in BUNDLE_GRAMMARS
        assert GRAMMAR_LICENCES[language] == spdx
        assert GRAMMAR_EXTRAS[language] == BUNDLE_EXTRA


def test_the_notices_file_states_the_measured_result_not_a_rounded_one(audit):
    text = NOTICES.read_text(encoding="utf-8")
    assert "code-bundle" in text
    assert "tree-sitter-language-pack" in text
    # The headline claim and the count behind it.
    assert "none was\ncopyleft" in text or "none was copyleft" in text
    assert str(audit["audited_languages"]) in text
    for language, (_spdx, repo, rev) in BUNDLE_GRAMMAR_SOURCES.items():
        host = repo.replace("https://", "")
        assert host in text, f"{language} upstream is not attributed"
        assert rev[:8] in text, f"{language} revision is not attributed"


def test_the_vb_grammar_declaration_is_not_rounded_up_to_a_licence_file(audit):
    """It declares MIT but ships no licence text, and that is said plainly."""
    record = next(g for g in audit["grammars"] if g["language"] == "vb")
    assert record["spdx"] == "MIT"
    assert "metadata" in record["evidence"], record["evidence"]
    assert "no licence text in-tree" in NOTICES.read_text(encoding="utf-8")


def test_the_bundle_still_carries_no_grammar_for_perl_xs(audit):
    """The licensing fact this file is about has not changed.

    Perl XS is now read, but by a documented pattern extractor rather than by a
    grammar, and this asserts the premise that makes that the only option: no
    grammar in the bundle claims the .xs extension. If one ever appears, this
    test failing is the signal to reconsider the extractor, not to delete the
    test.
    """
    from dkg.code.capability import BUNDLE_GRAMMAR_SOURCES

    assert "xs" not in BUNDLE_GRAMMAR_SOURCES
    claimed = {g["language"] for g in audit["grammars"]}
    assert "xs" not in claimed


def test_perl_xs_is_read_by_the_pattern_extractor_at_fallback_fidelity():
    """Read, but never presented as a parse.

    This row previously reported the extension unsupported. That was the right
    call about the C grammar and the wrong conclusion overall: the sectioning is
    the most regular thing in the file, and a user pointing the tool at a Perl
    extension distribution got nothing back for its most important file.
    """
    from dkg.code.model import FIDELITY_FALLBACK
    from dkg.code.parser import NOT_PARSED, language_inventory, not_parsed_reason, parse_source

    assert ".xs" not in NOT_PARSED
    assert not_parsed_reason("Ext/Fast.xs") == ""

    parsed = parse_source(
        "Ext/Fast.xs",
        "MODULE = Fast    PACKAGE = Fast\n\nint\nping()\n  CODE:\n    RETVAL = 1;\n",
    )

    assert parsed.language == "xs"
    assert parsed.fidelity == FIDELITY_FALLBACK, "it must never claim a grammar parse"
    assert ("class", "Fast") in {(s.kind, s.name) for s in parsed.symbols}

    entry = language_inventory()["xs"]
    assert entry["fidelity"] == "fallback"
    assert "No permissive Tree-sitter grammar" in entry["reason"]


@pytest.mark.parametrize(
    ("label", "text", "expected_family"),
    [
        # The ordering trap: a GPL file whose preamble quotes the MIT grant.
        # Checking permissive phrases first would classify these as MIT and the
        # whole audit would read clean while shipping copyleft.
        (
            "GPL-3.0 quoting the MIT grant",
            "GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007. "
            "Permission is hereby granted, free of charge, to copy verbatim copies",
            "copyleft",
        ),
        (
            "AGPL-3.0 quoting the MIT grant",
            "GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007. "
            "Permission is hereby granted, free of charge, to anyone",
            "copyleft",
        ),
        ("GPL-2.0", "GNU GENERAL PUBLIC LICENSE Version 2, June 1991", "copyleft"),
        ("LGPL-3.0", "GNU LESSER GENERAL PUBLIC LICENSE Version 3", "copyleft"),
        ("LGPL-2.1", "GNU LESSER GENERAL PUBLIC LICENSE Version 2.1", "copyleft"),
        ("MPL-2.0", "Mozilla Public License Version 2.0", "copyleft"),
        ("EPL-2.0", "Eclipse Public License - v 2.0", "copyleft"),
        (
            "MIT",
            "MIT License. Permission is hereby granted, free of charge, to any person",
            "permissive",
        ),
        ("Apache-2.0", "Apache License Version 2.0, January 2004", "permissive"),
        (
            "ISC",
            "Permission to use, copy, modify, and/or distribute this software for any purpose",
            "permissive",
        ),
        (
            "BSD-3-Clause",
            "Redistribution and use in source and binary forms, with or without "
            "modification. Neither the name of the copyright holder",
            "permissive",
        ),
    ],
)
def test_the_licence_classifier_cannot_read_copyleft_as_permissive(
    label, text, expected_family
):
    """The audit is only as good as this function. A misread here reads green."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from audit_grammar_bundle import classify_text, family

    assert family(classify_text(text)) == expected_family, label


def test_an_unrecognisable_licence_is_never_assumed_permissive():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from audit_grammar_bundle import classify_text, family

    assert classify_text("Some bespoke terms nobody has seen before.") is None
    assert family(None) == "unresolved"


def test_no_grammar_in_the_bundle_claims_the_xs_extension():
    """The reason Perl XS stays unsupported, checked rather than asserted."""
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    claiming = [
        name
        for name, spec in sources["languages"].items()
        if "xs" in [e.lower() for e in (spec.get("extensions") or [])]
    ]
    assert claiming == [], f"a bundled grammar does claim .xs: {claiming}"
