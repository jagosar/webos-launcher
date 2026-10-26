<!-- Adapted from ECC (rules/common), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# Common rules

These rules apply to any language. The language-specific ones (`.claude/rules/agentic/<language>.md`) extend them.

## Precedence

When two sources contradict each other, the first on this list wins:
1. `architecture.md`: contracts (section 6), conventions (section 7) and the WP's patterns to mirror.
2. The style of the code that already exists in the repo (section 4b).
3. The project's own rules in `.claude/rules/`, outside `agentic/`.
4. These generic rules.

In a repo with code, imitating what exists is worth more than applying a preference from here. If a rule here contradicts the plan, follow the plan and note the contradiction in the report.

## Design

- **The simplest thing that works.** No speculative abstractions (YAGNI). Extract a function or a type when the repetition is real, not just in case.
- **Files with one purpose.** High cohesion, low coupling. Organise by feature or domain, not by file type.
- **Short, flat functions.** Early returns instead of nested conditionals; more than 4 levels of nesting is a sign to split.
- **No magic numbers.** Thresholds, times and limits go in named constants or configuration.
- **Immutable data by default** when the language makes it easy: return a copy instead of mutating what comes in, unless the contract says otherwise.

## Errors and inputs

- Validate at the system's boundaries (user input, files, network, environment variables) and fail early, with a clear message.
- Never swallow an error silently. Catch the specific exception, not the generic one, except at the entry point.
- User-facing messages do not leak internal paths, traces, SQL or secrets. The detail goes to the log.
- The errors that cross a contract are those of section 6, with their exact name.

## Naming

- The name says what it holds or does, without needing a comment.
- Booleans read like a claim (`is_expired`, `hasAccess`).
- Capitalisation and prefixes follow the language's convention (see its rules file).

## Tests

- Arrange, act, assert. One behaviour per test, with a name that describes it (`returns_404_if_the_link_does_not_exist`).
- In a fix, the test that reproduces the bug comes before the correction and fails without it.
- Test edge cases and error cases, not just the happy path: empty, null, maximum, duplicate, no permissions.
- Tests are deterministic: no real network, no dependence on the clock or on execution order. Test doubles are defined in the WP's own files.
- A failing test is fixed or reported. It is never disabled to turn it green (the hook blocks that).

## Security

- No secrets in code, tests or reports: environment variables or a secrets manager.
- Always parameterised queries; never concatenate user input into SQL, shell commands or paths.
- Paths that come from outside are normalised and checked not to leave the allowed directory.
- Escape output in HTML templates and treat as suspicious everything that comes from an external API, a webhook or an uploaded file.
- Authentication and cryptography with established libraries; never your own implementations.

## Before saying DONE

- The WP's validation commands and the acceptance tests of its ACs pass.
- No debugging leftovers (`print`, `console.log`, `dbg!`, commented-out code).
- The project's formatter and linter give no new warnings in your files.
