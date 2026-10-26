---
paths:
  - "**/*.go"
  - "**/go.mod"
  - "**/go.sum"
---
<!-- Adapted from ECC (rules/golang), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# Go

Extends the [common rules](common.md).

## Style

- `gofmt` and `goimports`, no debate.
- Accept interfaces, return structs. Small interfaces (1 to 3 methods), defined where they are consumed.
- Dependency injection through constructors (`NewService(repo Repo)`); functional options when there are many optional parameters.
- `context.Context` as the first parameter of any function that does I/O or may take long.
- Short names in short scopes; export only what is part of the contract.

## Errors

- Always check the error. Wrap it with context: `fmt.Errorf("save link %s: %w", id, err)`.
- Sentinel errors (`var ErrNotFound = errors.New(...)`) or error types, compared with `errors.Is` and `errors.As`.
- `panic` only for impossible states, never for expected errors.

## Tests

- Table tests with `t.Run` per case. `t.Helper()` in helpers and `t.TempDir()` for files.
- `go test -race` on the suite: race conditions are defects.
- Hand-written test doubles against the small interface, rather than mock libraries.

## Security

- Secrets with `os.Getenv`, checked at startup.
- `database/sql` with parameters (`?`, `$1`); never `fmt.Sprintf` to build SQL.
- Timeouts on every HTTP client and server: the defaults have no limit.
- `gosec ./...` and `govulncheck ./...` if the project uses them.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `go build ./...` |
| types | `go vet ./...` |
| lint | `golangci-lint run` |
| tests | `go test -race ./...` |
