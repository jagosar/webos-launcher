---
paths:
  - "**/*.kt"
  - "**/*.kts"
---
<!-- Adapted from ECC (rules/kotlin), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# Kotlin

Extends the [common rules](common.md).

## Style

- The official Kotlin style, with ktlint or Detekt if the project uses them.
- `val` by default; `data class` for values and `copy()` to change state.
- Immutable collections (`List`, `Map`) in the public API.
- `sealed` for closed states and results, with an exhaustive `when`.
- Extension functions in a file named after the receiver type, never on `Any`. At most two levels of nested scope functions (`let`, `apply`…).

## Errors

- Never `!!`: use `?.`, `?:`, `requireNotNull()` or `checkNotNull()`.
- `Result<T>` or your own sealed type for expected errors. Exceptions are not used for control flow.
- In coroutines, `CancellationException` is always rethrown.

## Tests

- `kotlin.test` or JUnit 5; `kotlinx-coroutines-test` (`runTest`) for coroutines and Turbine for `Flow`, if they are in the project.
- Hand-written fakes rather than mocks when the interface is small.

## Security

- Secrets outside the code: `local.properties` ignored by git locally and CI variables for the release.
- Parameterised queries in Room, SQLDelight or JDBC.
- Explicit timeouts on every HTTP client; HTTPS only.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `./gradlew assemble` |
| types | (the compiler covers it in the build) |
| lint | `./gradlew ktlintCheck` or `./gradlew detekt` |
| tests | `./gradlew test` |
