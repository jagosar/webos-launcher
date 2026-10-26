---
paths:
  - "**/*.rs"
  - "**/Cargo.toml"
---
<!-- Adapted from ECC (rules/rust), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# Rust

Extends the [common rules](common.md).

## Style

- `cargo fmt` and `cargo clippy -- -D warnings`.
- `let` by default; `let mut` only when mutation is needed.
- Borrow (`&T`, `&str`, `&[T]`) by default; take ownership only to store or consume. No `clone()` to quiet the borrow checker without understanding why.
- Private by default; `pub(crate)` to share inside the crate and `pub` only for the public API.
- Newtypes and enums so the type system rules out invalid states ("parse, don't validate").

## Errors

- `Result<T, E>` and `?`. Never `unwrap()` or `expect()` in production code, except in truly impossible states, with the reason in the message.
- Libraries: typed errors with `thiserror`. Applications: `anyhow` with `.context(...)`.

## Tests

- A `#[cfg(test)] mod tests` module for unit tests and `tests/` for integration.
- `#[tokio::test]` for async, if the project uses tokio; `rstest` and `proptest` if they are already dependencies.
- Names that describe behaviour: `rejects_order_without_stock`.

## Security

- Every `unsafe` block carries a `// SAFETY:` comment with the invariant that makes it correct. No `unsafe` for convenience.
- Queries with bound parameters (sqlx, diesel); never `format!` for SQL.
- `cargo audit` when `Cargo.lock` changes.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `cargo build --locked` |
| types | `cargo check --all-targets` |
| lint | `cargo clippy --all-targets -- -D warnings` |
| tests | `cargo test` |
