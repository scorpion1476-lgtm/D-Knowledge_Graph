# Language inventory

Generated from the live language registry by `python scripts/language_inventory.py`. Do not hand-edit.

The source-code plane covers **42 languages and containers**, in opt-in extras so a minimal install stays minimal.

| Fidelity | Languages | What it means |
| --- | ---: | --- |
| `grammar` | 29 | a real Tree-sitter parse of the whole file |
| `composite` | 7 | the file is unwrapped first (a notebook's code cells, a component's script block, an infrastructure file's block structure) and its code is then parsed with another language's grammar |
| `fallback` | 1 | the documented line-oriented pattern extractor, used where no dedicated permissive grammar package is installable. Honestly lower fidelity: every edge leaving such a file is confidence-scaled and the language is never reported as though it had been parsed |
| `grammar or fallback` | 5 | `grammar` when the optional `code-bundle` extra is installed, and the documented pattern extractor at `fallback` fidelity when it is not. Which one is in force is a property of the machine, so both are stated here |

Whether a given grammar is installed on *your* machine is a property of your environment, not of this project, so it is not recorded here. Run `dkg code-languages` for the live answer, which reports the fidelity actually in force rather than the best case.

## `code` (5)

The starter set. Installed by `pip install -e ".[code]"`.

| Language | Extensions | Fidelity | How it is read | Licence |
| --- | --- | --- | --- | --- |
| databricks | detected by content | `composite` | notebook code cells, parsed with the notebook language grammar | MIT |
| go | `.go` | `grammar` | tree_sitter_go grammar | MIT |
| javascript | `.cjs`, `.js`, `.jsx`, `.mjs` | `grammar` | tree_sitter_javascript grammar | MIT |
| jupyter | `.ipynb` | `composite` | notebook code cells, parsed with the kernel language grammar | MIT |
| python | `.py`, `.pyi` | `grammar` | tree_sitter_python grammar | MIT |

## `code-extended` (8)

Common application languages and single-file components.

| Language | Extensions | Fidelity | How it is read | Licence |
| --- | --- | --- | --- | --- |
| astro | `.astro` | `composite` | frontmatter script, parsed with the TypeScript grammar | MIT |
| java | `.java` | `grammar` | tree_sitter_java grammar | MIT |
| ruby | `.rb` | `grammar` | tree_sitter_ruby grammar | MIT |
| rust | `.rs` | `grammar` | tree_sitter_rust grammar | MIT |
| svelte | `.svelte` | `composite` | script blocks, parsed with the JavaScript or TypeScript grammar | MIT |
| tsx | `.tsx` | `grammar` | tree_sitter_typescript grammar | MIT |
| typescript | `.cts`, `.mts`, `.ts` | `grammar` | tree_sitter_typescript grammar | MIT |
| vue | `.vue` | `composite` | script blocks, parsed with the JavaScript or TypeScript grammar | MIT |

## `code-full` (23)

The remaining grammars, including shells, systems languages, and infrastructure formats.

| Language | Extensions | Fidelity | How it is read | Licence |
| --- | --- | --- | --- | --- |
| ansible | `.yaml`, `.yml` | `composite` | YAML grammar, with plays and tasks mapped to symbols | MIT |
| bash | `.bash`, `.ksh`, `.sh` | `grammar` | tree_sitter_bash grammar | MIT |
| c | `.c`, `.h` | `grammar` | tree_sitter_c grammar | MIT |
| cpp | `.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx` | `grammar` | tree_sitter_cpp grammar | MIT |
| csharp | `.cs` | `grammar` | tree_sitter_c_sharp grammar | MIT |
| dart | `.dart` | `grammar` | tree_sitter_dart grammar | MIT |
| elixir | `.ex`, `.exs` | `grammar` | tree_sitter_elixir grammar | Apache-2.0 |
| hcl | `.hcl`, `.nomad`, `.tf`, `.tfvars` | `composite` | HCL grammar, with blocks mapped to Terraform addresses | Apache-2.0 |
| julia | `.jl` | `grammar` | tree_sitter_julia grammar | MIT |
| kotlin | `.kt`, `.kts` | `grammar` | tree_sitter_kotlin grammar | MIT |
| lua | `.lua` | `grammar` | tree_sitter_lua grammar | MIT |
| luau | `.luau` | `grammar` | tree_sitter_luau grammar | MIT |
| nix | `.nix` | `grammar` | tree_sitter_nix grammar | MIT |
| objc | `.m`, `.mm` | `grammar` | tree_sitter_objc grammar | MIT |
| php | `.php`, `.php4`, `.php5`, `.phtml` | `grammar` | tree_sitter_php grammar | MIT |
| powershell | `.ps1`, `.psd1`, `.psm1` | `grammar` | tree_sitter_powershell grammar | MIT |
| scala | `.sc`, `.scala` | `grammar` | tree_sitter_scala grammar | MIT |
| solidity | `.sol` | `grammar` | tree_sitter_solidity grammar | MIT |
| sql | `.sql` | `grammar` | tree_sitter_sql grammar | MIT |
| swift | `.swift` | `grammar` | tree_sitter_swift grammar | MIT |
| verilog | `.sv`, `.svh`, `.v`, `.vh` | `grammar` | tree_sitter_verilog grammar | MIT |
| zig | `.zig` | `grammar` | tree_sitter_zig grammar | MIT |
| zsh | `.zsh` | `grammar` | tree_sitter_zsh grammar | MIT |

## `code-bundle` (5)

Languages with no installable dedicated permissive grammar package. They are read through a bundle whose every grammar has been licence-audited into `docs/grammar_bundle_licences.json`. Without this extra they degrade to the documented pattern extractor at `fallback` fidelity, which is labelled as such everywhere it surfaces and never presented as a real parse.

| Language | Extensions | Fidelity | How it is read | Licence |
| --- | --- | --- | --- | --- |
| gdscript | `.gd` | `grammar` with the extra, `fallback` without | tree_sitter_language_pack bundled grammar | MIT |
| perl | `.pl`, `.pm`, `.t` | `grammar` with the extra, `fallback` without | tree_sitter_language_pack bundled grammar | MIT |
| r | `.R`, `.r`, `.rmd` | `grammar` with the extra, `fallback` without | tree_sitter_language_pack bundled grammar | MIT |
| rescript | `.res`, `.resi` | `grammar` with the extra, `fallback` without | tree_sitter_language_pack bundled grammar | MIT |
| vbnet | `.vb` | `grammar` with the extra, `fallback` without | tree_sitter_language_pack bundled grammar | MIT |

## No extra (always the pattern extractor) (1)

Perl XS. No permissive Tree-sitter grammar for `.xs` exists in any source available to this project, including the multi-grammar bundle, so there is no extra to install and no upgrade to offer. It is read by the documented pattern extractor in `src/dkg/code/xs.py` at `fallback` fidelity, is never presented as a parse, and every edge leaving such a file is confidence-scaled.

| Language | Extensions | Fidelity | How it is read | Licence |
| --- | --- | --- | --- | --- |
| xs | `.xs` | `fallback` | documented pattern extractor | not applicable |

## Licence position

Every shipped grammar is permissive. No GPL, LGPL, or AGPL grammar is used and none is vendored. Notices are in `THIRD_PARTY_NOTICES.md`, and the bundle audit that establishes the position for the `code-bundle` extra is in `docs/grammar_bundle_licences.json`.

## Accuracy

Parse accuracy is measured per language against two labelled corpora and published in `docs/BENCHMARKS.md`. A language whose optional grammar is not installed in the measuring environment is reported not measured, never scored zero.
