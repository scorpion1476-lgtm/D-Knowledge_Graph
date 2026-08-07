"""The fork-safe two-stage publication (R-19).

The threat is specific. On a pull request from a fork, the author controls every
file in the tree under analysis, including the workflow files, the build
scripts, and the source the parser reads. A single workflow that both ran that
code and held a token able to write to the repository would hand the token to
whoever opened the pull request.

So the work is split, and this module asserts the split structurally against the
two workflow files rather than trusting the comments in them:

  STAGE ONE (pr-review.yml) checks out and runs the pull request's code. It has
  read permission and nothing else, sees no secret, and posts nothing. It emits
  the rendered review as a step output and uploads it as an artifact.

  STAGE TWO (pr-review-publish.yml) holds `pull-requests: write`. It runs from
  the default branch on `workflow_run`, never checks anything out, never
  references the pull request's head, installs the tool from a ref that is a
  literal in the file, and validates the downloaded artifact before posting it.

Regex checks run unconditionally so this is meaningful in the base environment;
the structural checks are repeated against the parsed document when PyYAML is
present, which it is in the development environment.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNPRIVILEGED = ROOT / ".github" / "workflows" / "pr-review.yml"
PRIVILEGED = ROOT / ".github" / "workflows" / "pr-review-publish.yml"

# Every way a workflow can name the pull request's own commit or branch. Any of
# these in the privileged stage is a route to executing the fork's code.
HEAD_REFERENCES = (
    "github.event.pull_request.head",
    "github.event.workflow_run.head_sha",
    "github.event.workflow_run.head_branch",
    "github.event.workflow_run.head_repository",
    "github.event.workflow_run.head_commit",
    "github.head_ref",
)

WRITE_SCOPES = (
    "contents: write",
    "packages: write",
    "id-token: write",
    "actions: write",
    "deployments: write",
    "statuses: write",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _directives(path: Path) -> str:
    """The file with whole-line YAML comments removed.

    The workflows explain in prose exactly which constructs they avoid, so a
    check for the ABSENCE of a construct has to look at the directives rather
    than at the commentary that names them.
    """
    return "\n".join(
        line for line in _text(path).split("\n") if not line.lstrip().startswith("#")
    )


def _doc(path: Path):
    import yaml

    # `on:` is the YAML 1.1 boolean True, which is why it is looked up as such.
    return yaml.safe_load(_text(path))


def _triggers(doc) -> set:
    return set((doc.get("on") or doc.get(True) or {}).keys())


# -- both files exist and parse ----------------------------------------------


def test_both_stages_exist():
    assert UNPRIVILEGED.is_file(), "the unprivileged analysis stage must exist"
    assert PRIVILEGED.is_file(), "the trusted publication stage must exist"


def test_both_stages_are_valid_yaml():
    try:
        import yaml  # noqa: F401
    except ImportError:
        return
    for path in (UNPRIVILEGED, PRIVILEGED):
        doc = _doc(path)
        assert isinstance(doc, dict), path
        assert doc.get("jobs"), path


def test_every_sub_action_in_both_stages_is_sha_pinned():
    for path in (UNPRIVILEGED, PRIVILEGED):
        for ref in re.findall(r"uses:\s*(\S+)", _text(path)):
            if ref.startswith("./"):
                continue  # the composite action in this repository
            assert re.search(r"@[0-9a-f]{40}$", ref), f"{path.name}: {ref} is not SHA-pinned"


# -- stage one: unprivileged, read only, never comments ----------------------


def test_the_analysis_stage_runs_unprivileged_on_pull_request():
    text = _text(UNPRIVILEGED)
    assert re.search(r"^on:\s*$", text, re.M)
    assert re.search(r"^  pull_request:\s*$", text, re.M)
    # pull_request_target runs with the base repository's secrets against the
    # fork's code, which is exactly the arrangement this design avoids.
    assert "pull_request_target" not in _directives(UNPRIVILEGED)


def test_the_analysis_stage_has_no_write_permission():
    text = _directives(UNPRIVILEGED)
    assert re.search(r"^permissions:\s*$", text, re.M), "a permissions block must be declared"
    assert "pull-requests: write" not in text
    assert "issues: write" not in text
    for scope in WRITE_SCOPES:
        assert scope not in text, f"the analysis stage must not hold {scope}"


def test_the_analysis_stage_permissions_parse_as_read_only():
    try:
        import yaml  # noqa: F401
    except ImportError:
        return
    doc = _doc(UNPRIVILEGED)
    for scope, level in (doc.get("permissions") or {}).items():
        assert level == "read", f"top-level {scope} is {level}"
    for name, job in doc["jobs"].items():
        permissions = job.get("permissions")
        assert permissions, f"job {name} must declare its own permissions"
        for scope, level in permissions.items():
            assert level == "read", f"job {name}: {scope} is {level}"


def test_the_analysis_stage_sees_no_secret_and_never_comments():
    text = _directives(UNPRIVILEGED)
    assert "secrets." not in text, "the unprivileged stage must not be handed a secret"
    assert "pr-publish" not in text, "the unprivileged stage must not post anything"
    assert "github-token" not in text
    assert re.search(r'comment:\s*"false"', text), "commenting must be explicitly off"


def test_the_analysis_stage_emits_the_report_as_an_output_and_an_artifact():
    text = _text(UNPRIVILEGED)
    # An output of the composite action, consumed by the upload step.
    assert "steps.review.outputs.comment-file" in text
    assert "actions/upload-artifact@" in text
    assert "name: dkg-pr-review" in text
    assert "if-no-files-found: error" in text, "a missing report must fail, not pass quietly"


# -- stage two: trusted, never touches the pull request's code ---------------


def test_the_publication_stage_runs_only_from_the_default_branch():
    text = _directives(PRIVILEGED)
    assert re.search(r"^  workflow_run:\s*$", text, re.M)
    assert "pull_request_target" not in text
    assert "pull_request:" not in text
    try:
        import yaml  # noqa: F401
    except ImportError:
        return
    assert _triggers(_doc(PRIVILEGED)) == {"workflow_run"}


def test_the_publication_stage_never_checks_out_anything():
    text = _directives(PRIVILEGED)
    uses = re.findall(r"uses:\s*(\S+)", text)
    for ref in uses:
        assert not ref.startswith("actions/checkout"), (
            "the privileged stage must not check out a repository: it holds a "
            f"write token and {ref} would give it the pull request's code"
        )
    assert "actions/checkout" not in text
    assert "git clone" not in text


def test_the_publication_stage_never_references_the_pull_requests_head():
    text = _directives(PRIVILEGED)
    for reference in HEAD_REFERENCES:
        assert reference not in text, f"the privileged stage references {reference}"


def test_the_publication_stage_installs_a_literal_pinned_ref():
    text = _text(PRIVILEGED)
    match = re.search(r'DKG_REF:\s*"([^"]+)"', text)
    assert match, "the tool version must be pinned in the privileged stage"
    ref = match.group(1)
    assert "${{" not in ref, "the installed ref must not come from the event payload"
    assert ref.lower() not in ("", "main", "master", "head", "latest", "develop", "trunk")
    # Nothing anywhere in the install step is interpolated from the event.
    install = _step_block(text, "Install the tool from a pinned trusted ref")
    assert "github.event" not in install


def test_the_publication_stage_holds_only_the_permission_it_needs():
    try:
        import yaml  # noqa: F401
    except ImportError:
        return
    doc = _doc(PRIVILEGED)
    job = doc["jobs"]["publish"]
    permissions = job["permissions"]
    assert permissions.get("pull-requests") == "write", "it must be able to comment"
    assert permissions.get("contents") == "read"
    assert permissions.get("actions") == "read"
    assert "id-token" not in permissions
    assert "packages" not in permissions


def test_the_publication_stage_only_runs_on_a_successful_pull_request_analysis():
    try:
        import yaml  # noqa: F401
    except ImportError:
        return
    condition = _doc(PRIVILEGED)["jobs"]["publish"]["if"]
    assert "workflow_run.event == 'pull_request'" in condition
    assert "workflow_run.conclusion == 'success'" in condition


def test_the_publication_stage_validates_the_artifact_before_posting():
    text = _directives(PRIVILEGED)
    assert "dkg pr-publish" in text
    # There is no way to turn validation off, and nothing tries to.
    assert "--skip-validation" not in text
    assert "--no-validate" not in text
    assert "--force" not in text
    # The pull-request number from the artifact is stripped to digits.
    assert "tr -cd '0-9'" in text
    assert "--allow-egress" in text


def test_publication_validation_is_not_optional_in_the_code_either():
    from dkg.code.pr_publish import publish_sticky_comment

    calls = []

    def transport(request):
        calls.append(request)
        raise AssertionError("a rejected body must not reach the transport")

    result = publish_sticky_comment(
        transport=transport,
        repo="owner/name",
        pr_number=1,
        body="no marker here at all",
        token="t0ken",
        marker_key="dkg-code-review",
    )
    assert result["action"] == "rejected"
    assert calls == []


# -- helpers -----------------------------------------------------------------


def _step_block(text: str, step_name: str) -> str:
    """The lines of one named step, up to the next step at the same level."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {step_name}")
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for line in lines[start + 1 :]:
        stripped = line.lstrip()
        if stripped.startswith("- name:") and (len(line) - len(stripped)) <= indent:
            break
        out.append(line)
    return "\n".join(out)
