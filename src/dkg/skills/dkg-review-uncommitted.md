---
name: dkg-review-uncommitted
kind: workflow-command
title: Review the uncommitted delta
description: Review the working-tree changes that are not committed yet, using the code graph for blast radius, risk, and the tests that cover what moved.
cli: update, code-impact, code-risk, code-questions, code-report
mcp: dkg.code.change, dkg.code.impact, dkg.code.risk, dkg.code.tests_for, dkg.code.review_context, dkg.code.questions
bounds: read-only, offline, structural and over-approximate, advisory not a gate
---

# Review the uncommitted delta

Use this before you commit. It answers "what else does this touch, and what
should I have looked at" from the graph rather than from a file-by-file read.

## Tools this drives

- MCP (read-only): `dkg.code.change`, `dkg.code.impact`, `dkg.code.risk`,
  `dkg.code.tests_for`, `dkg.code.review_context`, `dkg.code.questions`.
- CLI: `dkg update`, `dkg code-impact`, `dkg code-risk`, `dkg code-questions`,
  `dkg code-report`.

## Steps

1. Bring the graph in step with the working tree, otherwise every answer below
   describes the code as it was.

   ```bash
   dkg update --repo .
   ```

2. List the changed files with `git status --porcelain`, then ask the graph what
   each one reaches.

   ```bash
   dkg code-impact --file src/example/module.py --depth 3 --max-nodes 500
   ```

   The `dkg.code.change` MCP tool takes the changed set in one call and is the
   better route when you are already connected.

3. Score the change set as a whole. Every factor's contribution is reported, so
   you can see why the number is what it is.

   ```bash
   dkg code-risk --file src/example/module.py --symbol example.module.entry
   ```

4. Find the tests that already cover what moved, with `dkg.code.tests_for`, and
   read the surrounding callers and callees with `dkg.code.review_context`.

5. Turn the analysis into questions to answer before committing.

   ```bash
   dkg code-questions --limit 20
   ```

6. If you want one artifact to paste into a commit message or a review, render
   the structural summary.

   ```bash
   dkg code-report . --format markdown
   ```

## Bounds this runs under

- Every tool named here is read-only except `dkg update`, which only refreshes
  the graph. Nothing in this workflow edits your source.
- Offline. No network call and no telemetry.
- The underlying call and reference edges are structural and over-approximate.
  Impact and risk over-flag rather than under-flag. They are advisory prompts
  for a human, not findings, and never a merge gate on their own.
- Traversal is bounded: `--depth` and `--max-nodes` cap the walk and the result
  reports when it was truncated. Raising the caps costs time and widens the
  over-approximation.
- Risk scoring reads git history only when you pass `--with-churn`. It is off by
  default.
