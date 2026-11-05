"""The clean-code ratchet: size and complexity, measured with no model and compared with a committed baseline.

`measure` reads function length and nesting depth (Python through `ast`, JavaScript/TypeScript through a small
scanner), file size and long lines. `check` compares the numbers with the baseline: nothing may get worse, and a new
function or file must respect the limits. `tighten` lowers the baseline when the code improves; it never raises a value.
agentic.py runs it as the `quality` command and as a phase of `verify`.

The JavaScript scanner is a heuristic, not a parser. It blanks comments, strings and template literals first, so braces
inside them never count. Known limits: a regular expression literal containing a brace, braces in JSX text, and an
object type written inside a parameter list can shift its counts. A stable misreading compares like with like, so it
does not cause false failures.
"""
import ast
import fnmatch
import re
from pathlib import Path

DEFAULT_LIMITS = {"function_lines": 60, "test_function_lines": 100, "nesting": 4,
                  "file_lines": 800, "file_growth": 30, "line_chars": 120}
PY_EXTS = (".py", ".pyi")
JS_EXTS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
TEST_DIRS = {"tests", "test", "__tests__"}
TEST_NAME_RE = re.compile(r"^(test_.*\.py|.*_test\.py|.*\.(test|spec)\.[^.]+)$")
PY_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
PY_SCOPES = PY_FUNCS + (ast.Lambda, ast.ClassDef)
PY_CONTROL = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.AsyncFor, ast.AsyncWith) + tuple(
    getattr(ast, name) for name in ("Match", "TryStar") if hasattr(ast, name))
JS_NAME = r"[A-Za-z_$][\w$]*"
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "with", "function", "return", "else", "do", "try", "finally"}
JS_CONTROL_RE = re.compile(r"^\s*(?:else\b|if\b|for\b|while\b|switch\b|try\b|catch\b|finally\b|do\b)")
JS_CLASS_RE = re.compile(r"\bclass\s+(" + JS_NAME + r")")
JS_OBJECT_RE = re.compile(r"(?:const|let|var)\s+(" + JS_NAME + r")\s*=\s*$")
JS_VALUE = r"\s*(?:async\s+)?(?:function\b|(?:\([^()]*\)|" + JS_NAME + r")\s*=>\s*$)"  # a function or arrow
JS_FUNCTION_RES = [
    re.compile(r"\bfunction\s*\*?\s*(" + JS_NAME + r")\s*\("),
    re.compile(r"(?:^|[\s,;])(" + JS_NAME + r")\s*=" + JS_VALUE),
    re.compile(r"(?:^|[\s,{])(" + JS_NAME + r")\s*:" + JS_VALUE),
    re.compile(r"^\s*(?:(?:static|async|get|set|public|private|protected)\s+)*\*?(" + JS_NAME + r")\s*\([^()]*\)\s*$"),
]


def is_test_file(rel):
    parts = rel.replace("\\", "/").split("/")
    return bool(TEST_NAME_RE.match(parts[-1])) or bool(TEST_DIRS & set(parts[:-1]))


def excluded(rel, patterns):
    """A path the baseline's `exclude` leaves out: a glob (fnmatch) or a whole-segment path prefix."""
    for pattern in patterns or []:
        if any(c in pattern for c in "*?["):
            if fnmatch.fnmatch(rel, pattern):
                return True
        elif rel == pattern.rstrip("/") or rel.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def limits_of(baseline):
    return dict(DEFAULT_LIMITS, **((baseline or {}).get("limits") or {}))


# ---------- Python ----------

def _py_nesting(node, level):
    """The deepest control level under node (whose own level is `level`). An elif stays at its if's level; nested
    functions, lambdas and classes are measured on their own."""
    best = level
    for child in ast.iter_child_nodes(node):
        if isinstance(child, PY_SCOPES):
            continue
        # An elif and an `else:` holding a lone `if` are the same tree; only the elif starts in its if's column.
        is_elif = (isinstance(node, ast.If) and node.orelse == [child] and isinstance(child, ast.If)
                   and child.col_offset == node.col_offset)
        child_level = level + 1 if isinstance(child, PY_CONTROL) and not is_elif else level
        best = max(best, _py_nesting(child, child_level))
    return best


def _py_functions(tree):
    found, seen = {}, {}

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            elif isinstance(child, PY_FUNCS):
                name = prefix + child.name
                seen[name] = seen.get(name, 0) + 1
                key = name if seen[name] == 1 else f"{name} #{seen[name]}"
                found[key] = {"lines": child.end_lineno - child.lineno + 1, "nesting": _py_nesting(child, 0)}
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _python(text):
    try:
        return _py_functions(ast.parse(text))
    except (SyntaxError, ValueError):
        return {}


# ---------- JavaScript / TypeScript ----------

def _blank(text):
    """The text with comments, strings and template literals turned into spaces (newlines kept)."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text[i + 1:i + 2] in ("/", "*"):
            closer = "\n" if text[i + 1] == "/" else "*/"
            found = text.find(closer, i + 2)
            end = n if found < 0 else found + len(closer)  # unclosed: it runs to the end, never backwards
        elif c in "'\"`":
            end = i + 1
            while end < n and text[end] != c and (c == "`" or text[end] != "\n"):
                end += 2 if text[end] == "\\" else 1
            end = min(end + 1, n)
        else:
            i += 1
            continue
        for k in range(i, end):
            if out[k] != "\n":
                out[k] = " "
        i = end
    return "".join(out)


def _js_frame(head, frames):
    """What a `{` opens: (kind, name). Code without semicolons puts earlier statements in the head, so the last line
    of the head is read first, then the whole head (a signature split over several lines)."""
    lines = [row for row in head.splitlines() if row.strip()]
    for text in ([lines[-1]] if lines else []) + [head]:
        found = _js_classify(text)
        if found[0] != "other":
            return found
    return "other", None


def _js_classify(head):
    m = JS_CLASS_RE.search(head)
    if m:
        return "class", m.group(1)
    if JS_CONTROL_RE.match(head):
        return "control", None
    for rx in JS_FUNCTION_RES:
        m = rx.search(head)
        if m and m.group(1) not in JS_KEYWORDS:
            return "function", m.group(1)
    m = JS_OBJECT_RE.search(head)
    return ("object", m.group(1)) if m else ("other", None)


def _js_key(name, frames):
    holder = frames[-1] if frames else None
    if holder and holder["kind"] in ("class", "object") and holder["name"]:
        return holder["name"] + "." + name
    return name


def _js_close(frame, line, found, seen):
    if frame["kind"] != "function":
        return
    seen[frame["key"]] = seen.get(frame["key"], 0) + 1
    key = frame["key"] if seen[frame["key"]] == 1 else f"{frame['key']} #{seen[frame['key']]}"
    found[key] = {"lines": line - frame["line"] + 1, "nesting": frame["deepest"]}


def _javascript(text):
    clean, frames, found, seen = _blank(text), [], {}, {}
    line, head_start = 1, 0
    for pos, c in enumerate(clean):
        if c == "\n":
            line += 1
        elif c == "{":
            kind, name = _js_frame(clean[head_start:pos], frames)
            owner = next((f for f in reversed(frames) if f["kind"] == "function"), None)
            depth = (frames[-1]["depth"] if frames else 0) + (kind == "control")
            frame = {"kind": kind, "name": name, "line": line, "deepest": 0,
                     "depth": 0 if kind == "function" else depth, "key": _js_key(name, frames) if name else None}
            if kind == "control" and owner:
                owner["deepest"] = max(owner["deepest"], frame["depth"])
            frames.append(frame)
            head_start = pos + 1
        elif c == "}":
            if frames:
                _js_close(frames.pop(), line, found, seen)
            head_start = pos + 1
        elif c == ";":
            head_start = pos + 1
    return found


# ---------- measuring ----------

def measure_file(rel, text, limits=None):
    """{"lines", "long_lines", "functions": {qualname: {"lines", "nesting"}}} for one file's text."""
    chars = (limits or DEFAULT_LIMITS).get("line_chars", DEFAULT_LIMITS["line_chars"])
    rows = text.splitlines()
    functions = _python(text) if rel.endswith(PY_EXTS) else _javascript(text) if rel.endswith(JS_EXTS) else {}
    return {"lines": len(rows), "long_lines": sum(1 for row in rows if len(row) > chars), "functions": functions}


def measure(root, files, limits):
    """Every file and every function of `files` (paths relative to root): what `check` compares."""
    result = {"files": {}, "functions": {}}
    for rel in files:
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = measure_file(rel, text, limits)
        result["files"][rel] = {"lines": m["lines"], "long_lines": m["long_lines"]}
        test = is_test_file(rel)
        for name, values in m["functions"].items():
            result["functions"][f"{rel}::{name}"] = dict(values, test=test)
    return result


# ---------- the ratchet ----------

def _function_allowance(limits, stored, test):
    """(lines allowed, nesting allowed or None) for a function: the limit, or its stored value when above it."""
    line_limit = limits["test_function_lines" if test else "function_lines"]
    lines = max(line_limit, stored.get("lines", 0))
    return lines, None if test else max(limits["nesting"], stored.get("nesting", 0))


def _file_allowance(limits, stored):
    base = stored.get("lines", 0)
    lines = base + limits["file_growth"] if base > limits["file_lines"] else limits["file_lines"]
    return lines, stored.get("long_lines", 0)


def _compare(kind, key, values, allowed, out):
    """Adds a violation (no values in it) and a detail line for every metric above its allowance."""
    for metric, now, limit in zip(("lines", "nesting", "long_lines"), values, allowed):
        if limit is not None and now is not None and now > limit:
            out["violations"].append(f"FAIL {kind} {key} exceeds its {metric} allowance")
            out["details"].append(f"  {key} {metric}: now {now}, allowed {limit}")


def check(baseline, current):
    limits, out = limits_of(baseline), {"violations": [], "details": [], "improvable": []}
    stored_functions, stored_files = baseline.get("functions") or {}, baseline.get("files") or {}
    for key, cur in sorted(current["functions"].items()):
        stored = stored_functions.get(key, {})
        lines, nesting = _function_allowance(limits, stored, cur.get("test"))
        _compare("function", key, (cur["lines"], cur["nesting"], None), (lines, nesting, None), out)
        if stored and (cur["lines"] < stored.get("lines", 0) or cur["nesting"] < stored.get("nesting", 0)):
            out["improvable"].append(key)
    for rel, cur in sorted(current["files"].items()):
        stored = stored_files.get(rel, {})
        lines, long_lines = _file_allowance(limits, stored)
        _compare("file", rel, (cur["lines"], None, cur["long_lines"]), (lines, None, long_lines), out)
        if stored and (cur["lines"] < stored.get("lines", 0) or cur["long_lines"] < stored.get("long_lines", 0)):
            out["improvable"].append(rel)
    out["ok"] = not out["violations"]
    return out


def _over_function(limits, values, test):
    line_limit = limits["test_function_lines" if test else "function_lines"]
    return values["lines"] > line_limit or (not test and values["nesting"] > limits["nesting"])


def _over_file(limits, values):
    return values["lines"] > limits["file_lines"] or values["long_lines"] > 0


def initial(current, limits, flow_repo):
    """A baseline that records every current offender, so that the code as it is passes."""
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    functions = {key: {"lines": v["lines"], "nesting": v["nesting"]}
                 for key, v in sorted(current["functions"].items()) if _over_function(limits, v, v.get("test"))}
    files = {rel: dict(v) for rel, v in sorted(current["files"].items()) if _over_file(limits, v)}
    return {"version": 1, "flow_repo": bool(flow_repo), "limits": limits, "exclude": [], "files": files,
            "functions": functions}


def tighten(baseline, current):
    """The baseline lowered to the current values: never raised, never extended, and an entry within the limits or no
    longer present is dropped."""
    limits, new = limits_of(baseline), dict(baseline)
    functions = {}
    for key, stored in (baseline.get("functions") or {}).items():
        cur = current["functions"].get(key)
        if cur is None:
            continue
        lowered = {"lines": min(stored.get("lines", 0), cur["lines"]),
                   "nesting": min(stored.get("nesting", 0), cur["nesting"])}
        if _over_function(limits, lowered, cur.get("test")):
            functions[key] = lowered
    files = {}
    for rel, stored in (baseline.get("files") or {}).items():
        cur = current["files"].get(rel)
        if cur is None:
            continue
        lowered = {"lines": min(stored.get("lines", 0), cur["lines"]),
                   "long_lines": min(stored.get("long_lines", 0), cur["long_lines"])}
        if _over_file(limits, lowered):
            files[rel] = lowered
    new["functions"], new["files"] = functions, files
    return new
