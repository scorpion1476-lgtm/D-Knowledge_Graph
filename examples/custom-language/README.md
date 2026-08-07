# Custom-language worked example: Ruby

The source-code plane ships with built-in parsers for Python, JavaScript, and Go.
You can register additional Tree-sitter languages without forking the project by
placing a project-owned `dkg.languages.json` config file in your DKG home (or
passing `--languages PATH` to `dkg code-ingest`). This folder registers Ruby end
to end.

## Files

- `dkg.languages.json` - the language registration. Each entry names an
  importable Tree-sitter grammar module, its licence, the file extensions it
  owns, and a mapping from symbol categories (`class`, `function`, `method`,
  `type`, `test`, `module`) to that grammar's node types. A single generic
  extractor uses the mapping to build the same code-graph symbols and edges the
  built-in parsers produce.
- `greeter.rb` - a small Ruby sample.

## No grammar install needed for a bundled language

The `code-full` extra bundles all thirty permissive grammars, the starter three included. If the language you
want to register is one of them, the grammar is already installed and the config
file is the only thing you write. Run `dkg code-languages` to see what is
bundled and what is available in your environment. A grammar the pack does not
carry stays a user-provided input, installed the way the Ruby example below
installs it.

## Every configuration key

The config describes how a grammar spells its definitions. Only `name`,
`grammar_module`, `licence`, `extensions`, and `symbols` are required; the rest
exist because real grammars disagree about where a name or a base class lives.

| Key | What it does |
|-----|--------------|
| `name` | The language name symbols are recorded under. |
| `grammar_module` | The importable Tree-sitter grammar module. |
| `licence` | Declared licence. Only permissive licences are supported. |
| `extensions` | File extensions this language owns. |
| `symbols` | Symbol category to grammar node types. |
| `name_field` | Field holding a definition's name. Falls back to `name`, `declarator`, `function_name`, `target`, `pattern`, then to the first identifier in source order. |
| `name_node_types` | Overrides which node types count as an identifier. |
| `name_skip_types` | Subtrees not searched for a name, for a language that writes the return type first. |
| `owner_field` | Field on the name node naming the type that owns the definition. |
| `default_names` | Fixed names for definitions that have none, such as a constructor. |
| `calls.node_types` | Node types that are a call. |
| `calls.name_field` | Field holding the callee. Falls back to `function`, `name`, `method`, `command_name`. |
| `calls.prev_sibling` | Read the callee from the preceding sibling, for a grammar with no call node. |
| `calls.require_child` | Only a call when it has a descendant of one of these types. |
| `calls.import_keywords` | Callee names that mean this is an import, not a call. |
| `imports.node_types` | Node types that are an import. |
| `imports.name_field` | Field holding the import target. |
| `inherits.field` | Field naming the base type. |
| `inherits.node_types` | Unnamed child wrappers carrying base types. |
| `bindings.node_types` | Nodes whose category depends on what they bind. |
| `bindings.values` | Bound node type to symbol category. |
| `bindings.first_child` | Only the first named child decides the category. |
| `keywords.node_types` | Node types whose target keyword decides the category. |
| `keywords.symbols` | Keyword to symbol category. |
| `keywords.imports` | Keywords that mean an import. |
| `keywords.name_field` | Field or child type holding the name. |
| `scope.node_types` | Nodes that name a scope without being a symbol. |
| `scope.name_field` | Field on a scope node naming the scope. |
| `body_node_types` | Blocks implementing the definition named by the preceding sibling. |
| `skip_node_types` | Subtrees never walked. |
| `test_node_types` | Node types that are always a test marker. |
| `test_prefix` | Name prefix that marks a definition as a test. |

Every key is validated when the config loads: an unknown symbol category, a
non-object section, a missing licence, or a duplicate language name is rejected
with the path to the offending entry, so a typo is a clear error rather than a
language that silently extracts nothing.

## Licence rule (permissive grammars only)

Every entry must declare a `licence`. Only permissive grammars are supported
(MIT, BSD, Apache-2.0, ISC and equivalents). The loader records the declared
licence and warns when it is not on the permissive allow-list. The Ruby grammar
used here, `tree-sitter-ruby`, is MIT. The grammar is a user-provided input, not
a dependency of the platform: it is never vendored, and it is capability-detected
at parse time so the platform works unchanged when it is absent.

## Run it

Install the grammar (a user-provided input, not a platform dependency):

```
pip install tree-sitter-ruby
```

Then ingest a repository that contains Ruby, pointing at this config:

```
dkg code-ingest /path/to/your/repo --languages examples/custom-language/dkg.languages.json
```

The Ruby `Greeter` and `LoudGreeter` classes, their methods, the
`greet -> format_message` call edge, and the `LoudGreeter -> Greeter` inheritance
edge all land in the shared code graph, so blast-radius, execution-flow, and
code search work over Ruby exactly as they do for the built-in languages.
