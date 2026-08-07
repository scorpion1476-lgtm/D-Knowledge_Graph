# Consumer GitHub Action

`action.yml` at the repository root is a composite GitHub Action that runs the
D-Knowledge Graph source-code analysis on a repository in your own CI and writes
a structural report. It is local-first and air-gapped: it makes no telemetry or
cloud call at runtime (`DKG_ALLOW_OUTBOUND=0`, `DKG_TELEMETRY=0`).

## Usage

```yaml
jobs:
  code-analysis:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - name: D-Knowledge_Graph code analysis
        uses: your-org/D-Knowledge_Graph@<PINNED_TAG_OR_SHA>
        with:
          repository-path: "."
          dkg-repo-url: "https://github.com/your-org/D-Knowledge_Graph.git"
          dkg-ref: "v0.1.0"        # pin to an immutable tag or commit SHA
          base-ref: ${{ github.event.pull_request.base.sha }}
          risk-gate: "off"         # off: report only. Name a level to gate.
```

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `repository-path` | `.` | Path to the repository to analyse. |
| `dkg-ref` | `v0.1.0` | Immutable git tag or commit SHA of the tool to install. Pin this; do not point it at a branch. |
| `dkg-repo-url` | `https://github.com/local/D-Knowledge_Graph.git` | Git URL to install the tool from. Set to your fork or mirror. |
| `base-ref` | `` | Optional git base ref (for example the PR base SHA) for changed-file impact. Empty skips impact analysis. |
| `report-format` | `markdown` | `markdown` or `json`. |
| `risk-gate` | `off` | Gate on a named level: `off`, `low`, `moderate`, `elevated`, `high`. The run fails when the observed level is at or above it. |
| `fail-on-impact` | `` | DEPRECATED. Fails when the impacted-entity count exceeds this integer. Behaviour unchanged; prefer `risk-gate`. |
| `top` | `10` | Rows per table in the rendered review. |
| `marker` | `dkg-code-review` | Hidden marker key. One marker owns one comment thread. |
| `cache` | `true` | Restore and save the built graph between runs. |
| `comment` | `false` | Post the rendered review as a sticky comment. Ignored unless `github-token` is also supplied. |
| `github-token` | `` | Used ONLY to publish the comment. Leave empty to keep the run air-gapped. |
| `pr-number` | `` | Pull-request number. Required when `comment` is true. |
| `api-base` | `https://api.github.com` | API base URL for publication (https only). |

## Outputs

| Output | Description |
| --- | --- |
| `report-file` | Path to the generated report file. |
| `comment-file` | Path to the rendered pull-request review comment. |
| `comment-body` | The rendered comment itself. Consume it through an `env:` binding; never interpolate it into a `run:` body, where it would be parsed as shell. The artifact is the safer route. |
| `impacted-count` | Structural, advisory, over-approximate impacted-entity count (0 when no base ref is given). |
| `risk-level` | Named overall risk level. Reported whether or not the gate is enabled. |
| `risk-score` | Overall risk score in 0 to 1. Reported whether or not the gate is enabled. |
| `gate-failed` | `true` when either gate failed. |
| `cache-status` | `hit`, `miss`, or `unusable`. `unusable` means a restored database failed validation, was removed, and the graph was rebuilt in full. |

## Version pinning (why the tool is installed pinned)

Pinning only the action ref (`@<tag>` or even `@<sha>`) pins the wrapper logic but
not the analysed tool version: a wrapper that installed the tool with no version
constraint would silently float to whatever is latest at run time. This action
avoids that. Because the tool is not yet published to a package index, the action
installs it from the repository at an explicitly pinned ref:

```
pip install "d-knowledge-graph[code] @ git+${dkg-repo-url}@${dkg-ref}"
```

Set `dkg-ref` to an immutable tag or commit SHA you have reviewed. The action does
not accept a floating default such as `main`. Every third-party action the
composite uses (`actions/setup-python`) is pinned to a full commit SHA with a
version comment, matching the repository's CI convention.

The `actions/cache` pin was resolved from the upstream release metadata
(`v5.1.0` is commit `caa296126883cff596d87d8935842f9db880ef25`) and verified
against that repository. It has not been exercised on a hosted runner.

## The merge gate

`risk-gate` is opt-in and off by default. When set to a named level the step
fails if the observed level is at or above it. The levels are `low`,
`moderate`, `elevated`, and `high`, and their score cuts are derived from the
distribution of scores the analysed repository's own graph produces, by
nearest-rank percentile at the 0th, 50th, 75th, and 90th positions. The cuts and
that derivation are printed in the report and in the comment, so the verdict can
be checked rather than taken on trust. A cut therefore means the same thing in a
small library and in a monolith, which a raw count never could.

`low` is the bottom cut, so gating there fails on any scored change. That is
stated rather than hidden.

The score and level are reported whether or not the gate is enabled: turning the
gate off suppresses the failure, never the measurement.

`fail-on-impact` still works exactly as it always did and is now DEPRECATED. It
compares an over-approximate impacted-entity count against an integer, which is
not comparable across repositories and cannot be calibrated. It was kept rather
than redefined, because silently changing what a flag callers depend on means
would be worse than keeping a weak one. Prefer `risk-gate`.

The impacted count remains structural and over-approximate: it is a signal to
prioritise review, not an oracle. Treat this action as one layer in an ensemble
alongside language servers, tests, SAST, and human review.

## The review comment

With `--review` (which the action always passes) the report gains a review block
and the action renders a pull-request comment from it. The comment carries an
overall risk level with its published thresholds, a table of changed symbols
ordered by risk with file and line locations and test-coverage status, the
affected execution flows ordered by criticality, the test gaps, the estimated
token saving, and the standing advisory caveat.

Every symbol name and path in the comment came out of the analysed repository,
which on a fork pull request the author controls. So values are escaped by
ALLOWLIST: alphanumerics and a short list of inert punctuation survive and
everything else becomes a numeric character reference, which renders as the
original character and cannot be parsed as markup. Control and format characters
collapse to a space instead of being faithfully re-encoded. The result has a
checkable invariant: outside the single hidden marker, a rendered comment
contains no angle bracket at all, and `dkg pr-publish` refuses any body that
does.

## The sticky comment

`dkg pr-publish` puts the rendered comment in the one thread its marker owns.
Before every write it looks the marker up, updates the comment it finds, and
creates one only when there is none, so a hundred pushes leave one comment.

Publication is the only outbound call this tool can make and it requires
`--allow-egress`. Without that flag the command validates the body locally and
makes no network call at all. The transport is injected, so the whole path is
covered by tests that drive a fake and never open a socket.

## Fork-safe two-stage publication

Running a fork's code and holding a token that can write to your repository in
the same job hands the token to whoever opened the pull request. The two shipped
workflows split them:

`.github/workflows/pr-review.yml` (unprivileged) triggers on `pull_request`,
checks out and analyses the pull request's code, holds `contents: read` and
nothing else, sees no secret, comments on nothing, and uploads the rendered
review as the `dkg-pr-review` artifact.

`.github/workflows/pr-review-publish.yml` (trusted) triggers on `workflow_run`,
so it runs the copy of itself on your default branch. It holds
`pull-requests: write`. It has NO checkout step, never references the pull
request's head, installs the tool from a ref that is a literal in the file,
downloads the artifact with `gh run download`, and validates it with
`dkg pr-publish` before posting. Validation is not optional and no flag skips
it.

Do not set `comment: true` on a job that has checked out fork code.

## Caching the built graph

With `cache: true` the action restores and saves the graph in the runner
temporary directory. The key covers all three things that make a graph
unusable elsewhere: the runner platform (`runner.os` and `runner.arch`), the
database schema version read from the installed tool, and a hash of the
dependency lockfiles. A `restore-keys` prefix allows an older graph to be
restored when only the lockfiles moved, which is what makes the restore
incremental; it never crosses a platform or schema boundary.

A restored database is validated before it is trusted: it must open, pass a
quick integrity check, carry every required table, and not come from a newer
schema. When it fails, the file and its write-ahead siblings are deleted and the
graph is rebuilt in full, and `cache-status` reports `unusable`. Falling back
costs one slow run; analysing a corrupt restore would cost a wrong answer that
nothing downstream could detect.

## Verification status

Validated locally in this repository:

- `action.yml` and both workflows parse as valid YAML; the action is composite.
- The tool install pins the tool version through `dkg-ref` (no floating
  default), every `uses:` is pinned to a 40-character commit SHA, and every
  input reaches a script through `env:` rather than by interpolation.
- Every embedded shell body passes `bash -n`.
- The Analyze step body was extracted and EXECUTED against a real git
  repository. It emitted every output; a second run reported `cache-status=hit`;
  and with `risk-gate: high` it exited non-zero while still emitting the score
  that failed it.
- The rendered comment passed `dkg pr-publish` validation with no network call.
- The sticky-comment path, the comment rendering and its escaping, the named
  gate, the cache fallback against a deliberately corrupt database, and the
  fork-safety structure of both workflows are covered by tests that were each
  shown to fail against a deliberately broken implementation.

Not yet performed: a live GitHub Actions run. Nothing here has posted a real
comment, restored a real Actions cache, or downloaded a real artifact. The local
checks validate the definitions and the underlying commands, not a hosted run.
The `actions/cache` SHA pin was resolved from the upstream release metadata and
has not been exercised on a runner.
