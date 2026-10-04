# Vendored Ruff crates

These crates are **vendored** from [astral-sh/ruff](https://github.com/astral-sh/ruff)
(MIT license) — see each crate's `[package.metadata.forger]` section in its
`Cargo.toml` for the exact source commit.

| Crate | Purpose |
|---|---|
| `ruff_python_ast` | Python AST node definitions |
| `ruff_python_parser` | Fast, error-tolerant Python parser + lexer |
| `ruff_python_trivia` | Comment/whitespace trivia handling |
| `ruff_source_file` | Source file / line index utilities |
| `ruff_text_size` | Text ranges over source |

## Local modifications

- `Cargo.toml` files de-workspaced (concrete values instead of `{ workspace = true }`)
  with `[package.metadata.forger]` provenance metadata added.
- Test fixtures, snapshot directories, and fixture-driven test harnesses removed
  (`ruff_python_parser/src/parser/tests.rs`, `ruff_python_parser/resources/`,
  `ruff_python_parser/src/**/snapshots/`, `ruff_text_size/tests/`).
- Everything else is byte-identical to upstream.

## Updating

1. Pick the new upstream tag; note its commit SHA.
2. Re-copy the five crates from the monorepo.
3. Re-apply the manifest rewrites above.
4. Update `vendored-ref` in every `Cargo.toml`.
5. Run `cargo check --all-targets --manifest-path cargo/Cargo.toml`.
