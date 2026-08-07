# Pull request

Read `CONTRIBUTING.md` before filling this in. It explains what contributing
means under a source-available non-commercial licence: the repository is under
PolyForm Noncommercial 1.0.0 plus a no-modification term, so a proposal is a
proposal, publishing a modified fork is not permitted, and an accepted change is
relicensed into this repository under that same single licence.

## What this changes

<!-- One paragraph. What behaviour is different after this, from a user's point
of view. -->

## Why

<!-- The problem this solves. Link the issue if there is one. -->

## How it was verified

<!-- The exact commands you ran and their result. Not "tests pass": which tests,
and what they printed. -->

```
```

## The test that can fail

<!-- Name the test, and say how you broke the code to watch it go red. A test
that has never failed is not evidence. -->

- Test:
- Mutation used to prove it fails:
- Result: it failed as expected, then passed again once restored.

## Requirement rows

<!-- Which rows in docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv this affects, and
what their status becomes. Write "none" if it touches no row. -->

## Checklist

This mirrors the "what a change has to satisfy" section of `CONTRIBUTING.md`.
Tick what applies; strike out and explain what does not.

### Licence and dependencies

- [ ] Permitted under the licence, and I am not distributing a modified version.
- [ ] No new runtime dependency, or a new one that is permissive (Apache-2.0,
      MIT, BSD, ISC, HPND, or public domain) and declared in `pyproject.toml`
      with a pinned floor.
- [ ] No GPL, LGPL, or AGPL Python-linked or vendored dependency. Any copyleft
      system tool is used only as an optional external binary invoked by
      non-interactive subprocess.
- [ ] `THIRD_PARTY_NOTICES.md`, the lockfile, the software bill of materials,
      and the licence inventory are regenerated if the dependency closure moved.
- [ ] No source copied from another project, and no other tool's name, handle,
      URL, or distinctive vocabulary introduced anywhere in the tree.

### Air gap and capability detection

- [ ] No network call on a runtime path. Any egress is opt-in and warns.
- [ ] No model downloaded at runtime; anything needed is pre-staged and loaded
      local-files-only.
- [ ] Every new capability is capability-detected and reports an honest reason
      when unavailable.
- [ ] The core still installs and passes with no optional extra present, and
      optional-tool tests skip with a reason rather than failing.

### Evidence and honest labelling

- [ ] Every number this change states in a document is read out of an artifact
      under `test-evidence/`, not typed from memory.
- [ ] No status was promoted without an executed test that passes and real
      on-disk evidence.
- [ ] Advisory or over-approximate results still carry the caveat that says so.
- [ ] No forced green anywhere: no skipped assertion, no widened tolerance, no
      gate weakened to make a run pass.

### Code shape

- [ ] Plane-specific logic stayed in its plane; nothing plane-specific went into
      the shared core.
- [ ] Output is deterministic: every list has an explicit sort key with ties
      broken by canonical name.
- [ ] Any new threshold is derived from the data's own distribution and is
      reported with its derivation, not tuned to a corpus.
- [ ] Graph algorithms are iterative, not recursive.
- [ ] SQL is parameterised; nothing interpolates a query parameter.
- [ ] Anything reachable from the MCP surface is read-only, size-capped, and
      confined to a root, with `truncated` covering every capped dimension.

### Documentation

- [ ] `docs/COMMANDS.md` updated if this touched the command line or the MCP
      surface. (`tests/unit/test_docs_commands_complete.py` will fail otherwise.)
- [ ] `docs/ROADMAP.md` updated if this shipped or started something it lists.
- [ ] `CHANGELOG.md` updated under Unreleased.
- [ ] Every new anchor, relative link, and cited repository path resolves.
      (`tests/unit/test_docs_links.py`.)
- [ ] **No em dash and no en dash anywhere in this change**, including in any
      translated document. (`bash scripts/check_dashes.sh`.)

### Gates

Run the ones this change can affect and paste the results above.

- [ ] `python -m ruff check src tests`
- [ ] `python scripts/mypy_gate.py`
- [ ] `bash scripts/check_dashes.sh`
- [ ] `python scripts/secret_scan.py`
- [ ] `python scripts/scrub_scan.py --history`
- [ ] `python scripts/license_inventory.py`
- [ ] `python scripts/validate_traceability.py`
- [ ] `python -m pytest -q`

## Anything the reviewer should know

<!-- Known limitations, things deliberately left undone, and why. Saying so here
is much better than being found out in review. -->
