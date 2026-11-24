"""Findings: the tier of a judge's finding, the plan's waivers, and whether a finding blocks.

One scale for every judge (both reviewers, QA, the test-auditor): floor never ships, reflex (a breach of a written
rule) blocks unless a waiver of the plan covers it, finish never blocks. A fingerprint names a finding across
reviews, so a re-review can say whether a fix addressed it. A finding with no tier keeps the meaning it
had before tiers existed: it blocks when its severity is one of the blocking ones. Pure functions, no imports from the
flow, so the bench and the engines' tests can load this module alone.
"""
import re

TIERS = ("floor", "reflex", "finish")
SUM_MODULUS = 1000000007
FNV_OFFSET = 2166136261
FNV_PRIME = 16777619
HASH_MASK = 0xFFFFFFFF
PROBLEM_CHARS = 80
FINGERPRINT_PARTS = ("axis", "tier", "rule", "file")
# A whole path in a text, as attribute() matches it, with an ASCII word class so the workflow's mirror agrees.
PATH_BEFORE = r"(?<![A-Za-z0-9_./-])"
PATH_AFTER = r"(?![A-Za-z0-9_-])"
ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
QA_NOT_RUN_DETAIL = 'QA did not run: the environment refused its commands (see the pending items of kind "qa not run")'
QA_NOT_RUN_FACT = "not run: the environment refused it"


def tier_of(item, blocking):
    """item["tier"] when it is one of TIERS; else "floor" for a blocking severity, "finish" otherwise."""
    tier = item.get("tier") if isinstance(item, dict) else None
    if tier in TIERS:
        return tier
    severity = item.get("severity") if isinstance(item, dict) else None
    return "floor" if severity in blocking else "finish"


def in_scope(path, scope):
    """path is the scope, or lies under it when the scope ends in "/"."""
    if not isinstance(path, str) or not isinstance(scope, str) or not scope:
        return False
    return path.startswith(scope) if scope.endswith("/") else path == scope


def _rule_key(text):
    return text.strip().casefold() if isinstance(text, str) else ""


def waiver_for(item, waivers):
    """The first waiver that covers a reflex finding (its file in the scope, the rule names agree), or None."""
    if not isinstance(item, dict) or item.get("tier") != "reflex":
        return None
    rule = _rule_key(item.get("rule"))
    if not rule:
        return None
    for waiver in waivers or ():
        if not isinstance(waiver, dict):
            continue
        named = _rule_key(waiver.get("rule"))
        if named and (named in rule or rule in named) and in_scope(item.get("file"), waiver.get("scope")):
            return waiver
    return None


def blocks(item, blocking, waivers=()):
    """floor: True; reflex: True unless a waiver covers it; finish: False."""
    tier = tier_of(item, blocking)
    if tier == "reflex":
        return waiver_for(item, waivers) is None
    return tier == "floor"


def text_sum(text):
    """The sum of the code points of text, modulo 1000000007: a check that a long text arrived whole."""
    total = 0
    for ch in text:
        total = (total + ord(ch)) % SUM_MODULUS
    return total


def _part(finding, key):
    value = finding.get(key)
    return "" if value is None else str(value)


def fingerprint(finding):
    """"F" + 8 hex digits of FNV-1a (32-bit) over the code points of "<axis>|<tier>|<rule>|<file>|<problem>": the
    problem stripped of " \\t\\r\\n", lowered with str.lower() (as JS toLowerCase) and cut to its first 80 code points.
    The line and the evidence are left out: they change between reviews of the same finding."""
    problem = _part(finding, "problem").strip(" \t\r\n").lower()[:PROBLEM_CHARS]
    text = "|".join([_part(finding, k) for k in FINGERPRINT_PARTS] + [problem])
    h = FNV_OFFSET
    for ch in text:
        h = ((h ^ ord(ch)) * FNV_PRIME) & HASH_MASK
    return f"F{h:08x}"


def with_fingerprints(findings):
    """Copies of the findings, each with "fingerprint"; the second and later of a repeated value get "-2", "-3"."""
    seen, out = {}, []
    for f in findings:
        fp = fingerprint(f)
        seen[fp] = seen.get(fp, 0) + 1
        out.append({**f, "fingerprint": fp if seen[fp] == 1 else f"{fp}-{seen[fp]}"})
    return out


def _text(value):
    return value if isinstance(value, str) else ""


def _without_root(text, root):
    """text with "\\" read as "/" and every "<root>/" removed, the root compared ASCII-lowercased (a drive letter of
    another case still matches); the rest keeps its case."""
    text = text.replace("\\", "/")
    if not root:
        return text
    needle, low = (root.replace("\\", "/") + "/").translate(ASCII_LOWER), text.translate(ASCII_LOWER)
    out, i = [], 0
    while True:
        j = low.find(needle, i)
        if j < 0:
            return "".join(out) + text[i:]
        out.append(text[i:j])
        i = j + len(needle)


def _named(files, low):
    """The files (as written) whose whole path appears in the lowered text; a path ending in "/" names a directory."""
    return [f for f in files if isinstance(f, str) and f and re.search(
        PATH_BEFORE + re.escape(f.translate(ASCII_LOWER)) + ("" if f.endswith("/") else PATH_AFTER), low)]


def split_by_owner(findings, owners, root):
    """One axis's confirmed findings, each with "fingerprint". A finding whose evidence or fix names files of other
    packages becomes the original, with "scope" (its own files named), then one part per other owning package in
    ascending id order, with that package's "wp", "file", "scope" and "split_from", no "line", and its own fingerprint
    (suffixed -2, -3 ... when taken). A finding that names no other package is returned as the same object."""
    owners = owners if isinstance(owners, dict) else {}
    root = root if isinstance(root, str) else ""
    taken = {f.get("fingerprint") for f in findings if isinstance(f, dict)}
    out = []
    for f in findings:
        if not isinstance(f, dict):
            out.append(f)
            continue
        wp = f.get("wp") if isinstance(f.get("wp"), str) else None
        low = _without_root(_text(f.get("evidence")) + "\n" + _text(f.get("fix")), root).translate(ASCII_LOWER)
        others = {o: _named(owners[o], low) for o in sorted(owners) if o != wp and isinstance(owners[o], list)}
        others = {o: files for o, files in others.items() if files}
        if not others:
            out.append(f)
            continue
        own = _without_root(_text(f.get("file")), root)
        mine = _named(owners.get(wp) or [], low) if isinstance(owners.get(wp), list) else []
        out.append({**f, "scope": sorted(set(([own] if own else []) + mine))})
        for o, files in others.items():
            scope = sorted(set(files))
            part = {**f, "wp": o, "file": scope[0], "scope": scope, "split_from": f.get("fingerprint")}
            part.pop("line", None)
            base = fp = fingerprint(part)
            n = 2
            while fp in taken:
                fp, n = f"{base}-{n}", n + 1
            part["fingerprint"] = fp
            taken.add(fp)
            out.append(part)
    return out


def qa_environment_blocked(qa):
    """QA's report says the environment, not the code, kept it from validating."""
    return isinstance(qa, dict) and qa.get("status") == "BLOCKER" and qa.get("blocker_kind") == "environment"


def qa_not_run_items(qa):
    """The pending items of an environment-blocked QA report: one per command it could not run (strings, once each,
    in order), or one for QA's whole validation when it named none. [] for any other report."""
    if not qa_environment_blocked(qa):
        return []
    listed = qa.get("not_run") if isinstance(qa.get("not_run"), list) else []
    commands = []
    for cmd in listed:
        if isinstance(cmd, str) and cmd not in commands:
            commands.append(cmd)
    if not commands:
        return [_not_run_item("QA's validation", "allow QA's test commands, then run /implement again")]
    return [_not_run_item(cmd, "run " + cmd) for cmd in commands]


def _not_run_item(cmd, how):
    return {"type": "needs validation", "source": "qa", "wp": None, "file": None, "kind": "qa not run",
            "problem": cmd, "claim": cmd, "fact": QA_NOT_RUN_FACT, "how_to_check": how}
