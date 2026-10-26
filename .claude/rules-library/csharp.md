---
paths:
  - "**/*.cs"
  - "**/*.csproj"
---
<!-- Adapted from ECC (rules/csharp), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# C#

Extends the [common rules](common.md).

## Style

- Current .NET conventions, with nullable reference types enabled (`<Nullable>enable</Nullable>`).
- `record` for values; `class` for entities with identity; `interface` at the boundaries between services.
- `init` setters and immutable collections for shared state. No `dynamic`.
- Explicit access modifiers; one main type per file.

## Errors

- `async`/`await` all the way; never `.Result` or `.Wait()`. `CancellationToken` in every public async API.
- Specific exceptions, with structured logging at the point where they are handled.

## Tests

- xUnit, with NSubstitute or Moq for dependencies, if the project uses them.
- `WebApplicationFactory<T>` to test an API over HTTP, without bypassing the middleware.
- The structure of `tests/` mirrors that of `src/`.

## Security

- Secrets in environment variables, or user-secrets locally; never in `appsettings.*.json`.
- Parameterised queries with EF Core, Dapper or ADO.NET.
- Authentication with the framework's handlers, without hand-parsing tokens. Tokens and passwords are not logged.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `dotnet build --nologo` |
| types | (the compiler covers it in the build) |
| lint | `dotnet format --verify-no-changes` |
| tests | `dotnet test --nologo` |
