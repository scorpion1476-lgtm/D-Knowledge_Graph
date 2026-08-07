---
name: dkg-review-branch-diff
kind: workflow-command
title: Review a branch or pull-request diff
description: Review every change on a branch against its base ref, using the code graph for changed-file blast radius, affected execution flows, and co-change accuracy.
cli: code-report, code-impact, code-flow, code-cochange, code-risk, graph-snapshot, graph-diff
mcp: dkg.code.change, dkg.code.impact, dkg.code.flows.affected, dkg.code.risk, dkg.code.review_context, dkg.graph.diff
bounds: read-only, offline, structural and over-approximate, advisory gate is opt-in
---

# Review a branch or pull-request diff

Use this on a branch, a fork, or a pull request, where the unit of review is
"everything since the base ref" rather than the working tree.

## Tools this drives

- CLI: `dkg code-report`, `dkg code-impact`, `dkg code-flow`,
  `dkg code-cochange`, `dkg code-risk`, `dkg graph-snapshot`, `dkg graph-diff`.
- MCP (read-only): `dkg.code.change`, `dkg.code.impact`,
  `dkg.code.flows.affected`, `dkg.code.risk`, `dkg.code.review_context`,
  `dkg.graph.diff`.

## Steps

1. Produce the branch report against the base ref. This is the same command the
   consumer GitHub Action runs.

   ```bash
   dkg code-report . --base origin/main --format markdown --out review.md
   ```

   Add `--fail-on-impact N` only if you want the advisory structural gate to
   exit non-zero above a threshold you chose. It is opt-in and off by default.

2. For each symbol the report names as high impact, follow the call chain
   forward so you can see what actually executes differently.

   ```bash
   dkg code-flow "src/example/module.py::entry" --depth 4
   ```

   `dkg.code.flows.affected` answers the same question for the whole changed
   set in one call.

3. Score the branch as a change set.

   ```bash
   dkg code-risk --file src/example/module.py --with-churn --repo .
   ```

4. Sanity-check the blast radius against a signal that does not come from the
   graph itself. `dkg code-cochange` measures the structural impact answer
   against what git history says actually changes together.

   ```bash
   dkg code-cochange --repo . --depth 3
   ```

5. If you want a structural before-and-after rather than a point-in-time view,
   snapshot the graph on the base ref and diff it against the branch.

   ```bash
   git checkout origin/main && dkg update --repo . && dkg graph-snapshot base.json
   git checkout - && dkg update --repo . && dkg graph-snapshot branch.json
   dkg graph-diff base.json branch.json
   ```

## Bounds this runs under

- Every analysis tool named here is read-only with respect to the graph.
  `dkg code-report` writes only the report file you name with `--out`.
- Offline. No network call and no telemetry. `--base origin/main` reads refs
  that are already in your local clone; fetching them is your job, not this
  workflow's.
- The changed-file blast radius is structural and over-approximate. It
  over-flags. Treat it as advisory, and never let `--fail-on-impact` stand in
  for a human review.
- Traversal is bounded by `--depth` and `--max-nodes`, and a truncated result
  says so.
- `dkg code-cochange` reads git history and is bounded by `--max-commits` and
  `--max-commit-files`; commits wider than the file cap are excluded so a single
  sweeping commit cannot dominate the measurement.
- Snapshot community indices are per-run labels. Compare co-membership sets
  across two snapshots, never the index numbers.
