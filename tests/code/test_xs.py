"""Perl XS extraction, at the fidelity it claims and no higher.

N-10 names Perl XS explicitly. The build used to report the extension
unsupported, on the reasoning that handing an .xs file to the C grammar would
misattribute the macro layer and invent symbols. That reasoning was right about
the C grammar and wrong as a conclusion: the file's own MODULE/PACKAGE
sectioning and its two-line XSUB headers are the most regular thing in it, and
a pattern extractor reads them without touching the macro layer at all.

So these tests check two things that pull in opposite directions:

1. It finds what the file really defines, including the cases that make XS
   different from C: the PREFIX rule, a package change, and the two-line
   header shape.
2. It does NOT claim more than that. The fidelity label is `fallback`, edges
   leaving the file are confidence-scaled, and the extractor's stated limits
   are asserted as limits rather than quietly hoped away.

The second group matters more than the first. A pattern extractor that silently
grew into a half-parser would be exactly the dishonesty the fidelity label
exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dkg.code.model import FIDELITY_FALLBACK
from dkg.code.parser import EXT_LANG, NOT_PARSED, language_inventory, not_parsed_reason
from dkg.code.xs import parse_xs

CORPUS = Path(__file__).resolve().parent / "corpus" / "langs" / "xs"


def _kinds(pf) -> set[tuple[str, str]]:
    return {(s.kind, s.name) for s in pf.symbols if s.kind != "module"}


def _refs(pf, kind: str) -> set[tuple[str, str]]:
    """(owner tail, target) pairs, with the owner reduced to its readable end."""
    out = set()
    for r in pf.references:
        if r.kind != kind:
            continue
        owner = r.from_qualified.split("::")[-1] if "::" in r.from_qualified else r.from_qualified
        out.add((Path(owner).name, r.name))
    return out


# -- what it finds ------------------------------------------------------------


def test_a_package_becomes_a_class():
    pf = parse_xs(CORPUS / "Shapes.xs")

    assert ("class", "Geometry::Shapes") in _kinds(pf)
    assert ("class", "Geometry::Shapes::Util") in _kinds(pf)


def test_a_prefix_is_stripped_because_that_is_the_name_perl_sees():
    """`PREFIX = gs_` means the XSUB `gs_area_of_circle` registers as `area_of_circle`."""
    pf = parse_xs(CORPUS / "Shapes.xs")
    names = {name for kind, name in _kinds(pf) if kind == "method"}

    assert "area_of_circle" in names
    assert "gs_area_of_circle" not in names, "the C-level name is not what Perl calls"


def test_the_line_as_written_is_kept_so_the_c_level_name_is_not_lost():
    pf = parse_xs(CORPUS / "Shapes.xs")
    xsub = next(s for s in pf.symbols if s.name == "area_of_circle")

    assert "gs_area_of_circle" in xsub.text


def test_a_package_without_a_prefix_keeps_the_name_exactly():
    """The prefix must stop applying at the next MODULE line."""
    pf = parse_xs(CORPUS / "Registry.xs")
    names = {name for kind, name in _kinds(pf) if kind == "method"}

    assert {"add", "total", "reset"} <= names


def test_an_xsub_is_owned_by_the_package_in_force():
    pf = parse_xs(CORPUS / "Shapes.xs")
    clamp = next(s for s in pf.symbols if s.name == "clamp")

    assert clamp.parent.endswith("Geometry::Shapes::Util")


def test_a_c_helper_above_the_module_line_is_a_function_not_a_method():
    pf = parse_xs(CORPUS / "Shapes.xs")

    assert ("function", "circle_area") in _kinds(pf)
    assert ("method", "circle_area") not in _kinds(pf)


def test_a_one_line_c_definition_is_found():
    """A body on the same line still has semicolons in it.

    An earlier rule rejected any line containing a semicolon, to exclude
    prototypes, and silently lost every one-line helper.
    """
    pf = parse_xs(CORPUS / "Registry.xs")

    assert ("function", "bump_count") in _kinds(pf)


def test_an_include_becomes_an_import():
    pf = parse_xs(CORPUS / "Shapes.xs")

    assert ("Shapes.xs", "perl") in _refs(pf, "imports")
    assert ("Shapes.xs", "math") in _refs(pf, "imports")


def test_a_call_in_an_xsub_body_is_attributed_to_that_xsub():
    pf = parse_xs(CORPUS / "Shapes.xs")

    assert ("Shapes.area_of_circle", "circle_area") in _refs(pf, "calls")


def test_a_section_marker_is_never_a_symbol():
    """CODE:, OUTPUT: and PPCODE: are sectioning, not definitions."""
    pf = parse_xs(CORPUS / "Shapes.xs")
    names = {name for _, name in _kinds(pf)}

    assert not names & {"CODE", "OUTPUT", "PPCODE", "PROTOTYPES", "RETVAL"}


# -- what it refuses to claim -------------------------------------------------


def test_the_fidelity_is_fallback_not_grammar():
    pf = parse_xs(CORPUS / "Shapes.xs")

    assert pf.fidelity == FIDELITY_FALLBACK
    assert pf.language == "xs"


def test_the_inventory_reports_it_as_fallback_with_a_reason():
    entry = language_inventory()["xs"]

    assert entry["fidelity"] == "fallback"
    assert entry["available"] is True
    assert "No permissive Tree-sitter grammar" in entry["reason"]
    assert entry["licence"] == "not applicable"


def test_the_inventory_offers_no_upgrade_because_there_is_none():
    """The five bundle languages can be upgraded to a grammar. This one cannot.

    Offering an upgrade key here would point a user at an extra that would not
    change anything.
    """
    entry = language_inventory()["xs"]

    assert "upgrade" not in entry
    assert entry["extra"] is None


def test_the_extension_is_no_longer_reported_unsupported():
    assert not_parsed_reason("thing.xs") == ""
    assert ".xs" not in NOT_PARSED
    assert EXT_LANG[".xs"] == "xs"


def test_a_prototype_defines_nothing():
    """`static SV *build_result(int, const char *);` declares and defines nothing."""
    source = (
        '#include "XSUB.h"\n'
        "static SV *build_result(int code, const char *message);\n"
    )
    pf = parse_xs("proto.xs", source)

    assert ("function", "build_result") not in _kinds(pf)


def test_a_definition_inside_a_block_comment_is_not_a_definition():
    source = (
        "MODULE = X    PACKAGE = X\n"
        "\n"
        "/*\n"
        "void\n"
        "commented()\n"
        "  CODE:\n"
        "    nothing();\n"
        "*/\n"
    )
    pf = parse_xs("comment.xs", source)

    assert ("method", "commented") not in _kinds(pf)


def test_the_preprocessor_limit_is_real_and_is_the_documented_one():
    """An XSUB inside `#if 0` IS extracted, which is a false positive.

    This is asserted rather than fixed because the extractor's docstring states
    that it does not evaluate the C preprocessor, and the held-out corpus
    publishes the resulting precision of 0.875 rather than tuning it away. If
    someone later teaches it to track `#if 0`, this test failing is the correct
    and intended signal to update the docstring and republish the figure.
    """
    source = (
        "MODULE = X    PACKAGE = X\n"
        "\n"
        "#if 0\n"
        "\n"
        "void\n"
        "disabled()\n"
        "  CODE:\n"
        "    never();\n"
        "\n"
        "#endif\n"
    )
    pf = parse_xs("ifdef.xs", source)

    assert ("method", "disabled") in _kinds(pf), (
        "the documented limit changed; update src/dkg/code/xs.py and the published "
        "held-out precision"
    )


def test_an_edge_leaving_an_xs_file_is_confidence_scaled():
    """The fidelity label has to cost something or it is decoration.

    The graph builder scales any edge whose source came from a fallback-parsed
    file. XS gets that for free by setting the fidelity, and this asserts the
    wiring rather than assuming it.
    """
    from dkg.code.graph import resolve_edges
    from dkg.code.model import CONF_RESOLVED, FALLBACK_CONFIDENCE_FACTOR

    parsed = parse_xs(CORPUS / "Shapes.xs")
    edges = resolve_edges([parsed])
    calls = [
        e for e in edges
        if e.predicate == "calls" and e.to_qualified.endswith("::circle_area")
    ]

    assert calls, "the XS parse should still produce a call edge"
    # The same edge out of a grammar parse would score CONF_RESOLVED.
    assert calls[0].confidence == pytest.approx(CONF_RESOLVED * FALLBACK_CONFIDENCE_FACTOR)
    assert calls[0].confidence < CONF_RESOLVED


def test_an_empty_file_yields_only_its_module_symbol():
    pf = parse_xs("empty.xs", "")

    assert [s.kind for s in pf.symbols] == ["module"]


def test_symbols_are_unique_and_references_are_deduplicated():
    pf = parse_xs(CORPUS / "Shapes.xs")

    qualified = [s.qualified for s in pf.symbols]
    assert len(qualified) == len(set(qualified))
    keys = [(r.from_qualified, r.kind, r.name) for r in pf.references]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("name", ["Shapes.xs", "Registry.xs"])
def test_the_corpus_files_parse_through_the_ordinary_entry_point(name):
    """Not just parse_xs directly: the dispatcher must route .xs here."""
    from dkg.code.parser import parse_source

    pf = parse_source(CORPUS / name)

    assert pf.language == "xs"
    assert pf.fidelity == FIDELITY_FALLBACK
    assert any(s.kind == "class" for s in pf.symbols)
