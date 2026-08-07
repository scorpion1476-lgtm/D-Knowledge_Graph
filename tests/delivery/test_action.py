"""Consumer GitHub Action definition validation.

Dependency-free structural checks (so this runs in the base environment): the
action is composite, pins the analysed tool version explicitly, SHA-pins every
sub-action, and declares the documented inputs and outputs. When PyYAML is
present, the file is also parsed to confirm it is valid YAML. The action itself
runs on push in a consumer repository; a live CI run is not performed here.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "action.yml"

FLOATING_REFS = {"", "main", "master", "head", "latest", "trunk", "develop"}

REQUIRED_INPUTS = {
    "repository-path",
    "dkg-ref",
    "dkg-repo-url",
    "base-ref",
    "report-format",
    "fail-on-impact",
    "risk-gate",
    "marker",
    "cache",
    "comment",
    "github-token",
    "pr-number",
    "api-base",
    "top",
}
REQUIRED_OUTPUTS = {
    "report-file",
    "comment-file",
    "comment-body",
    "impacted-count",
    "risk-level",
    "risk-score",
    "gate-failed",
    "cache-status",
}


def _text() -> str:
    return ACTION.read_text(encoding="utf-8")


def _input_defaults(text: str) -> dict[str, str]:
    """Parse `name:`/`default:` pairs under the top-level inputs block (2-space indent)."""
    defaults: dict[str, str] = {}
    in_inputs = False
    current: str | None = None
    for line in text.splitlines():
        if re.match(r"^inputs:\s*$", line):
            in_inputs = True
            continue
        if in_inputs and re.match(r"^\S", line):
            break  # left the inputs block
        if not in_inputs:
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            current = m.group(1)
            continue
        d = re.match(r'^    default:\s*"?([^"\n]*)"?\s*$', line)
        if d and current is not None:
            defaults[current] = d.group(1)
    return defaults


def test_action_exists_and_is_composite():
    assert ACTION.exists(), "action.yml must exist at the repository root"
    text = _text()
    assert re.search(r"^\s*using:\s*composite\s*$", text, re.M), "action must be a composite action"
    assert re.search(r"^name:\s*\S", text, re.M)
    assert re.search(r"^description:\s*\S", text, re.M)


def test_action_declares_documented_inputs_and_outputs():
    text = _text()
    for name in REQUIRED_INPUTS:
        assert re.search(rf"^  {re.escape(name)}:\s*$", text, re.M), f"missing input {name}"
    for name in REQUIRED_OUTPUTS:
        assert re.search(rf"^  {re.escape(name)}:\s*$", text, re.M), f"missing output {name}"


def test_tool_install_is_pinned_not_floating():
    text = _text()
    # The install pins the tool version through the dkg-ref input, which reaches
    # the script through the environment rather than by interpolation.
    assert "git+${DKG_REPO_URL}@${DKG_REF}" in text
    assert "DKG_REPO_URL: ${{ inputs.dkg-repo-url }}" in text
    assert "DKG_REF: ${{ inputs.dkg-ref }}" in text
    defaults = _input_defaults(text)
    ref = defaults.get("dkg-ref", "").strip().lower()
    assert ref not in FLOATING_REFS, f"dkg-ref default {ref!r} must be an immutable tag or SHA, not floating"
    assert ref, "dkg-ref must have a pinned default"


def test_every_sub_action_is_sha_pinned():
    text = _text()
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses, "expected at least one sub-action"
    for ref in uses:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"sub-action {ref} is not pinned to a 40-hex commit SHA"


def test_commenting_is_off_by_default_and_doubly_gated():
    text = _text()
    defaults = _input_defaults(text)
    assert defaults.get("comment") == "false", "commenting must be off by default"
    assert defaults.get("github-token") == "", "no token by default"
    # Asking for a comment is not enough on its own, and neither is a token.
    condition = re.search(r"if:\s*\$\{\{\s*(.+?)\s*\}\}", text)
    assert condition is not None, "the publish step must be conditional"
    assert "inputs.comment == 'true'" in condition.group(1)
    assert "inputs.github-token != ''" in condition.group(1)


def test_analysis_declares_the_air_gap_and_renders_the_comment():
    text = _text()
    assert 'DKG_ALLOW_OUTBOUND: "0"' in text
    assert 'DKG_TELEMETRY: "0"' in text
    assert "--review" in text
    assert "--comment-out" in text
    assert "--risk-gate" in text
    assert "--cache-check" in text


def test_inputs_reach_scripts_through_the_environment_not_interpolation():
    """A caller-controlled input interpolated into a `run:` body is parsed as shell.

    Every input is bound to an environment variable and read as `"$VAR"`, so the
    only `${{ }}` expansions inside a script body would be a mistake.
    """
    text = _text()
    in_run = False
    offenders = []
    for line in text.splitlines():
        if re.match(r"^\s*run:\s*\|", line):
            in_run = True
            continue
        if in_run and re.match(r"^    - name:", line):
            in_run = False
        if in_run and "${{ inputs." in line:
            offenders.append(line.strip())
    assert offenders == [], f"inputs interpolated into a script body: {offenders}"


def test_underlying_command_is_wired():
    # `dkg code-report --help` exercises the command wiring without the code extra.
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "dkg", "code-report", "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "--fail-on-impact" in proc.stdout
    assert "--base" in proc.stdout
    assert "--risk-gate" in proc.stdout
    assert "--comment-out" in proc.stdout
    assert "--cache-check" in proc.stdout


def test_publication_command_is_wired():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "dkg", "pr-publish", "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0
    for flag in ("--body-file", "--repo", "--pr", "--marker", "--allow-egress", "--dry-run"):
        assert flag in proc.stdout, flag


def test_publication_refuses_to_reach_the_network_without_the_opt_in(tmp_path):
    """The air-gap default holds at the command boundary, not just in the library."""
    from dkg.code.pr_comment import marker_for

    body = tmp_path / "comment.md"
    body.write_text(f"{marker_for('dkg-code-review')}\n## review\n\nnothing to see\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "GITHUB_TOKEN": "unused"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "dkg",
            "pr-publish",
            "--body-file",
            str(body),
            "--repo",
            "owner/name",
            "--pr",
            "1",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "not-attempted" in proc.stdout
    assert "--allow-egress" in proc.stdout


def test_valid_yaml_when_pyyaml_present():
    try:
        import yaml
    except ImportError:
        return  # PyYAML is not a project dependency; string checks above suffice.
    doc = yaml.safe_load(_text())
    assert doc["runs"]["using"] == "composite"
    assert REQUIRED_INPUTS <= set(doc["inputs"].keys())
    assert REQUIRED_OUTPUTS <= set(doc["outputs"].keys())
