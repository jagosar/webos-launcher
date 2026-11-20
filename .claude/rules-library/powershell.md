---
paths:
  - "**/*.ps1"
  - "**/*.psm1"
  - "**/*.psd1"
---
<!-- Written for agentic-flow. -->
# PowerShell

Extends the [common rules](common.md). Written for Windows PowerShell 5.1 and Pester 3.4, the versions whose traps it records; newer versions keep most of them.

## Style

- Approved verbs in `Verb-Noun` function names (`Get-Verb` lists them), `[CmdletBinding()]` and a `param()` block with types on every public function.
- `Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'` at the top of a script, so a typo or a failed call stops it.
- Full cmdlet names and named parameters in scripts and modules; no aliases (`%`, `?`, `ls`).
- A module exports only what it means to: `Export-ModuleMember` or the manifest's `FunctionsToExport`.

## Errors

- `throw` or `Write-Error -ErrorAction Stop` for a failure the caller must handle; `try`/`catch` around the specific call, catching a typed exception where there is one.
- Never an empty `catch {}`, and never `-ErrorAction SilentlyContinue` to hide a failure the code should handle.
- `$LASTEXITCODE` after every native command: a non-zero exit does not throw.

## Tests

- Pester. One behaviour per `It`, with the criterion's ID in the `Describe` or the `It` name.
- **Mocks keep their history inside a `Describe`.** `Mock -ModuleName X` and `Assert-MockCalled` count every call made in that `Describe`, across its `It`s. A second `It` sees the first one's calls. Give each case that asserts call counts its own `Describe` (or `Context`), or assert with `-Scope It`.
- **No `Import-Module -Force` inside an `It`.** It reloads the module and leaves the mocks made earlier with `-ModuleName` pointing at the old one, so later assertions see stale mocks. Import once, in the `Describe`'s setup.
- **Arrays unroll.** A function that returns `@(...)` with one element hands the caller that element, not an array. A hashtable then answers `.Count` with its number of keys. Wrap the call, `@(Get-Thing)`, before counting. On 5.1.26100, `@($list)` on a `List[object]` throws "argument types do not match": use `$list.ToArray()` or `[object[]]$list`.
- **`$script:` scope belongs to the script that defines it.** A global test double that reads `$script:Name` sees its own empty variable, not the value of the script under test. Pass the value in, or mock inside the module with `-ModuleName`.
- Exercise every helper and test double once on a trivial input in its own `It`, before a criterion relies on it.
- Write a report the flow can read: `-OutputFile test-results.xml -OutputFormat NUnitXml`, declared in the manifest's `reports` field (for example `"reports": {"tests": "test-results.xml"}`). Without it, attribution falls back to the console, where every `Describing` heading is printed.

## Security

- Secrets from the environment or `SecretManagement`; never in a script, a module manifest or a test.
- No `Invoke-Expression` on input. Build a command as a call with named parameters, and pass paths through `-LiteralPath`.
- Check a path that comes from outside with `Resolve-Path` and compare it with the allowed root before using it.
- `-ExecutionPolicy Bypass` only in the CI command line, never set for the machine by a script.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | (not applicable) |
| types | (not applicable) |
| lint | `powershell -NoProfile -NonInteractive -Command "Invoke-ScriptAnalyzer -Path . -Recurse -EnableExit"` |
| tests | `powershell -NoProfile -NonInteractive -Command "Invoke-Pester -EnableExit -OutputFile test-results.xml -OutputFormat NUnitXml"` |
