---
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.js"
  - "**/*.jsx"
  - "**/*.mjs"
  - "**/*.cjs"
---
<!-- Adapted from ECC (rules/typescript), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# TypeScript and JavaScript

Extends the [common rules](common.md).

## Style

- Explicit parameter and return types on everything exported; inference for obvious local variables.
- `interface` for extensible object shapes; `type` for unions, intersections and utility types.
- Unions of literals instead of `enum`, unless interoperability requires it.
- No `any`: `unknown` for what comes from outside, narrowed before use. Generics when the type depends on the caller.
- `const` by default; spread or `structuredClone` instead of mutating received objects.
- In `.js` files, JSDoc where it clarifies the types.

## Errors

- Custom error classes that extend `Error`, with `name` and `cause`.
- Every promise is awaited or returned: no floating promises. `try/catch` around the `await`, not an empty `.catch`.
- Validate untyped input at the boundary (JSON, a request body), with a schema (for example zod) if the project already uses one.

## Tests

- The runner the project already uses (`vitest`, `jest` or `node --test`).
- Component tests by visible behaviour (Testing Library), not by internal details.
- Playwright for critical end-to-end flows, if the project has it.
- No `console.log` in production code; use the project's logger.

## Security

- Secrets with `process.env`, checked at startup.
- Never `eval`, `new Function` or `innerHTML` with external data; in React, no `dangerouslySetInnerHTML` without sanitising.
- `child_process.execFile` or `spawn` with an argument list, not `exec` with concatenated text.
- `npm audit` (or the package manager's equivalent) when the lockfile changes.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `npm run build` |
| types | `npx tsc --noEmit` |
| lint | `npx eslint .` |
| tests | `npm test` or `npx vitest run` |
