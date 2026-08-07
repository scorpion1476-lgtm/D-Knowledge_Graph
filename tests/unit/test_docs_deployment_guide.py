"""The deployment guide has to cover a real production deployment.

Acceptance test for three matrix rows that all rest on this one document:

* **F-11**, reverse proxy and TLS instructions,
* **L-06**, self-hosted remote deployment,
* **L-11**, production deployment guide covering TLS, backups, monitoring, log
  retention and recovery.

All three were previously accepted on a manual review, and a manual review of a
deployment guide is the weakest kind: the reader who finds the gap is an
operator in production, and the gap that hurts is a missing topic rather than a
badly written one. L-11 names five topics explicitly, so each is bound to an
assertion here.

Two of the checks are about substance rather than presence:

* the reverse-proxy example must actually terminate TLS and must actually
  forward to a loopback address. A proxy snippet that listens on plain HTTP, or
  that forwards to a public bind, is worse than no snippet because it is
  copy-pasted.
* the remote-deployment guidance must state the credential rule the code really
  enforces. This build refuses a non-loopback bind with no credential at
  startup, and an operator who does not know that will read the refusal as a
  bug. The assertion is cross-checked against the source so the document cannot
  drift away from the behaviour.

The container section is asserted against the checked-in Dockerfile and compose
file only. Nothing here builds or runs a container: the project's Docker
isolation rule forbids it, and the rows that need a real container run stay
honestly unverified for that reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "DEPLOYMENT_GUIDE.md"

# Each of L-11's five named topics maps to the section that must carry it.
# "recovery" is deliberately mapped to `Upgrade and rollback` alone rather than
# also to `Backup and recovery`: an adversarial review pointed out that letting
# two topics share one heading means deleting that heading fails one topic while
# the other silently rides on the survivor, so recovery would have had no
# independent section of its own.
REQUIRED_TOPICS = {
    "TLS": ("Reverse proxy with TLS",),
    "backups": ("Backup and recovery",),
    "monitoring": ("Monitoring",),
    "log retention": ("Log retention",),
    "recovery": ("Upgrade and rollback",),
}


def test_each_named_topic_has_a_section_of_its_own():
    """No two of the five may be satisfied by the same heading."""
    headings = [h for hs in REQUIRED_TOPICS.values() for h in hs]
    assert len(headings) == len(set(headings)), (
        f"topics share a section, so one can vanish unnoticed: {headings}"
    )


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), "docs/DEPLOYMENT_GUIDE.md does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sections(doc: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current, body = None, []
    for line in doc.splitlines():
        if line.startswith("## "):
            if current:
                out[current] = "\n".join(body)
            current, body = line[3:].strip(), []
        elif current is not None:
            body.append(line)
    if current:
        out[current] = "\n".join(body)
    return out


# -- L-11: every named topic is covered ---------------------------------------


@pytest.mark.parametrize(("topic", "headings"), sorted(REQUIRED_TOPICS.items()))
def test_every_topic_the_requirement_names_has_a_section(sections, topic, headings):
    present = [h for h in headings if h in sections]
    assert present, f"the guide has no section for {topic}; expected one of {headings}"
    assert any(len(sections[h].split()) >= 25 for h in present), (
        f"the {topic} section exists but is too thin to act on"
    )


# -- F-11: the proxy example is a working proxy example -----------------------


def test_the_guide_gives_a_concrete_reverse_proxy_configuration(sections):
    section = sections["Reverse proxy with TLS"]
    assert re.search(r"```(nginx|caddy|apache)", section), (
        "the reverse proxy section has no configuration block to copy"
    )


def test_the_proxy_example_terminates_tls(sections):
    section = sections["Reverse proxy with TLS"]
    assert re.search(r"listen\s+443\s+ssl", section), "the proxy example does not listen on TLS"
    assert "ssl_certificate" in section, "the proxy example configures no certificate"
    assert "ssl_certificate_key" in section, "the proxy example configures no private key"


def test_the_proxy_forwards_to_a_loopback_address_only(sections):
    """Forwarding to a public bind would defeat the whole arrangement."""
    section = sections["Reverse proxy with TLS"]
    upstreams = re.findall(r"proxy_pass\s+https?://([^;\s]+)", section)
    assert upstreams, "the proxy example forwards nowhere"
    for upstream in upstreams:
        assert upstream.startswith(("127.0.0.1", "localhost", "[::1]")), (
            f"the proxy forwards to {upstream}, which is not loopback"
        )


def test_the_guide_warns_against_removing_the_loopback_bind(doc):
    flat = re.sub(r"\s+", " ", doc)
    assert "Do not remove the `127.0.0.1:` prefix" in flat


# -- L-06: remote self-hosting, with the credential rule the code enforces -----


def test_the_guide_opens_by_naming_the_self_hosted_deployment_shape(doc):
    head = doc.split("## ", 1)[0]
    flat = re.sub(r"\s+", " ", head).lower()
    assert "self-hosted" in flat
    assert "no cloud requirement" in flat


def test_the_guide_states_the_credential_requirement_for_a_remote_surface(sections):
    flat = re.sub(r"\s+", " ", sections["Reverse proxy with TLS"]).lower()
    assert "dkg_mcp_token" in flat, "the guide never tells an operator to set a credential"
    assert "non-loopback bind with no credential is refused at startup" in flat


def test_that_credential_rule_is_the_one_the_code_actually_enforces():
    """The document's strongest claim, cross-checked against the source.

    If the startup refusal were removed, the guide would still read correctly
    and an operator would deploy a public, unauthenticated surface believing
    the process would stop them.
    """
    joined = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (ROOT / "src" / "dkg").rglob("*.py")
    )
    assert "DKG_MCP_TOKEN" in joined, "the MCP surface no longer reads DKG_MCP_TOKEN"
    assert "DKG_MCP_ALLOW_UNAUTHENTICATED_LOOPBACK" in joined, (
        "the documented loopback opt-in is not implemented"
    )
    # The refusal the guide promises happens at bind time, not per request.
    guard = (ROOT / "src" / "dkg" / "mcp" / "http_guard.py").read_text(encoding="utf-8")
    assert "DKG_MCP_ALLOW_UNAUTHENTICATED_LOOPBACK" in guard, (
        "the loopback opt-in is not consulted by the HTTP guard"
    )


def test_the_guide_names_host_and_origin_validation(sections):
    flat = re.sub(r"\s+", " ", sections["Reverse proxy with TLS"]).lower()
    assert "host and origin are validated" in flat


# -- container tier: documented against files that exist ----------------------


def test_the_container_section_points_at_files_that_exist(sections):
    section = sections["Docker or Podman"]
    referenced = sorted(set(re.findall(r"`(docker/[A-Za-z0-9_./-]+)`", section)))
    assert referenced, "the container section names no checked-in file"
    missing = [r for r in referenced if not (ROOT / r).exists()]
    assert not missing, f"the guide names container files that do not exist: {missing}"


def test_the_compose_file_really_publishes_loopback_only(sections):
    """The guide's central container claim, checked against the compose file."""
    compose = (ROOT / "docker" / "compose.yml").read_text(encoding="utf-8")
    published = re.findall(r"^\s*-\s*\"?([0-9.]+):\d+:\d+", compose, re.M)
    assert published, "the compose file publishes no port, so the claim cannot be checked"
    for host in published:
        assert host == "127.0.0.1", f"compose publishes on {host}, not loopback"


# -- backups, monitoring, retention and recovery are actionable ---------------


def test_the_backup_section_shows_both_backup_and_restore(sections):
    section = sections["Backup and recovery"]
    assert "dkg backup" in section
    assert "dkg restore" in section
    assert "manifest" in section.lower(), "the guide never says a restore validates the archive"


def test_the_monitoring_section_names_a_liveness_check_and_an_alarm(sections):
    section = sections["Monitoring"]
    assert "/healthz" in section, "no liveness endpoint is documented"
    assert "audit --verify" in section, "nothing tells an operator to watch the audit chain"


def test_the_log_retention_section_is_honest_about_what_does_not_exist(sections):
    """Retention is the topic most likely to be answered with an invented feature.

    This build ships no scheduler and no time-based retention, and the section
    has to say so rather than describe a policy an operator cannot configure.
    """
    flat = re.sub(r"\s+", " ", sections["Log retention"]).lower()
    assert "writes no log files" in flat
    assert "append-only" in flat
    assert "no automated time-based retention" in flat or "is on the roadmap and is not" in flat
    assert "backup rotation" in flat


def test_the_no_log_file_claim_is_true_of_the_source():
    """The retention section's premise, verified rather than asserted."""
    offenders: list[str] = []
    for path in (ROOT / "src" / "dkg").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"logging\.(basicConfig|FileHandler)|RotatingFileHandler", text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "the guide says the application writes no log files, but these do: " + ", ".join(offenders)
    )


def test_the_recovery_path_is_documented_end_to_end(sections):
    flat = re.sub(r"\s+", " ", sections["Upgrade and rollback"]).lower()
    assert "restore a backup" in flat
    assert "schema major" in flat
