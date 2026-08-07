# Requirements traceability matrix

Every capability in the mission brief has a row in
`REQUIREMENTS_TRACEABILITY_MATRIX.csv`, across capability areas A through V.
This markdown file summarises the matrix and the rules used to populate the
`status` column. The CSV is the authoritative source; when the two disagree,
the CSV wins.

Three views of the same data, all derived from the CSV and never hand-typed:

| Artifact | What it is | Regenerate with |
|---|---|---|
| `REQUIREMENTS_TRACEABILITY_MATRIX.csv` | The source of truth | edited directly |
| `traceability_summary.json` | Machine-readable counts and validation result | `python scripts/validate_traceability.py` |
| `REQUIREMENTS_TRACEABILITY_MATRIX.xlsx` | Formatted, filterable, colour-coded view | `python scripts/export_matrix_xlsx.py` |

The spreadsheet is verified cell by cell against the CSV by
`python scripts/export_matrix_xlsx.py --check`, so the two cannot silently
disagree.

## Status labels

The mission mandates exactly six labels. This project uses them literally.

| Label | Meaning |
|-------|---------|
| `PRODUCTION READY` | Implementation files resolve on disk, the acceptance test is an executable pytest command, and the recorded evidence is a real executed run that passed. |
| `IMPLEMENTED BUT NOT FULLY VERIFIED` | Code and tests exist and the tests pass in this environment, but final acceptance depends on an external input this environment cannot provide. |
| `PARTIAL` | Some but not all sub-behaviours of the requirement are covered. |
| `NOT IMPLEMENTED` | The requirement is not addressed by code or documentation. |
| `BLOCKED BY EXTERNAL PLATFORM` | Cannot be verified here because a required external component is unavailable. |
| `NOT APPLICABLE` | The requirement does not apply to this build. |

`PRODUCTION READY` is intentionally hard to earn. The validator enforces the bar
mechanically and refuses to pass a row that claims it without executed evidence:
merely having an evidence file present is not sufficient, and an evidence file
that records a manual or documentary note rather than an executed run is
rejected. `python scripts/validate_traceability.py --rederive` re-checks every
production-ready row against that bar and reports any that should be
down-labelled.

## Counts

Counts are derived from the CSV by `scripts/validate_traceability.py` and
mirrored to `docs/traceability_summary.json`. The values below are quoted from
that summary; re-run the validator any time the CSV changes.

| Status                              | Count |
|-------------------------------------|------:|
| PRODUCTION READY                    |   263 |
| IMPLEMENTED BUT NOT FULLY VERIFIED  |    10 |
| PARTIAL                             |     4 |
| NOT IMPLEMENTED                     |     6 |
| BLOCKED BY EXTERNAL PLATFORM        |     0 |
| NOT APPLICABLE                      |     0 |
| **Total**                           | **283** |

The sum of the six categories equals the total row count; the validator exits
non-zero if that invariant breaks. It also exits non-zero if the total drifts
from `EXPECTED_TOTAL` in the validator, so a row cannot be added or lost
silently.

## What is not yet built, and why

13 rows are `NOT IMPLEMENTED` or `PARTIAL`. Most were added by the parity
capture: every capability of a public reference implementation of the same
problem domain was enumerated from its own documentation, compared against this
codebase rather than against this repository's claims about itself, and every
genuine shortfall was recorded as a requirement of this project in its own
words. Nothing was built in that step, so no status among them is green, and
none is blocked or not applicable. `PARTIAL` means part of the behaviour exists
and is covered by the tests the row names; the shortfall is stated in the row's
remaining limitation. `NOT IMPLEMENTED` means the capability is absent, and it
is used strictly: a row whose requirement names behaviour none of which exists
is `NOT IMPLEMENTED` even when a neighbouring module would be the one to change.
Adding these rows moved the total from 193 to 278; no pre-existing row was
renumbered, reworded, or relabelled, so the total only grew.

The first build step against that capture took the source-code plane's parsing
to the full language set. It promoted ten of those rows on measured evidence
(N-17 from `PARTIAL`, and N-11 to N-16 and V-01 to V-03 from `NOT IMPLEMENTED`),
updated four rows that were already green or stayed partial, and added three new
rows (N-23, N-24, V-07) for capabilities the capture had not named: the held-out
accuracy corpus, the language inventory surface, and template files as graph
nodes. N-10, the full-language-set row, stays `PARTIAL`: an adversarial review
pointed out that its requirement text names Perl XS, which this build neither
parses nor claims, and a row cannot be production ready against a requirement it
states it does not meet. The total moved from 278 to 281, again with nothing
renumbered. What that step measured is in `docs/BENCHMARKS.md`; what it left
open is on the rows themselves, in the limitation column of the CSV.

The second build step closed the shared-core half of the remaining open rows.
It promoted sixteen on executed evidence (F-19 and Q-04, Q-05, T-09, U-14,
V-06, F-13, F-14, F-15, F-17, F-20, R-12 from `NOT IMPLEMENTED`, and Q-06,
T-10, T-12, F-18 from `PARTIAL`) and added two rows for capabilities the
capture had not named: Q-16, answer-shaped node-level slices, and S-15, the
audit of every grammar inside a bundled dependency. Three in-scope rows stay
honestly open with their reason recorded rather than being promoted: N-10,
because its requirement names Perl XS and no permissive grammar for it exists
anywhere available to this project; F-16, because cross-repository search is
not implemented at all, only the repository listing; and U-15, because its
measurement uses this project's own source rather than a third-party
repository pinned to an explicit commit. The total moved from 281 to 283, again
with nothing renumbered. What each of those rows now claims, and what it still
does not, is on the row itself.

The third build step closed the delivery, user-experience, and semantic-backend
half. It promoted twenty-two rows to `PRODUCTION READY` on executed evidence
(R-07, R-09, R-10, R-11, R-14, R-16, R-17, R-21, J-10, J-11, J-12, J-14, J-17
from `PARTIAL`, and R-22, R-23, J-07, J-08, J-09, J-13, J-15, O-08, O-09 from
`NOT IMPLEMENTED`), and moved R-15, R-18, and R-19 to `IMPLEMENTED BUT NOT
FULLY VERIFIED` rather than green, because each names behaviour that only a
live hosted run can exercise and no such run was performed. No row was added,
so the total stays 283 and nothing was renumbered.

Two in-scope rows stay honestly `PARTIAL`. R-08, because hooks are written for
three of twenty tools and five further tools do document a hook mechanism whose
event vocabulary could not be verified, so writing one would be a guess that
fires on every tool call; refusing to guess is right, but the requirement says
each supported tool's hook definitions. J-16, because four complete
translations ship behind a real drift gate over numbers, code blocks, headings,
tables, and links, and no test can establish that a sentence still means what
the English means; that awaits a native-speaker review.

Four rows in the same areas were outside this step's scope and are untouched:
R-13 and R-24, the single-repository watch command and the managed watcher
service, which belong with the shared-core watch work; R-20, an editor
extension, which is a new client artifact; and R-25, publication to a public
package index, which needs a credentialed outward action nobody has authorised.
Each of those four records its own reason for being out of scope on its row.

Three independent adversarial reviews of the capture found sixteen capabilities
the first pass had missed, sixteen `PARTIAL` labels that the definition above
makes `NOT IMPLEMENTED`, and eleven wrong or overstated row texts. All were
corrected. They also found three defects outside the capture: a dash gate that
could not fail, an evidence manifest that omitted the files a commit added, and
a forbidden-identifier deny-list that did not cover the identifier most likely
to leak. Those were fixed too. Every correction those reviews produced is in the rows
below and in the tests they cite; none of it is recorded only in prose.

## What is not fully verified, and why

10 rows are `IMPLEMENTED BUT NOT FULLY VERIFIED`. Nothing is blocked or not
applicable.

That count was 42. A dedicated verification step read every one of those rows,
ran the real acceptance where an acceptance could be run, and promoted 31 of
them on executed evidence. Most were not blocked by anything: they carried a
manual or shell acceptance, which the strict bar rejects, so the work was to
write an executable test that genuinely exercises the requirement rather than to
build anything new. Nothing was weakened to make a row pass, and each new test
carries a negative control that plants the failure it is supposed to catch, so a
check that stopped working fails rather than passing silently. Four rows in the
group verified with a pre-staged model or language server present (O-01, O-02,
P-03, Q-01); their acceptance ran the real model path with no test skipped, and
an environment without the staged artefacts still skips those tests honestly.
Two defects were found and fixed on the way: the dependency-audit report
recorded an empty result because pip-audit writes its verdict to stderr and only
stdout was captured, and three of the six guides were missing from the README's
documentation index. Each promoted row cites the test that promoted it and the
executed log under `test-evidence/rows/`, which is the account that matters.

The 11 that remain each need an external input this environment genuinely cannot
provide, and each row records both the blocker and the evidence that would close
it:

- **A container runtime this environment may not drive** (F-10, L-05, and L-02
  for the Linux code path): the project's Docker isolation rule forbids
  starting, building or modifying any container, and podman is not installed.
  The Dockerfile, compose file and validation script are checked in, and the
  compose file's loopback-only publication is asserted by an executed test.
- **A Windows machine** (L-03): `docs/DEVELOPER_GUIDE.md` carries an explicit
  instruction not to promote this row until the full suite has run on Windows.
  Promoting it here would contradict the repository's own written gate.
- **An external decoder or model** (M-05, M-08): no HEIF binary is installed and
  no ASR model is pre-staged, and the air-gap rule forbids downloading one at
  runtime. Both degrade paths are tested; ASR is recorded as NOT MEASURED rather
  than scored.
- **A Subversion binary** (N-19): the whole product path runs end to end against
  a stub emitting real-format `svn status -v --xml`, so every line of product
  code executes, but a real working copy does not exist here. This one is
  weaker than the others in this list and is labelled as such in the row:
  Subversion is ordinarily installable, so the blocker is a tool that is absent
  rather than something that cannot be done, and installing a system package was
  outside this session's remit.
- **A live hosted Actions run** (R-15, R-18, R-19): the sticky review comment,
  the graph cache, and the fork-safe two-stage publication are asserted against
  the workflow files and driven by injected fakes, and the fork-safety test was
  shown to fail when a checkout of the head ref is added. No hosted run has
  posted a real comment, restored a real cache, or crossed the workflow-run
  boundary.
- **A signing identity and a real release event** (S-04): the workflow is
  configured for keyless signing and SLSA provenance and validates locally, but
  no release has been signed. K-12 is the executed test that stops any surface
  from claiming otherwise.

A row in this list is honestly not-verified, not a defect. None of them is
marked production ready, and no status is forced green.

One standing caveat is not a row and is recorded here so it does not get lost.
A **third-party MCP client handshake** against the HTTP surface has never been
performed in this environment: it needs an external client, and no such client
has connected. The configuration helper writes the server entry for Claude Code,
Cursor and Windsurf and is verified against fixture configuration only, which is
the whole of what R-06 claims. F-12 is the executed test that stops any document
from implying a handshake was observed.

E-04, contradiction detection, was `PARTIAL`: its unit acceptance passed while
the end-to-end path measurably did not work, detecting 0 of 6 planted
contradictions. Both underlying defects are fixed and the row is
`PRODUCTION READY` on held-out evidence: recall 9 of 9 real disagreements (1.0)
and precision 0.8182 over 11 signals, with two false alarms that stay in the
score. Read the recall figure with its provenance: three of those cases were
expected misses until the matcher was changed in response to them, so for those
three the corpus is a regression suite and not independent evidence. What stays
independent is the negative side, the six cases that must stay silent, which
were untouched by that work. Six of the fifteen held-out cases were contributed
by an adversarial review whose brief was to break the matcher. Two rows were
added for that work, S-08 and S-09, which is why the total moved from 191 to 193.

Four rows moved to `PRODUCTION READY` in the final pass, each on an executed
run rather than a re-reading. N-19 was verified against a real Subversion binary
and a real working copy once Subversion was installed. N-10 stopped reporting
Perl XS unsupported and began reading it with a documented pattern extractor at
fallback fidelity, measured on both an authored and a held-out corpus. R-13
gained a single-repository watch command independent of the registry, and R-24
gained the managed background service the row had always described. Three rows
stay short of it: R-08 because verifying another tool hook vocabulary is a
network and third-party exercise this environment cannot perform, and R-15 and
R-19 because a hosted pull-request run would mean leaving test artifacts on the
public repository.

## Keeping this file honest

The counts above are a snapshot and can go stale if the CSV changes and this
file does not. Before quoting a number from here, confirm it against
`docs/traceability_summary.json`, which is regenerated from the CSV on every
validator run. If the two disagree, the summary is right and this file needs
updating.
