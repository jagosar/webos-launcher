---
paths:
  - "**/*.java"
  - "**/pom.xml"
  - "**/build.gradle"
---
<!-- Adapted from ECC (rules/java), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# Java

Extends the [common rules](common.md).

## Style

- The project's formatter (google-java-format or Checkstyle). One public type per file.
- `record` for values and DTOs; `final` fields by default; defensive copies in the API (`List.copyOf`).
- Sealed classes and pattern-matching `switch` for closed hierarchies, if the Java version allows.
- Constructor injection, never field injection.
- Short, side-effect-free streams; if the chain gets complicated, a loop reads better.

## Errors

- Domain exceptions that extend `RuntimeException`, with context in the message.
- `Optional<T>` as the return of lookups; never as a field or a parameter, and never `get()` without checking.
- No `catch (Exception e)` except in the top-level handler.

## Tests

- JUnit 5 (`@Test`, `@ParameterizedTest`, `@Nested`), AssertJ for assertions and Mockito for dependencies, if the project uses them.
- Testcontainers when an integration test needs a real database.
- Names like `method_scenario_expectedResult` or a readable `@DisplayName`.

## Security

- `PreparedStatement` or the framework's parameterised API; never concatenate SQL.
- Passwords with bcrypt or Argon2; never MD5 or SHA-1.
- Bean Validation (`@NotNull`, `@Size`) on input DTOs, if the project uses it.
- OWASP Dependency-Check or the equivalent when the dependencies change.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `./mvnw -q -DskipTests package` or `./gradlew assemble` |
| types | (the compiler covers it in the build) |
| lint | `./mvnw -q checkstyle:check` or `./gradlew check -x test` |
| tests | `./mvnw -q test` or `./gradlew test` |
