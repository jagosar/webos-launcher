"""What the flow reads from the repository without a model: the stack and its rules, the code map, the callers of the
symbols a change touched (for the reviewer) and the links and paths the documentation cites.
"""
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file
from core import (  # noqa: E402
    ARCHIVE_DIR, DEFAULT_CODEMAP, ManifestError, ROOT, RULES_DIR, RULES_LIBRARY, SKIP_DIRS, codemap_path, die,
    docs_entity, ledger_stage, load_manifest, owns, source_files, symbol_re
)

# Signals for each rules pack: project files (in any folder) and code extensions.
STACK_SIGNALS = {
    "python": (("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt"), (".py", ".pyi")),
    "typescript": (("package.json", "tsconfig.json", "deno.json"), (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")),
    "go": (("go.mod",), (".go",)),
    "rust": (("Cargo.toml",), (".rs",)),
    "java": (("pom.xml", "build.gradle"), (".java",)),
    "kotlin": ((), (".kt",)),
    "csharp": ((), (".cs", ".csproj", ".sln")),
    "ruby": (("Gemfile",), (".rb", ".rake")),
    "php": (("composer.json",), (".php",)),
    "swift": (("Package.swift",), (".swift",)),
}


# ---------- language rules ----------

def detect_stack():
    """Languages with signals in the repo: {language: {"markers": [...], "files": N}}."""
    found = {}
    for rel in source_files():
        name = rel.rsplit("/", 1)[-1]
        for pack, (markers, exts) in STACK_SIGNALS.items():
            is_marker = name in markers or (pack == "python" and name.startswith("requirements")
                                            and name.endswith(".txt"))
            if is_marker or name.endswith(exts):
                entry = found.setdefault(pack, {"markers": [], "files": 0})
                if is_marker:
                    entry["markers"].append(rel)
                else:
                    entry["files"] += 1
    return found

def declared_stack():
    try:
        stack = load_manifest().get("stack", [])
    except ManifestError:
        return []
    return [s for s in stack if isinstance(s, str)] if isinstance(stack, list) else []

def rule_files(manifest):
    """Rules the agents must follow: the common ones and those of each language of the stack (or, if none was
    declared, the detected ones), as long as they are installed."""
    packs = manifest.get("stack") or sorted(detect_stack())
    names = ["common"] + [p for p in packs if isinstance(p, str)]
    return [f".claude/rules/agentic/{n}.md" for n in names if (RULES_DIR / f"{n}.md").exists()]

def rules_hint(manifest):
    files = rule_files(manifest)
    return f" Rules you must follow (language and common): {', '.join(files)}." if files else ""

def wanted_rules():
    """The rule names rules_sync installs: "common" plus each declared or detected language with a file in the
    library, sorted. It never exits: the missing-library check stays in rules_sync."""
    # Stack entries are names, never paths: "../../x" must not copy a file out of the library or write outside it.
    names = set(declared_stack()) | set(detect_stack())
    return sorted({"common"} | {p for p in names
                                if re.fullmatch(r"[a-z][a-z0-9+-]*", str(p)) and (RULES_LIBRARY / f"{p}.md").exists()})

def rules_sync():
    """Copies the library to the directory Claude Code loads: common + declared or detected languages."""
    if not (RULES_LIBRARY / "common.md").exists():
        die(f"{RULES_LIBRARY.relative_to(ROOT)}/common.md does not exist: install the full flow (plugin or template).")
    declared, detected = declared_stack(), sorted(detect_stack())
    wanted = set(wanted_rules())
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    installed, removed = [], []
    for name in sorted(wanted):
        src = (RULES_LIBRARY / f"{name}.md").read_text(encoding="utf-8")
        dst = RULES_DIR / f"{name}.md"
        if not dst.exists() or dst.read_text(encoding="utf-8") != src:
            dst.write_text(src, encoding="utf-8")
            installed.append(name)
    for old in sorted(RULES_DIR.glob("*.md")):
        if old.stem not in wanted:
            old.unlink()
            removed.append(old.stem)
    print(json.dumps({"rules": sorted(wanted), "updated": installed, "removed": removed, "declared": declared,
                      "detected": detected, "missing": sorted(set(declared) - wanted)}, ensure_ascii=False))


# ---------- living documentation ----------

def codemap(manifest, target=None):
    """Deterministic code map: code files by folder, with lines and public symbols. No date, so it only changes
    when the code does."""
    rel_out = target or codemap_path(manifest) or DEFAULT_CODEMAP
    out = ROOT / rel_out
    rows, langs = {}, set()
    for rel in source_files():
        rx = symbol_re(rel)
        if rx is None or rel == rel_out:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        symbols = [m.group(1) for line in text.splitlines() if (m := rx.match(line))]
        langs |= {p for p, (_, exts) in STACK_SIGNALS.items() if rel.endswith(exts)}
        folder = rel.rsplit("/", 1)[0] if "/" in rel else "."
        rows.setdefault(folder, []).append((rel, len(text.splitlines()), symbols))
    total = sum(len(v) for v in rows.values())
    lines = ["# Code map", "",
             "> Generated by `python3 .claude/bin/agentic.py codemap`. Do not edit it by hand: the flow regenerates it "
             "when "
             "a cycle closes.", "",
             f"{total} code files in {len(rows)} folders. Languages: {', '.join(sorted(langs)) or 'none'}.", ""]
    for folder in sorted(rows):
        lines += [f"## `{folder}/`" if folder != "." else "## Root", "", "| File | Lines | Public symbols |",
                  "|---|---|---|"]
        for rel, n, symbols in rows[folder]:
            shown = ", ".join(f"`{s}`" for s in symbols[:12])
            shown += f" and {len(symbols) - 12} more" if len(symbols) > 12 else ""
            lines.append(f"| `{rel.rsplit('/', 1)[-1]}` | {n} | {shown or '—'} |")
        lines.append("")
    content = "\n".join(lines)
    changed = not out.exists() or out.read_text(encoding="utf-8") != content
    if changed:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
    print(json.dumps({"path": rel_out, "files": total, "folders": len(rows), "changed": changed}, ensure_ascii=False))


# ---------- callers: what the reviewer must read beyond the diff ----------

CALLERS_PER_SYMBOL = 10

CALLERS_MAX = 80

CALLER_TEXT_MAX = 160

CALLER_RE_TEMPLATE = r"(?<![\w$])%s(?![\w$])"  # the symbol as a whole word; $ for JS identifiers

EMPTY_CALLERS = {"changed": [], "symbols": {}, "callers": [], "truncated": False}

def _git_paths(args):
    """The NUL-separated paths a git command prints, or None when git or the revision is not there."""
    try:
        out = subprocess.run(["git", *args, "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return [p for p in out.decode("utf-8", errors="replace").split("\0") if p]

def changed_code_files():
    """Code files modified since HEAD or untracked, filtered like source_files(): sorted, relative, existing."""
    modified = _git_paths(["diff", "--name-only", "--relative", "HEAD"]) or []
    untracked = _git_paths(["ls-files", "--others", "--exclude-standard"]) or []
    found = set()
    for rel in modified + untracked:
        rel = rel.replace("\\", "/")
        if set(rel.split("/")[:-1]) & SKIP_DIRS or rel.startswith(".claude/state/") or symbol_re(rel) is None:
            continue
        if (ROOT / rel).is_file():
            found.add(rel)
    return sorted(found)

def file_symbols(rel):
    """The public symbols of one file, as the code map sees them, in order and without repeats."""
    rx = symbol_re(rel)
    if rx is None:
        return []
    try:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen = []
    for line in text.split("\n"):
        m = rx.match(line.rstrip("\r"))
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return seen

def find_callers(changed, symbols):
    """The lines of tracked code files outside `changed` that use a symbol as a whole word. symbols: {file: [name]}.
    Symbol-major (symbols in file order), then file order, then line order; capped per symbol and in total.
    Returns (callers, truncated)."""
    names = []
    defined_in = {}
    for rel in sorted(symbols):
        for name in symbols[rel]:
            if name not in defined_in:
                defined_in[name] = rel
                names.append(name)
    if not names:
        return [], False
    changed_set = set(changed)
    whole = re.compile(CALLER_RE_TEMPLATE % ("(?:" + "|".join(re.escape(n) for n in names) + ")"))
    per_name = {n: re.compile(CALLER_RE_TEMPLATE % re.escape(n)) for n in names}
    hits = {n: [] for n in names}
    for rel in source_files():
        if rel in changed_set or symbol_re(rel) is None or rel.startswith(".claude/state/"):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(n in text for n in names):
            continue
        for number, line in enumerate(text.split("\n"), 1):
            line = line.rstrip("\r")
            if not whole.search(line):
                continue
            for n in names:
                if per_name[n].search(line):
                    hits[n].append({"symbol": n, "defined_in": defined_in[n], "file": rel, "line": number,
                                    "text": line.strip()[:CALLER_TEXT_MAX]})
    truncated = False
    out = []
    for n in names:
        if len(hits[n]) > CALLERS_PER_SYMBOL:
            truncated = True
        out.extend(hits[n][:CALLERS_PER_SYMBOL])
    if len(out) > CALLERS_MAX:
        truncated = True
        out = out[:CALLERS_MAX]
    return out, truncated

def callers():
    """One JSON line for the engines: the changed code files, their public symbols and the callers outside the diff.
    Exits 0 whatever happens: an instrument never stops the review."""
    try:
        changed = changed_code_files()
        symbols = {rel: syms for rel in changed for syms in [file_symbols(rel)] if syms}
        found, truncated = find_callers(changed, symbols)
        print(json.dumps({"changed": changed, "symbols": symbols, "callers": found, "truncated": truncated},
                         ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 - the step must not stop the review
        print(f"[agentic] callers skipped: {type(e).__name__}: {e}", file=sys.stderr)
        print(json.dumps(dict(EMPTY_CALLERS, error=f"{type(e).__name__}: {e}"), ensure_ascii=False))
    sys.exit(0)


# ---------- documentation checks ----------

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

PATH_RE = re.compile(r"`([\w.-]+/[\w./-]*)`")

def heading_anchors(text):
    """The anchors GitHub gives the headings: a repeated heading gets -1, -2..."""
    anchors, seen = set(), {}
    for h in re.findall(r"^#+ (.+)$", text, re.M):
        slug = re.sub(r"[^\w\s-]", "", h.strip().lower(), flags=re.U).replace(" ", "-")
        anchors.add(f"{slug}-{seen[slug]}" if slug in seen else slug)
        seen[slug] = seen.get(slug, 0) + 1
    return anchors

def docs_files(manifest):
    docs = docs_entity(manifest) if manifest else None
    scope = docs["files"] if docs else ["README.md", "docs/"]
    return [rel for rel in source_files() if rel.endswith(".md") and any(owns(f, rel) for f in scope)
            and not rel.startswith(ARCHIVE_DIR)]

def docs_check(manifest):
    """Relative links, anchors and repo paths cited in backticks: everything must exist."""
    top_dirs = {p.name for p in ROOT.iterdir() if p.is_dir()}
    broken, files = [], docs_files(manifest)
    for rel in files:
        path = ROOT / rel
        fenced = False
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            for link in LINK_RE.findall(line):
                if link.startswith(("http://", "https://", "mailto:")):
                    continue
                target_rel, _, frag = link.partition("#")
                target_rel = urllib.parse.unquote(target_rel)
                # a link starting with / is relative to the repo root, as GitHub renders it
                target = (ROOT / target_rel.lstrip("/") if target_rel.startswith("/") else path.parent / target_rel) \
                    if target_rel else path
                if not target.exists():
                    broken.append({"file": rel, "line": n, "target": link, "kind": "link"})
                elif (frag and target.suffix == ".md"
                      and frag not in heading_anchors(target.read_text(encoding="utf-8"))):
                    broken.append({"file": rel, "line": n, "target": link, "kind": "anchor"})
            for cited in PATH_RE.findall(line):
                first = cited.split("/", 1)[0]
                if first in top_dirs and first not in SKIP_DIRS and not (ROOT / cited).exists():
                    broken.append({"file": rel, "line": n, "target": cited, "kind": "path"})
    ok = not broken
    ledger_stage("docs-check", "OK" if ok else f"REVIEW: {len(broken)} broken reference(s)")
    print(json.dumps({"ok": ok, "checked": len(files), "broken": broken}, ensure_ascii=False))
    sys.exit(0 if ok else 3)
