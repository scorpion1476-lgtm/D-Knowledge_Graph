# Third-Party Notices

D-Knowledge Graph is a clean-room implementation.
Its default runtime uses only the Python standard library.

The following third-party packages are optional extras. Each is used unmodified
under its own upstream licence. Nothing in D-Knowledge Graph copies source code
from any other project, and no upstream project is required for the core
platform to install, run, test, and demonstrate its functions.

## Optional runtime extras

| Extra        | Package         | Licence     | Purpose                                   |
|--------------|-----------------|-------------|-------------------------------------------|
| `html`       | beautifulsoup4  | MIT         | HTML parsing for the ingestion pipeline    |
| `html`       | lxml            | BSD-3       | XML/HTML parsing backend                   |
| `pdf`        | pypdf           | BSD-3       | PDF text extraction                        |
| `rss`        | feedparser      | BSD-2       | RSS and Atom parsing                       |
| `web`        | httpx           | BSD-3       | Safe outbound HTTP client for adapters     |
| `embeddings` | model2vec       | MIT         | Real local embeddings (numpy inference)    |
| `reranker`   | fastembed       | Apache-2.0  | Local cross-encoder reranker over ONNX     |
| `media-detect` | fastembed     | Apache-2.0  | Zero-shot image detection over ONNX (CLIP) |
| `media-image` | Pillow         | HPND        | Image decode and EXIF metadata             |
| `asr-faster-whisper` | faster-whisper | MIT  | In-process speech recognition (pre-staged model) |
| `code`       | tree-sitter     | MIT         | Parser runtime for the source-code plane   |
| `code`       | tree-sitter-python | MIT      | Python grammar                             |
| `code`       | tree-sitter-javascript | MIT  | JavaScript grammar                         |
| `code`       | tree-sitter-go  | MIT         | Go grammar                                 |
| `code-extended` | tree-sitter-typescript | MIT | TypeScript and TSX grammars             |
| `code-extended` | tree-sitter-java | MIT      | Java grammar                               |
| `code-extended` | tree-sitter-ruby | MIT      | Ruby grammar                               |
| `code-extended` | tree-sitter-rust | MIT      | Rust grammar                               |
| `code-full`  | tree-sitter-c   | MIT         | C grammar                                  |
| `code-full`  | tree-sitter-cpp | MIT         | C++ grammar                                |
| `code-full`  | tree-sitter-c-sharp | MIT     | C# grammar                                 |
| `code-full`  | tree-sitter-objc | MIT        | Objective-C grammar                        |
| `code-full`  | tree-sitter-zig | MIT         | Zig grammar                                |
| `code-full`  | tree-sitter-kotlin | MIT      | Kotlin grammar                             |
| `code-full`  | tree-sitter-swift | MIT       | Swift grammar                              |
| `code-full`  | tree-sitter-dart | MIT        | Dart grammar                               |
| `code-full`  | tree-sitter-php | MIT         | PHP grammar                                |
| `code-full`  | tree-sitter-lua | MIT         | Lua grammar                                |
| `code-full`  | tree-sitter-luau | MIT        | Luau grammar                               |
| `code-full`  | tree-sitter-julia | MIT       | Julia grammar                              |
| `code-full`  | tree-sitter-scala | MIT       | Scala grammar                              |
| `code-full`  | tree-sitter-elixir | Apache-2.0 | Elixir grammar                          |
| `code-full`  | tree-sitter-bash | MIT        | Bash and ksh grammar                       |
| `code-full`  | tree-sitter-zsh | MIT         | Zsh grammar                                |
| `code-full`  | tree-sitter-powershell | MIT  | PowerShell grammar                         |
| `code-full`  | tree-sitter-solidity | MIT    | Solidity grammar                           |
| `code-full`  | tree-sitter-sql | MIT         | SQL grammar                                |
| `code-full`  | tree-sitter-verilog | MIT     | Verilog and SystemVerilog grammar          |
| `code-full`  | tree-sitter-nix | MIT         | Nix grammar                                |
| `code-full`  | tree-sitter-hcl | Apache-2.0  | Terraform and generic HCL grammar          |
| `code-full`  | tree-sitter-yaml | MIT        | YAML grammar, used to read Ansible         |
| `watch`      | watchfiles      | MIT         | Filesystem watching for the watch daemon   |
| `release`    | build           | MIT         | Reproducible wheel build (release only)    |
| `release`    | sigstore        | Apache-2.0  | Keyless release signing (release only)     |

Every grammar above is permissive: MIT except tree-sitter-elixir and
tree-sitter-hcl, which are Apache-2.0. That is what the permissive-only rule
requires. No grammar is vendored, and none is GPL, AGPL, or LGPL.

### The `code-bundle` extra and its grammars

Five languages publish no dedicated Tree-sitter package this project can depend
on: R, GDScript, ReScript, and VB.NET publish none to PyPI at all, and
tree-sitter-perl, which is MIT, publishes no wheel for every supported platform
and needs the Tree-sitter C headers to build from source.

| Extra          | Component                  | Licence | Purpose                              |
| -------------- | -------------------------- | ------- | ------------------------------------ |
| `code-bundle`  | tree-sitter-language-pack  | MIT     | Bundled grammars for the five below  |

The bundle compiles its grammars into a single shared object, so installing this
extra ships all of them, not only the five that are enabled. Every grammar it
carries is therefore audited, not just the five.

The bundle publishes no per-grammar licence manifest: at version 1.14.3 its
`sources/language_definitions.json` carries a `license` field for 13 of 371
entries, and its `ATTRIBUTIONS.md` covers a vendored Rust crate rather than the
grammars. What it does publish is more useful for attribution: the upstream
repository and the exact revision compiled in, for every grammar. That makes the
licences resolvable from the primary source rather than from a second-hand
summary.

`scripts/audit_grammar_bundle.py` does exactly that and writes
`docs/grammar_bundle_licences.json`, which is committed and checked by a test.
It resolves each grammar strongest evidence first: the licence file in the
repository at the pinned revision, then a declaration in the grammar's own
metadata at that revision, then the bundle manifest's own `license` field.
Nothing unresolved is assumed permissive. That generated manifest is the bulk
attribution for the grammars this extra ships, and it is the authority; the
summary here is a reading of it, not a substitute.

At the audited version all 371 resolved to a permissive licence and **none was
copyleft**: no GPL, no LGPL, no AGPL, no MPL, no EPL, and none left unresolved.
The measured distribution was 321 MIT, 27 Apache-2.0, 9 ISC, 4 BSD-3-Clause, 3
BSD-2-Clause, 2 Apache-2.0 with the LLVM exception (Apache-2.0 plus an
additional permission, so strictly more permissive), 2 Unlicense, 2 WTFPL, and 1
CC0-1.0. WTFPL and CC0 are public-domain equivalents, which the permissive-only
rule admits explicitly.

One grammar (Groovy) resolved through the forge's own licence detection for the
repository rather than from a file at the pinned revision, because it ships no
licence file on the branch the bundle pins. That is the weakest of the three
evidence levels and the generated manifest records it as such per grammar, so
the strength of each result is inspectable rather than flattened into one claim.

This replaces an earlier decision to decline the bundle as unauditable. That
claim was wrong: the bundle is auditable, from the revisions it pins, and the
audit now exists as a regenerable artifact rather than an assertion. The extra is
pinned to an exact version rather than a floor, because the audit is only
meaningful for the revisions that version compiles.

The five grammars actually enabled, with the licence measured at the revision the
bundle compiles (also pinned in `dkg.code.capability.BUNDLE_GRAMMAR_SOURCES`):

| Language | Upstream grammar                                  | Licence | Revision   |
| -------- | ------------------------------------------------- | ------- | ---------- |
| R        | github.com/r-lib/tree-sitter-r                    | MIT     | `58a22794` |
| GDScript | github.com/PrestonKnopp/tree-sitter-gdscript      | MIT     | `c5c8fa48` |
| ReScript | github.com/rescript-lang/tree-sitter-rescript     | MIT     | `19ed8a8e` |
| VB.NET   | github.com/CodeAnt-AI/tree-sitter-vb-dotnet       | MIT     | `cfca210c` |
| Perl     | github.com/tree-sitter-perl/tree-sitter-perl      | MIT     | `0390ac6f` |

Four of the five ship a licence file at that revision. The VB.NET grammar ships
no licence text in-tree; it declares MIT in four separate metadata files
(`package.json`, `tree-sitter.json`, `Cargo.toml`, `pyproject.toml`) at the
pinned revision. That is a declaration rather than a licence text, and it is
recorded as such here and in the generated manifest rather than being rounded up.

`code-bundle` is optional. Without it these five degrade to the documented
pattern extractor in `src/dkg/code/fallback.py`, which adds no dependency, and
the language inventory reports which of the two actually ran rather than
claiming grammar fidelity a build does not have.

A grammar a project registers itself through the project-owned language config
is user-provided and is not a dependency of this platform, so it is not listed
here; the config records its declared licence and the loader warns when that
licence is not permissive.

Scene and keyframe detection and on-screen OCR add no Python dependency; they use
the external ffmpeg and tesseract binaries (see the media plane carve-out).

The `embeddings` and `reranker` extras pull a permissive transitive closure:
numpy (BSD), tokenizers (Apache-2.0), safetensors (Apache-2.0), onnxruntime
(MIT), huggingface-hub (Apache-2.0), tqdm (MIT), jinja2 (BSD), joblib (BSD),
requests (Apache-2.0), loguru (MIT), mmh3 (public domain / MIT), py-rust-stemmers
(MIT), Pillow (HPND). None are GPL, AGPL, or LGPL.

## Pre-staged local models

These optional model weights are downloaded once at build or CI time (never at
runtime) and are loaded local-files-only. Provenance and checksums are recorded
in `docs/model_provenance.json`.

| Capability | Model                              | Weight licence |
|------------|------------------------------------|----------------|
| embeddings | minishlab/potion-base-8M           | MIT            |
| reranker   | Xenova/ms-marco-MiniLM-L-6-v2      | Apache-2.0     |
| media-detect | Qdrant/clip-ViT-B-32-vision      | MIT            |
| media-detect | Qdrant/clip-ViT-B-32-text        | MIT            |

## External language servers (Wave 4a)

Type-aware code resolution uses language servers as external Node processes over
stdio (never Python-linked), so they add no Python runtime dependency and do not
enter the pip closure or the licence audit. They are pre-staged (npm) and
capability-detected, like the ffmpeg and tesseract binaries.

| Language   | Server                       | Licence    |
|------------|------------------------------|------------|
| Python     | pyright (pyright-langserver) | MIT        |
| JavaScript | typescript-language-server   | Apache-2.0 |
| JavaScript | typescript (5.x, tsserver)   | Apache-2.0 |

Go type-aware resolution is not adopted this wave (no Go toolchain assumed); Go
code analysis remains structural. python-lsp-server was deliberately not used
because it pulls an LGPL dependency (docstring-to-markdown) into the pip closure.

## This project's own modules

The Ariadne community-detection module (`src/dkg/ariadne/`) is covered by the
repository licence at the root, the same source-available non-commercial terms
that cover every other file here. It is not a third-party dependency, it is not
separately licensed, and it is not excluded from the built wheel. It is listed
here only because earlier versions of this document described it as an
exception.

## Development-only

| Package        | Licence | Purpose             |
|----------------|---------|---------------------|
| pytest         | MIT     | Test framework      |
| pytest-cov     | MIT     | Coverage measurement |
| ruff           | MIT     | Lint and format     |
| mypy           | MIT     | Static type checks  |

