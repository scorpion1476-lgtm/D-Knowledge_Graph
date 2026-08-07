# Dependency and licence policy

## Policy

1. The core runtime has zero third-party dependencies. It ships with the
   Python standard library.
2. Every optional integration is an extra. Installing an extra is a
   conscious choice by the operator.
3. Only permissive licences are accepted for THIRD-PARTY runtime dependencies:
   Apache 2.0, MIT, BSD, ISC, or public domain equivalents.
4. Copyleft licences (GPL, AGPL, LGPL) are not permitted for runtime
   dependencies. Development-only tools may use any OSI-approved licence.
5. Each dependency is pinned by a floor in `pyproject.toml`. A future
   release will introduce a lockfile.
6. `scripts/license_inventory.py` records every installed package and its
   licence into `test-evidence/license_inventory.json`.
7. `scripts/sbom.py` writes a CycloneDX 1.5 JSON SBOM into
   `test-evidence/sbom.cdx.json`.

## Current extras

| Extra      | Package         | Licence     | Purpose |
|------------|-----------------|-------------|---------|
| html       | beautifulsoup4  | MIT         | HTML text extraction |
| html       | lxml            | BSD-3       | XML / HTML backend |
| pdf        | pypdf           | BSD-3       | PDF text extraction |
| rss        | feedparser      | BSD-2       | RSS / Atom parsing |
| web        | httpx           | BSD-3       | Outbound HTTP client |
| embeddings | model2vec       | MIT         | Real local embeddings (numpy inference) |
| reranker   | fastembed       | Apache-2.0  | Local cross-encoder reranker over ONNX |
| media-detect | fastembed     | Apache-2.0  | Zero-shot image detection over ONNX (CLIP, MIT weights) |

Scene/keyframe detection and on-screen keyframe OCR add no Python dependency; they
use the external ffmpeg (copyleft carve-out) and tesseract (Apache-2.0) binaries
by non-interactive subprocess, never vendored or Python-linked.

The `embeddings` and `reranker` transitive closure is all permissive (numpy BSD,
tokenizers Apache-2.0, safetensors Apache-2.0, onnxruntime MIT, huggingface-hub
Apache-2.0, tqdm MIT, requests Apache-2.0, and so on). The pre-staged model
weights are minishlab/potion-base-8M (MIT) and Xenova/ms-marco-MiniLM-L-6-v2
(Apache-2.0); see `docs/model_provenance.json`.

`scripts/license_inventory.py` classifies every installed package and fails loud
if any third-party runtime dependency is GPL, AGPL, or LGPL.

## Language servers for type-aware code resolution (Wave 4a)

Type-aware resolution uses language servers (pyright, MIT; typescript-language-server
and typescript, Apache-2.0) as external Node processes invoked over stdio by
non-interactive subprocess, never Python-linked. They add no Python runtime
dependency and are not in the pip closure, so the licence audit is unaffected.
They are pre-staged and capability-detected, and the code analysis falls back to
structural when they are absent. python-lsp-server was not adopted because it
pulls an LGPL dependency into the pip closure, which the policy forbids.

## This project's own licence

The whole repository, Ariadne included, is under one source-available
non-commercial licence (`LicenseRef-DKG-Source-Available-NonCommercial`): see
`LICENSE`. It is not an open-source licence. There is no separately licensed
component and nothing is excluded from the built wheel.

The permissive-only rule in this document governs THIRD-PARTY dependencies only,
and is unchanged: no third-party GPL, AGPL, or LGPL, linked or vendored. Those
dependencies keep their own licences and are unaffected by the project's terms.

## Development-only

| Package    | Licence | Purpose |
|------------|---------|---------|
| pytest     | MIT     | test framework |
| pytest-cov | MIT     | coverage |
| ruff       | MIT     | lint |
| mypy       | MIT     | static types |

## Vulnerability scanning

`scripts/dep_audit.py` is intentionally not bundled because it would need to
call a public vulnerability database. In CI, add a step that runs
`pip-audit` (or equivalent) against the installed packages and fails the
build on high-severity findings.
