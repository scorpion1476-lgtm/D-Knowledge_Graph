"""Three negative requirements, each asserting that a claim is *not* made.

These are the acceptance tests for matrix rows D-06, F-12 and K-12. All three
say the same kind of thing: the product must not claim something it has not
earned. A negative requirement is easy to satisfy by accident and easy to break
by accident, which is exactly why it needs an executed gate rather than a
reviewer's memory.

* **D-06, no provider name in normal branding.** The project is LLM-agnostic.
  Its branding surfaces (the distribution name, the console scripts, the
  wordmark, the badge row, the brand document) must therefore carry no model
  provider's name. Naming a provider as an *interoperability target* is a
  different thing and is allowed: "writes the MCP entry for Claude Code, Cursor
  or Windsurf" describes what the helper configures, it does not brand the
  product. The test separates the two by scoping the ban to branding surfaces
  and requiring every remaining mention to sit on a line that names the
  integration it belongs to.

* **F-12, no false claim of a cloud client handshake.** No third-party MCP
  client has ever connected to this build's HTTP surface in this environment.
  No document may say or imply otherwise, and the matrix must keep saying so.

* **K-12, no signed release claim until real signing runs.** The release
  workflow is configured for keyless signing, but no release has been signed:
  S-04 records exactly that. While S-04 is not production ready, nothing in the
  repository may present a signed release as a fact.

Each test carries a negative control that plants the forbidden claim in a copy
of the text and proves the same checker rejects it. Without those controls a
regex that silently stopped matching would leave all three rows green forever.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DOCS = ROOT / "docs"
CSV_PATH = DOCS / "REQUIREMENTS_TRACEABILITY_MATRIX.csv"

# Model providers whose name must never brand this product. Kept as whole-word
# patterns so "gptcache" or a hash that happens to contain "gpt" is not a hit.
PROVIDER_NAMES = (
    "openai",
    "anthropic",
    "claude",
    "chatgpt",
    "gpt-3",
    "gpt-4",
    "gpt-5",
    "gemini",
    "bard",
    "cohere",
    "mistral",
    "ollama",
    "copilot",
)
# "llama" is deliberately not in the list above. It is an ordinary Spanish verb
# ("llama a casa" is "calls home"), and the Spanish README uses it, so a bare
# word match would report a translation as branding itself with a model name.
# The model is matched in the shapes it actually appears in instead.
_EXTRA_PROVIDER_PATTERNS = (
    r"llama[\s._-]?\d",
    r"llama\.cpp",
    r"meta[\s-]llama",
)
_PROVIDER_RE = re.compile(
    "(?<![a-z0-9])("
    + "|".join([*(re.escape(p) for p in PROVIDER_NAMES), *_EXTRA_PROVIDER_PATTERNS])
    + r")(?![a-z0-9])",
    re.I,
)

# Every tracked README is a branding surface, not just the English one. A
# translation whose masthead named a provider would be exactly the failure this
# row forbids, and it went unchecked until an adversarial review planted one.
READMES = ("README.md", "README.zh-CN.md", "README.es.md", "README.fr.md", "README.de.md")


def _providers_in(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _PROVIDER_RE.finditer(text)})


# --------------------------------------------------------------------------
# D-06: no provider name in normal branding
# --------------------------------------------------------------------------


def _masthead(path: Path) -> str:
    """The masthead: logo, tagline, badges and language row, down to the nav.

    This is what a reader sees before any prose, and it is what "branding"
    means for a repository page. Delimited at the first level-two heading,
    because the translations head their first section in their own language.
    """
    text = path.read_text(encoding="utf-8")
    end = text.find("\n## ")
    assert end > 0, f"{path.name} has no level-two heading; the masthead cannot be delimited"
    return text[:end]


def _readme_branding_block() -> str:
    return _masthead(README)


def _project_branding_fields() -> dict[str, str]:
    """Name, description, keywords and console scripts from pyproject.

    tomllib is 3.11 and later and this project supports 3.10, so the loader is
    guarded the same way `scripts/probe_environment.py` guards it. The fallback
    reads the same four fields out of the text.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib

        project = tomllib.loads(text)["project"]
        return {
            "name": project["name"],
            "description": project["description"],
            "keywords": " ".join(project.get("keywords", [])),
            "console_scripts": " ".join(project.get("scripts", {})),
        }
    except ModuleNotFoundError:
        pass
    fields: dict[str, str] = {}
    for key in ("name", "description"):
        m = re.search(rf'^{key}\s*=\s*"([^"]*)"', text, re.M)
        assert m, f"pyproject has no {key}"
        fields[key] = m.group(1)
    kw = re.search(r"^keywords\s*=\s*\[(.*?)\]", text, re.M | re.S)
    fields["keywords"] = kw.group(1) if kw else ""
    scripts = re.search(r"^\[project\.scripts\]\s*\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    fields["console_scripts"] = scripts.group(1) if scripts else ""
    assert fields["console_scripts"].strip(), "pyproject declares no console scripts"
    return fields


def test_distribution_and_console_scripts_carry_no_provider_name():
    surfaces = _project_branding_fields()
    for label, value in surfaces.items():
        assert not _providers_in(value), f"pyproject {label} names a provider: {value!r}"


@pytest.mark.parametrize("name", READMES)
def test_every_readme_masthead_carries_no_provider_name(name):
    path = ROOT / name
    assert path.is_file(), f"{name} is missing"
    found = _providers_in(_masthead(path))
    assert not found, f"the {name} masthead brands the product with {found}"


def test_brand_document_carries_no_provider_name():
    found = _providers_in((DOCS / "BRAND.md").read_text(encoding="utf-8"))
    assert not found, f"docs/BRAND.md names a provider: {found}"


def test_brand_asset_filenames_carry_no_provider_name():
    assets = ROOT / "assets" / "brand"
    if not assets.is_dir():
        pytest.skip("no brand asset directory in this checkout")
    for path in sorted(assets.rglob("*")):
        assert not _providers_in(path.name), f"brand asset {path.name} names a provider"


def test_every_remaining_provider_mention_is_an_integration_not_branding():
    """A provider name in the body is allowed only where it names a client.

    This is the line between "we integrate with that tool" and "we are that
    tool". Any mention that is not on a line about configuring, connecting or
    supporting a client is branding by default and fails.
    """
    integration_words = (
        "mcp",
        "client",
        "editor",
        "configure",
        "configuration",
        "integration",
        "server entry",
        "code, cursor",
    )
    # The integration vocabulary has to cover the translations too, since the
    # same table row is rendered in five languages.
    translated_words = (
        "编辑器", "客户端", "配置",           # editor, client, configure
        "editor", "cliente", "configura",    # es
        "éditeur", "client", "configure",    # fr
        "editor-integration", "eintrag", "konfigur",  # de
    )
    offenders: list[str] = []
    sources = [ROOT / name for name in READMES] + sorted(DOCS.glob("*.md"))
    for path in sources:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _providers_in(line):
                continue
            low = line.lower()
            if any(word in low for word in integration_words + translated_words):
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:100]}")
    assert not offenders, "provider named outside an integration context: " + "; ".join(offenders)


def test_the_branding_checker_rejects_a_planted_provider_name():
    """Negative control for D-06.

    If the pattern above ever stops matching, every assertion in this section
    passes vacuously. This proves it still bites.
    """
    planted = _readme_branding_block().replace(
        "D-Knowledge Graph", "OpenAI Knowledge Graph", 1
    )
    assert _providers_in(planted) == ["openai"]


# --------------------------------------------------------------------------
# F-12: no false claim of a cloud client handshake
# --------------------------------------------------------------------------

# Wording that would assert a real client actually connected. "can be
# configured for" is fine; "has been verified against" is not.
_HANDSHAKE_CLAIM = re.compile(
    r"\b(verified|tested|validated|confirmed|exercised|proven)\b[^.\n]{0,80}"
    r"\b(handshake|connect(?:ed|ion)?|live client|real client|third-party client)\b",
    re.I,
)


def _tracked_prose() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in [README, *sorted(DOCS.glob("*.md"))]]


def test_no_document_claims_a_third_party_client_handshake_was_performed():
    offenders: list[str] = []
    for path, text in _tracked_prose():
        for number, line in enumerate(text.splitlines(), 1):
            if _HANDSHAKE_CLAIM.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:110]}")
    assert not offenders, "a client handshake is claimed as performed: " + "; ".join(offenders)


def test_the_matrix_still_records_the_handshake_as_an_external_input():
    """The honest disclaimer must be present, not merely the absence of a lie.

    Silence would also pass the check above, and silence is how an unverified
    claim becomes an assumed one.
    """
    text = (DOCS / "REQUIREMENTS_TRACEABILITY_MATRIX.md").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", text).lower()
    assert "third-party mcp client handshake" in flat
    assert "needs an external client" in flat


def test_the_handshake_checker_rejects_a_planted_claim():
    """Negative control for F-12."""
    planted = "The HTTP surface has been verified against a live client handshake."
    assert _HANDSHAKE_CLAIM.search(planted)


# --------------------------------------------------------------------------
# K-12: no signed release claim until real signing runs
# --------------------------------------------------------------------------

_SIGNED_RELEASE_CLAIM = re.compile(
    r"\b(releases?|wheels?|artefacts?|artifacts?|builds?)\b[^.\n]{0,60}"
    r"\b(are|is|were|was|have been|has been)\b[^.\n]{0,30}\bsigned\b",
    re.I,
)

# Words that turn the pattern above into a denial rather than a claim. "no
# release has been signed" matches the claim pattern exactly, and is the
# opposite of a claim: the matrix and this project's own reports say it often.
_NEGATORS = re.compile(r"\b(no|not|never|nothing|until|unless|cannot|without)\b", re.I)


def _claims_a_signed_release(line: str) -> bool:
    match = _SIGNED_RELEASE_CLAIM.search(line)
    if not match:
        return False
    # Look from a little before the subject to the end of the match: a negation
    # anywhere in that window means the sentence denies the signing.
    start = max(0, match.start() - 30)
    return not _NEGATORS.search(line[start : match.end()])


def _row(row_id: str) -> dict:
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["id"] == row_id:
                return row
    raise AssertionError(f"row {row_id} not found")


def test_signing_has_not_actually_run_so_the_ban_is_live():
    """This test only means anything while signing really has not happened.

    S-04 is the row that would go green the day a real signed release runs. If
    it ever does, this module must be revisited rather than left asserting a
    ban that no longer applies.
    """
    assert _row("S-04")["status"] != "PRODUCTION READY", (
        "S-04 is production ready, so a real signed release has run and K-12's "
        "ban on claiming one needs rewriting"
    )


def test_no_surface_states_that_a_release_was_signed():
    offenders: list[str] = []
    for path, text in _tracked_prose():
        for number, line in enumerate(text.splitlines(), 1):
            if _claims_a_signed_release(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:110]}")
    assert not offenders, "a signed release is claimed as fact: " + "; ".join(offenders)


def test_no_badge_advertises_a_signed_release():
    badges = re.findall(r"!\[[^\]]*\]\([^)]*\)", README.read_text(encoding="utf-8"))
    offenders = [b for b in badges if re.search(r"sign(ed|ing)|sigstore|slsa", b, re.I)]
    assert not offenders, f"README badge advertises signing: {offenders}"


def test_the_runbook_forbids_the_label_until_signing_runs():
    text = (DOCS / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", text).lower()
    assert "do not add a signed-release label until a real signing step has run" in flat


def test_the_release_row_states_signing_is_not_green():
    """S-04 must say both things: that it is not green, and why.

    Matched on the claim rather than on one exact sentence. The wording of this
    cell has been rewritten more than once, and a test pinned to a phrase fails
    on an honest rewording while passing on a dishonest one that happens to keep
    the phrase.
    """
    limitation = _row("S-04")["remaining_limitation"].lower()
    assert "not green" in limitation, "S-04 does not state that it is not green"
    assert "signing identity" in limitation or "signing key" in limitation, (
        "S-04 does not name the external input it is waiting on"
    )
    assert re.search(
        r"(no release has been signed|have not been performed|has not been performed"
        r"|not been signed)",
        limitation,
    ), "S-04 does not state that no signed release has actually run"


def test_the_signed_release_checker_rejects_a_planted_claim():
    """Negative control for K-12, in both directions.

    It has to fire on a claim and stay silent on a denial. Without the second
    half, the honest sentence "no release has been signed" would be reported as
    a claim that one was, and the only way to pass would be to stop saying it.
    """
    assert _claims_a_signed_release("Every release wheel is signed with Sigstore.")
    assert _claims_a_signed_release("Release artifacts have been signed since 0.1.0.")
    # Denials and the configured-but-not-run wording must not fire.
    assert not _claims_a_signed_release("NOT GREEN: no release has been signed.")
    assert not _claims_a_signed_release("No release artifact has been signed by this project.")
    assert not _claims_a_signed_release(
        "Do not add a signed-release label until a real signing step has run."
    )
    assert not _claims_a_signed_release(
        "The workflow signs the wheel with sigstore-python when a release runs."
    )
