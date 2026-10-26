---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
<!-- Adapted from ECC (rules/python), https://github.com/affaan-m/ECC — MIT License, Copyright (c) 2026 Affaan Mustafa. -->
# Python

Extends the [common rules](common.md).

## Style

- PEP 8. Type annotations on every public signature; `X | None` instead of `Optional[X]` from Python 3.10.
- `dataclass(frozen=True)` or `NamedTuple` for values; `Protocol` to depend on an interface without inheritance.
- `pathlib.Path` for paths, `with` for every resource you open, generators to walk large data.
- No mutable default arguments (`def f(x=[])`). No `import *`.
- Grouped imports: standard library, third party and local.

## Errors

- Domain exceptions that inherit from a module base (`class StorageError(Exception)`).
- `raise NewError(...) from e` when translating an exception, so the cause is not lost.
- Never `except:` or `except Exception: pass`. Catch the specific one.

## Tests

- `pytest` if the project uses it; otherwise `unittest`, which adds no dependency.
- Fixtures to prepare state, `tmp_path` for files, `monkeypatch` for the environment. No writing outside the temporary directory.
- `pytest.mark.parametrize` for tables of cases.

## Security

- Secrets with `os.environ["NAME"]`, which fails if missing.
- Never `eval`, `exec`, `pickle` of external data or `yaml.load` without `SafeLoader`.
- `subprocess.run([...])` with an argument list, without `shell=True` when there is external input.
- If the project uses it, `bandit -r src/` as static analysis.

## Common commands

| `verify` phase | Typical command |
|---|---|
| build | `python3 -m compileall -q src` |
| types | `mypy src` or `pyright` |
| lint | `ruff check .` (and `ruff format --check .`) |
| tests | `python3 -m pytest -q` or `python3 -m unittest discover -s tests` |
