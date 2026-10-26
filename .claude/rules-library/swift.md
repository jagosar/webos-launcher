---
paths:
  - "**/*.swift"
  - "**/Package.swift"
---
<!-- Adapted from ECC (rules/swift), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# Swift

Extends the [common rules](common.md).

## Style

- SwiftFormat and SwiftLint, or `swift-format`, depending on what the project uses.
- `let` by default; `struct` with value semantics unless identity is needed.
- Clarity at the point of use: methods are named for their role, with no needless words.
- Enums with associated values to model distinct states; protocols for dependencies.
- Structured concurrency (`async let`, `TaskGroup`) rather than loose `Task {}`; actors for shared mutable state and `Sendable` types across isolation domains.

## Errors

- `throws` with your own error types (an `enum` that conforms to `Error`).
- No `try!` and no force-unwrapping (`!`) with external data: use `guard let` or `if let`.

## Tests

- Swift Testing (`@Test`, `#expect`) or XCTest, whichever the project uses.
- Parameterised tests for tables of cases; dependencies injected through protocols to isolate them.

## Security

- Secrets in the Keychain, never in `UserDefaults` or in the code.
- App Transport Security enabled; HTTPS only.
- Validate everything that comes from outside (deep links, the pasteboard, an API) before using it.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `swift build` |
| types | (the compiler covers it in the build) |
| lint | `swiftlint --strict` |
| tests | `swift test` |
