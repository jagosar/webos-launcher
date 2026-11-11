"""Findings: the tier of a judge's finding, the plan's waivers, and whether a finding blocks.

One scale for every judge (both reviewers, QA, the test-auditor): floor never ships, reflex (a breach of a written
rule) blocks unless a waiver of the plan covers it, finish never blocks. A fingerprint names a finding across
reviews, so a re-review can say whether a fix addressed it. A finding with no tier keeps the meaning it
had before tiers existed: it blocks when its severity is one of the blocking ones. Pure functions, no imports from the
flow, so the bench and the engines' tests can load this module alone.
"""
TIERS = ("floor", "reflex", "finish")
SUM_MODULUS = 1000000007
FNV_OFFSET = 2166136261
FNV_PRIME = 16777619
HASH_MASK = 0xFFFFFFFF
PROBLEM_CHARS = 80
FINGERPRINT_PARTS = ("axis", "tier", "rule", "file")


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
