"""Every workflow must start with no privilege and ask for exactly what it needs.

Acceptance test for matrix row K-09, "CI workflows with least privilege". The
row's acceptance used to be a hosted run producing a green matrix. A green
matrix proves the tests pass; it says nothing about privilege, and a workflow
with a write token it never needed is green right up until something in it is
compromised.

Least privilege here means four concrete things, and each is asserted over
every workflow file rather than over a named list, so a workflow added later
cannot skip the rule:

1. a top-level `permissions:` block exists and grants no more than
   `contents: read`. Without an explicit block, GitHub applies the repository
   default, which on many repositories is write to everything.
2. any elevation is per job, not global, and every elevated scope is one of a
   small reviewed set. A job that needs `pull-requests: write` gets it; the
   workflow does not.
3. no workflow gives `contents: write` at all. Nothing in this repository's
   automation is supposed to push.
4. every third-party action is pinned to a full 40-character commit SHA. A tag
   is mutable, so a tag-pinned action is an unreviewed dependency that can
   change under you, which makes the permission analysis above meaningless.

`pull_request_target` is banned outright. It runs with the base repository's
token while checking out a fork's code, and it is the single most common way a
repository hands a write token to an untrusted contributor.

The YAML is parsed rather than pattern-matched wherever a parser is available,
because `permissions:` nested under a job reads identically to a top-level one
in a regex.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Both spellings. GitHub accepts .yaml as readily as .yml, so globbing one of
# them leaves the other silently unscanned; none exists today, and that is
# exactly when the gap is cheapest to close.
WORKFLOWS = sorted(
    p
    for pattern in ("*.yml", "*.yaml")
    for p in (ROOT / ".github" / "workflows").glob(pattern)
)

# Scopes a job may elevate to, with the reason each is permitted.
ALLOWED_JOB_SCOPES = {
    "contents": {"read"},
    "id-token": {"write"},        # OIDC for keyless signing
    "attestations": {"write"},    # build provenance
    "pull-requests": {"write"},   # the review comment, in the trusted stage only
    "actions": {"read"},          # downloading a workflow-run artifact
    "packages": {"read"},
    "checks": {"read"},
}

SHA_PIN = re.compile(r"^[0-9a-f]{40}$")


def _yaml():
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover
        pytest.skip("PyYAML is not installed in this environment")
    return yaml


def _strict_loader(yaml):
    """A SafeLoader that refuses duplicate mapping keys.

    PyYAML silently keeps the last of a duplicated key, so a `permissions:` block
    written as `contents: write` followed by `contents: read` parses as `read`
    and every assertion below passes on a workflow that GitHub would also read as
    `read`, but which reads to a human as a write grant. Refusing the duplicate
    outright removes the ambiguity rather than picking a winner. An adversarial
    review used exactly this shape to try to slip a write grant past the gate.
    """

    class StrictLoader(yaml.SafeLoader):
        pass

    def _no_duplicates(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise AssertionError(f"duplicate key {key!r} in a workflow mapping")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
    )
    return StrictLoader


@pytest.fixture(scope="module")
def workflows() -> dict[Path, dict]:
    yaml = _yaml()
    loader = _strict_loader(yaml)
    out: dict[Path, dict] = {}
    for path in WORKFLOWS:
        out[path] = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
    return out


def test_no_workflow_declares_a_duplicated_key(workflows):
    """Loading them at all is the assertion; the strict loader raises otherwise."""
    assert workflows, "no workflows parsed"


def test_the_strict_loader_rejects_a_duplicated_permission_key():
    """Negative control, and proof the ambiguity is really removed."""
    yaml = _yaml()
    loader = _strict_loader(yaml)
    sneaky = "permissions:\n  contents: write\n  contents: read\n"
    # The default loader quietly keeps the last one and reports read.
    assert yaml.safe_load(sneaky)["permissions"]["contents"] == "read"
    with pytest.raises(AssertionError, match="duplicate key"):
        yaml.load(sneaky, Loader=loader)


def test_there_are_workflows_to_check():
    assert WORKFLOWS, "no workflow files found; this test would pass vacuously"


# -- 1 and 3: the top-level default -------------------------------------------


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_declares_a_top_level_permissions_block(workflows, path):
    doc = workflows[path]
    assert "permissions" in doc, (
        f"{path.name} declares no top-level permissions, so it inherits the "
        "repository default, which is not least privilege"
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_top_level_default_is_read_only(workflows, path):
    perms = workflows[path]["permissions"]
    assert isinstance(perms, dict), f"{path.name}: permissions is {perms!r}, not a scope map"
    offenders = {k: v for k, v in perms.items() if v != "read"}
    assert not offenders, f"{path.name} grants {offenders} to every job by default"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_can_write_repository_contents(workflows, path):
    doc = workflows[path]
    scopes = [doc.get("permissions") or {}]
    scopes.extend((job or {}).get("permissions") or {} for job in (doc.get("jobs") or {}).values())
    for scope in scopes:
        if isinstance(scope, dict):
            assert scope.get("contents") != "write", (
                f"{path.name} grants contents: write; nothing here should push"
            )


# -- 2: elevation is per job and reviewed --------------------------------------


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_level_scope_is_one_that_was_reviewed(workflows, path):
    unexpected: list[str] = []
    for name, job in (workflows[path].get("jobs") or {}).items():
        perms = (job or {}).get("permissions")
        if not isinstance(perms, dict):
            continue
        for scope, level in perms.items():
            allowed = ALLOWED_JOB_SCOPES.get(scope)
            if allowed is None or level not in allowed:
                unexpected.append(f"{name}: {scope}={level}")
    assert not unexpected, f"{path.name} elevates unreviewed scopes: {unexpected}"


def test_write_access_is_granted_to_jobs_and_never_to_a_whole_workflow(workflows):
    """The shape of the rule: elevation is narrow, and it is job-scoped."""
    elevated_jobs = 0
    for path, doc in workflows.items():
        top = doc.get("permissions") or {}
        assert not any(v == "write" for v in top.values()), path.name
        for job in (doc.get("jobs") or {}).values():
            perms = (job or {}).get("permissions") or {}
            if isinstance(perms, dict) and any(v == "write" for v in perms.values()):
                elevated_jobs += 1
    assert elevated_jobs >= 1, (
        "no job elevates at all, so this test is not distinguishing anything; "
        "the signing and comment jobs are expected to"
    )


# -- untrusted-code triggers ---------------------------------------------------


def _triggers(doc: dict) -> set[str]:
    """The workflow's `on:` keys.

    YAML 1.1 reads the bare word `on` as a boolean, so `safe_load` returns the
    key as `True`. Both spellings are accepted here rather than assuming which
    loader version is installed.
    """
    raw = doc.get("on", doc.get(True))
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return set(raw)
    if isinstance(raw, dict):
        return set(raw)
    return set()


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_uses_pull_request_target(workflows, path):
    """The trigger that combines a privileged token with fork-controlled code.

    Checked against the parsed triggers, not the file text: `pr-review.yml`
    explains in a comment why it deliberately does not use this trigger, and a
    substring search cannot tell that apart from using it.
    """
    triggers = _triggers(workflows[path])
    assert triggers, f"{path.name} declares no triggers at all"
    assert "pull_request_target" not in triggers, (
        f"{path.name} uses pull_request_target; the fork-safe two-stage split exists "
        "precisely so this trigger is never needed"
    )


def test_the_trigger_reader_actually_finds_the_triggers(workflows):
    """Negative control: if `_triggers` returned nothing, the ban above is vacuous."""
    seen = {t for doc in workflows.values() for t in _triggers(doc)}
    assert "pull_request" in seen or "workflow_run" in seen, seen


# -- 4: the pins ----------------------------------------------------------------


def _action_refs(path: Path) -> list[str]:
    return re.findall(r"^\s*uses:\s*([^\s#]+)", path.read_text(encoding="utf-8"), re.M)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_third_party_action_is_pinned_to_a_commit_sha(path):
    unpinned: list[str] = []
    for ref in _action_refs(path):
        if ref.startswith("./") or ref.startswith("docker://"):
            continue  # a local composite action is this repository's own code
        assert "@" in ref, f"{path.name}: {ref} has no ref at all"
        _, version = ref.rsplit("@", 1)
        if not SHA_PIN.fullmatch(version):
            unpinned.append(ref)
    assert not unpinned, f"{path.name} uses tag-pinned actions: {unpinned}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_pinned_action_records_the_human_readable_version(path):
    """A bare SHA is unreviewable; the trailing comment is what makes it auditable."""
    text = path.read_text(encoding="utf-8")
    missing = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"uses:\s*[^\s#]+@[0-9a-f]{40}", line) and "#" not in line.split("uses:", 1)[1]
    ]
    assert not missing, f"{path.name} pins without naming the version: {missing}"


def test_the_pin_check_would_reject_a_tag():
    """Negative control for the SHA matcher."""
    assert not SHA_PIN.fullmatch("v4")
    assert not SHA_PIN.fullmatch("main")
    assert not SHA_PIN.fullmatch("11d5960a326750d5838078e36cf38b85af6772")  # 38 chars
    assert SHA_PIN.fullmatch("11d5960a326750d5838078e36cf38b85af677262")
