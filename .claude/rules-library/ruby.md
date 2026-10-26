---
paths:
  - "**/*.rb"
  - "**/Gemfile"
  - "**/*.rake"
---
<!-- Adapted from ECC (rules/ruby), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# Ruby

Extends the [common rules](common.md).

## Style

- The repo's RuboCop configuration; no inline disabling of rules except as a justified exception.
- `# frozen_string_literal: true` if the project follows that convention.
- Clear Ruby over clever metaprogramming; DSLs are isolated and tested.
- In Rails: the framework's conventions first. Controllers only handle transport (authentication, parameters, response). Service or query objects when the complexity calls for them, not by default.
- `bin/rails` and the repo's binstubs, not globally installed commands.

## Errors

- `rescue` specific exceptions; no `rescue StandardError` that does not re-raise or keep the context.
- No `puts`, `pp` or `debugger` in delivered code.

## Tests

- The framework the project already uses (Minitest or RSpec), without mixing them in the same area.
- Domain logic in model or service tests; HTTP contracts in request tests. System tests only for critical flows.
- Fixtures or `factory_bot`, whichever the repo already uses.

## Security

- Strong parameters before any mass assignment. CSRF protection, enabled.
- Active Record queries or parameterised SQL; never interpolate parameters into SQL.
- `html_safe` and `raw` are treated as sensitive code.
- `bundle audit` or `bundler-audit` when the `Gemfile.lock` changes.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `bundle exec rails zeitwerk:check` (Rails) |
| types | (not applicable, or `bundle exec srb tc` with Sorbet) |
| lint | `bundle exec rubocop` |
| tests | `bin/rails test` or `bundle exec rspec` |
