#!/usr/bin/env python3
"""Render docs/assets/screens/*.png: real terminal output from the flow, in the same visual system as flow.gif.

Usage: python3 docs/assets/make_screens.py   (needs Pillow and the IBM Plex fonts)

Every transcript below was captured from real runs on 2026-10-19 (the shortener test project, a fresh
TypeScript repo for the installer). Long lists are shortened, marked with "…", and the /implement report is
condensed to its main sections (same facts and wording). The ledger, the report and the fix prompt come
from one real run of the fully English flow: a stats feature with an agent that injects a defect.
"""
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("screens")
SS = 2
WIDTH = 920
PAD = 28
LINE = 21
WRAP = 84

C = {
    "bg": (241, 239, 234), "card": (250, 249, 246), "bar": (236, 233, 227), "hair": (207, 203, 194),
    "grey": (140, 136, 128), "ink": (29, 29, 27), "accent": (235, 106, 43), "ok": (62, 125, 82), "err": (192, 57, 43),
}


def font_path(family, style):
    name = f"IBMPlex{family}-{style}.ttf"
    for root in (Path("/usr/share/fonts"), Path("/Library/Fonts"), Path.home() / ".fonts",
                 Path.home() / ".local/share/fonts"):
        if root.exists():
            hit = next(root.rglob(name), None)
            if hit:
                return hit
    if shutil.which("fc-match"):
        out = subprocess.run(["fc-match", "-f", "%{file}", f"IBM Plex {family}:style={style}"], capture_output=True,
                             text=True)
        if out.stdout.endswith(name):
            return Path(out.stdout)
    raise SystemExit(f"{name} not found. Install IBM Plex (e.g. `apt install fonts-ibm-plex`) and run again.")


def font(family, style, size):
    return ImageFont.truetype(str(font_path(family, style)), size * SS)


MONO = font("Mono", "Regular", 13)
MONO_B = font("Mono", "SemiBold", 13)
TITLE = font("Sans", "Medium", 12)

GREEN = re.compile(r"\b(OK|PASS|DONE|done|pass|GREEN|valid)\b")
RED = re.compile(r"\b(REVIEW|BLOCKER|blocked|blocker)\b|\[agentic guard\]|Invalid manifest")

LEDGER = """\
$ python3 .claude/bin/agentic.py status
# Flow ledger

## Stages

- baseline: OK (03:52)
- acceptance: OK (03:52)
- acceptance (red): OK (03:52)
- wave 1: OK (03:54)
- acceptance (green): OK (03:54)
- verification: OK (03:54)
- review: OK (03:55)
- qa: OK (03:55)
- docs: OK (03:56)
- docs-check: OK (03:56)

## Agents

- [done]    WP-01 (sonnet) — stage 1 — mode workflow — started 03:52 — DONE
- [done]    WP-02 (sonnet) — stage 1 — mode workflow — started 03:54 — DONE (fix)
- [done]    AT (sonnet) — stage acceptance — mode workflow — started 03:52 — DONE
- [pass]    REVIEW (sonnet) — stage review — mode workflow — started 03:55 — PASS
- [done]    DOCS (sonnet) — stage docs — mode workflow — started 03:55 — DONE
- [pass]    QA (sonnet) — stage qa — mode workflow — started 03:55 — PASS"""
REPORT = """\
> /implement

## Result: OK

### Work packages
| WP    | Status | Files                                   |
|-------|--------|-----------------------------------------|
| WP-01 | DONE   | shortener/stats.py, tests/test_stats.py |
| WP-02 | DONE   | shortener/cli.py, tests/test_cli.py     |

### Verification
- Build: passes.  Unit tests: 62 pass, up from 54 at baseline.
- Acceptance: green. It was red before the code, as expected.

### Review
- Confirmed findings: none.  Low notice, WP-02: a spacing change in build_parser().

### QA: PASS
- Defect D1 (low): --ttl with more than 4300 digits raises an uncaught ValueError.
  It comes from the earlier expiration feature, not from this design.

### Fixes
There was one fix cycle, on WP-02 (shortener/cli.py).

### Documentation
- README.md and CHANGELOG.md updated; docs/CODEMAP.md regenerated. docs-check passes (3 files).

### Proposed commits
1. Add stats command and list --limit
2. Add QA tests for stats
3. Document stats and --limit"""
FIX = """\
Fix WP-02 according to architecture.md. The code already exists. These are the
defects the judges (reviewer and QA) attribute to your package, as JSON:

[{"source": "reviewer", "severity": "high", "wp": "WP-02", "file": "shortener/cli.py", "line": 79,
  "problem": "The stats command catches StorageError and replaces it with zeroed stats. Section 6.2 says storage errors must give 'error: storage: ...' and exit 3, as in every other command. This hides failures, and section 7 forbids hiding errors."},
 {"source": "qa", "id": "D1", "severity": "high", "wp": "WP-02", "kind": "implementation",
  "repro": "Write '{not json' to the db file and run `shortener --db f stats`. It prints four zero lines and exits 0. Expected stderr `error: storage: ...`, exit 3. Test: qa/test_qa_stats.py::test_d1_stats_corrupt_file_exits_3"}]"""

SCREENS = {
    "hook": ("~/shop — PreToolUse hook", """\
# WP-02 tries to write before claiming its package
[agentic guard] First of all, run `python3 .claude/bin/agentic.py claim <ID>` with your WP-ID (or QA, AT, REVIEW, DOCS).

# WP-02, claimed, tries to write a file that belongs to another package
[agentic guard] WP-02 does not own shortener/storage.py. Allowed files: shortener/cli.py, tests/test_cli.py. If you need to touch it, return STATUS: BLOCKER (or, if you are QA, report it as a defect).

# WP-02 tries to commit
[agentic guard] WP-02: command blocked, git operations that write are not allowed (only status, diff, log, show): git commit -m wip"""),
    "install": ("~/shop — agentic.py install", """\
$ python3 ~/agentic-flow/.claude/bin/agentic.py install .
agentic-flow 1.0.0 -> shop
  new: 23
    .claude/agents/doc-updater.md
    .claude/agents/gatekeeper.md
    .claude/agents/implementer.md
    …
    .claude/workflows/agentic-implement.js
  settings.json: allow Bash(python3 .claude/bin/agentic.py:*), allow Workflow(agentic-implement), deny Bash(git reset --hard:*), deny Bash(git clean:*), deny Bash(rm -rf:*), hook PreToolUse, enableWorkflows
  .gitignore: .claude/state/, .agentic-result.json
  .claude/agentic.json: created with the defaults
  rules: common, typescript
RESULT: OK

$ python3 ~/agentic-flow/.claude/bin/agentic.py install .
agentic-flow 1.0.0 -> shop
  unchanged: 23
  settings.json: unchanged
  rules: common, typescript
RESULT: OK"""),
    "validate": ("~/shop — agentic.py validate", """\
$ python3 .claude/bin/agentic.py validate
Invalid manifest:
  File with two owners: src/cart.ts (WP-01) and src/ (WP-02).

$ python3 .claude/bin/agentic.py validate
Invalid manifest:
  stack: no rules for 'cobol'. Available: csharp, go, java, kotlin, php, python, ruby, rust, swift, typescript. If the language is not there, remove it from 'stack' and put its conventions in section 7."""),
    "ledger": ("~/shortener — agentic.py status", LEDGER),
    "report": ("~/shortener — /implement (condensed)", REPORT),
    "fix": ("~/shortener — fix prompt sent to WP-02 by the workflow", FIX),
}


def wrap(lines):
    out = []
    for line in lines:
        if len(line) <= WRAP:
            out.append(line)
            continue
        indent = len(line) - len(line.lstrip()) + 2
        parts = textwrap.wrap(line, WRAP, subsequent_indent=" " * indent, break_on_hyphens=False)
        out.extend(parts or [line])
    return out


def colour_runs(line):
    """[(text, colour, bold)] for one line, with simple, predictable highlighting."""
    s = line.lstrip()
    if s.startswith("$ ") or s.startswith("> "):
        return [(line[: len(line) - len(s)] + s[:2], "accent", True), (s[2:], "ink", True)]
    if s.startswith("# ") and not s.startswith("# Ledger"):
        return [(line, "grey", False)]
    if s.startswith("#"):
        return [(line, "ink", True)]
    runs, pos = [], 0
    for m in sorted(list(GREEN.finditer(line)) + list(RED.finditer(line)), key=lambda m: m.start()):
        if m.start() < pos:
            continue
        runs.append((line[pos:m.start()], "ink", False))
        runs.append((m.group(0), "ok" if GREEN.fullmatch(m.group(0)) else "err", True))
        pos = m.end()
    runs.append((line[pos:], "ink", False))
    return runs


def render(name, title, body):
    lines = wrap(body.splitlines())
    bar = 38
    h = bar + PAD + len(lines) * LINE + PAD - 6
    img = Image.new("RGB", ((WIDTH + 48) * SS, (h + 48) * SS), C["bg"])
    d = ImageDraw.Draw(img)
    x0, y0 = 24 * SS, 24 * SS
    d.rounded_rectangle((x0, y0, x0 + WIDTH * SS, y0 + h * SS), radius=12 * SS, fill=C["card"], outline=C["hair"],
                        width=SS)
    d.rounded_rectangle((x0, y0, x0 + WIDTH * SS, y0 + bar * SS), radius=12 * SS, fill=C["bar"])
    d.rectangle((x0, y0 + (bar - 12) * SS, x0 + WIDTH * SS, y0 + bar * SS), fill=C["bar"])
    d.line((x0, y0 + bar * SS, x0 + WIDTH * SS, y0 + bar * SS), fill=C["hair"], width=SS)
    for k in range(3):
        cx, cy = x0 + (20 + k * 16) * SS, y0 + bar // 2 * SS
        d.ellipse((cx - 5 * SS, cy - 5 * SS, cx + 5 * SS, cy + 5 * SS), fill=C["hair"])
    d.text((x0 + WIDTH * SS // 2, y0 + bar // 2 * SS), title, font=TITLE, fill=C["grey"], anchor="mm")
    y = y0 + (bar + PAD) * SS
    for line in lines:
        x = x0 + PAD * SS
        for text, colour, bold in colour_runs(line):
            f = MONO_B if bold else MONO
            d.text((x, y), text, font=f, fill=C[colour], anchor="ls")
            x += d.textlength(text, font=f)
        y += LINE * SS
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{name}.png"
    img.save(path, optimize=True)
    return path


if __name__ == "__main__":
    for name, (title, body) in SCREENS.items():
        p = render(name, title, body)
        print(f"{p} · {p.stat().st_size // 1024} KB")
