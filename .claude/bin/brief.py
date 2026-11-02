"""Briefs: the slice of architecture.md each agent needs, cut by headings with no model.

An implementer or the test-author reads its brief instead of the whole document, which is the biggest cost of a run.
agentic.py writes them (`briefs`); both engines point the agents to them. Any plan that does not have the expected
headings falls back to the whole document, so old plans keep working.
"""
import json
import re

MANIFEST_RE = re.compile(r"```json\s+wp-manifest\s*\n.*?\n```\n?", re.S)
SECTION_RE = re.compile(r"^## (\d+b?)\. ")
AC_RE = re.compile(r"^- (AC-\d+)\b")
WP_RE = re.compile(r"^#### (WP-\d+)\b")
WP_SECTIONS = ("1", "2", "3", "4b", "5", "6", "7", "9", "11")
AT_SECTIONS = ("1", "2", "3", "4b", "5", "6", "7", "9")


def split(text):
    """[(kind, key, lines)]: the preamble, then one entry per '## N. ' section. Fenced lines never start a section."""
    parts, fenced = [("pre", None, [])], False
    for line in text.splitlines(keepends=True):
        fenced ^= line.lstrip().startswith(("```", "~~~"))
        m = None if fenced else SECTION_RE.match(line)
        if m:
            parts.append(("section", m.group(1), []))
        parts[-1][2].append(line)
    return parts


def blocks(lines, start_re):
    """(intro, {key: lines}): a block runs from a line matching start_re to the next one (or the end)."""
    intro, found, current, fenced = [], {}, None, False
    for line in lines:
        fenced ^= line.lstrip().startswith(("```", "~~~"))
        m = None if fenced else start_re.match(line)
        if m:
            current = found.setdefault(m.group(1), [])
        (intro if current is None else current).append(line)
    return intro, found


def subsections(lines):
    """Lines of section 8's body, split at '### ' (the first chunk is the intro before any heading)."""
    out, fenced = [[]], False
    for line in lines:
        fenced ^= line.lstrip().startswith(("```", "~~~"))
        if line.startswith("### ") and not fenced:
            out.append([])
        out[-1].append(line)
    return out


def section_eight(body, wanted_acs, wp_id):
    """Section 8 reduced to its intro, the wanted AC blocks (all if wanted_acs is None) and the WP's block (none for
    the test-author). None if a piece the brief needs is missing."""
    out, seen_ac, seen_wp = [], False, wp_id is None
    for sub in subsections(body):
        head = sub[0] if sub else ""
        if "Acceptance criteria" in head:
            seen_ac = True
            intro, found = blocks(sub, AC_RE)
            if wanted_acs is None:
                out += sub
            elif any(a not in found for a in wanted_acs):
                return None
            else:
                out += intro + [x for a, ls in found.items() if a in wanted_acs for x in ls]
        elif "Work packages" in head:
            if wp_id is None:
                continue
            intro, found = blocks(sub, WP_RE)
            if wp_id not in found:
                return None
            seen_wp = True
            out += intro + found[wp_id]
        else:
            out += sub
    return out if seen_ac and seen_wp else None


def slice_architecture(text, ident, manifest):
    """(brief markdown, sliced) for a WP id of the manifest or "AT"; (text, False) when the plan cannot be sliced."""
    wp = next((w for w in manifest.get("work_packages", []) if w.get("id") == ident), None)
    if ident == "AT":
        wanted, keep, tail = None, AT_SECTIONS, {"acceptance": manifest.get("acceptance")}
    elif wp and wp.get("acs"):
        wanted, keep, tail = wp["acs"], WP_SECTIONS, wp
    else:
        return text, False
    out = [f"> Brief for {ident}: the parts of architecture.md this agent needs. "
           "Read architecture.md only if this brief points to something it does not contain.\n\n"]
    seen_eight = False
    for kind, key, lines in split(MANIFEST_RE.sub("", text)):
        if kind == "pre" or key in keep:
            out += lines
        elif key == "8":
            seen_eight = True
            reduced = section_eight(lines[1:], wanted, None if ident == "AT" else ident)
            if reduced is None:
                return text, False
            out += lines[:1] + reduced
    if not seen_eight:  # without its criteria and its package the brief would hold nothing to build
        return text, False
    return "".join(out).rstrip("\n") + "\n\n```json\n" + json.dumps(tail, indent=2) + "\n```\n", True
