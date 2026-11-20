"""Runner reports: the failing tests of a JUnit or NUnit (2.x or 3.x) XML report that a test command writes.

The manifest's optional "reports" maps a verify phase or "acceptance" to the report its command writes. Verification,
the baseline and the stop gate read the failing tests from it by name; with no usable report they read the console.
A report is the flow's artefact: it is deleted before its command runs, so a stale one is never read. XML is parsed
with the standard library after a size check; it does not fetch DTDs or external entities.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import ROOT, VERIFY_PHASES  # noqa: E402

REPORT_KEYS = VERIFY_PHASES + ("acceptance",)
MAX_REPORT_BYTES = 10_000_000  # a larger file is not read: the console is used instead
MAX_MESSAGE = 200  # characters of a failure message kept
NAME_JOIN = " > "  # between a NUnit case's suites and its own name
NUNIT_FAILED = ("failure", "error", "failed")  # a test-case's result, lowercased


def _reports(manifest):
    reports = manifest.get("reports") if isinstance(manifest, dict) else None
    return reports if isinstance(reports, dict) else {}


def report_path(manifest, key):
    """The declared report (str) of a verify phase or "acceptance", or None. Never raises on a malformed manifest."""
    path = _reports(manifest).get(key)
    return path if isinstance(path, str) and path.strip() else None


def report_paths(manifest):
    """Every declared report path, for the stage snapshot; [] when there is none or the field is malformed."""
    return [p for p in (report_path(manifest, k) for k in REPORT_KEYS) if p]


def _inside_root(rel):
    try:
        (ROOT / rel).resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def clear_report(manifest, key):
    """Deletes the declared report before its command runs, so a stale file is never read. It never deletes a path
    that resolves outside ROOT, even in a manifest that was never validated."""
    rel = report_path(manifest, key)
    if rel and _inside_root(rel):
        try:
            (ROOT / rel).unlink(missing_ok=True)
        except (IsADirectoryError, PermissionError):  # a folder or a locked file is left alone; it is never read
            pass


def _one_line(text):
    first = (text or "").strip().splitlines()
    return first[0].strip()[:MAX_MESSAGE] if first else ""


def _message(node):
    """The first line of the failure (or error) child's message attribute, or of its text."""
    for child in node:
        if child.tag in ("failure", "error"):
            msg = child.get("message")
            if msg is None:
                sub = child.find("message")
                msg = sub.text if sub is not None else child.text
            return _one_line(msg)
    return ""


def _junit(root):
    failed = []
    for case in root.iter("testcase"):
        if not any(child.tag in ("failure", "error") for child in case):
            continue
        name, cls = case.get("name") or "", case.get("classname")
        failed.append({"name": " ".join((f"{cls}::{name}" if cls else name).split()), "file": case.get("file"),
                       "message": _message(case)})
    return failed


def _nunit(node, suites, failed):
    """Walks test-suite elements depth first, keeping the suites' names, and collects the failed test-cases."""
    for child in node:
        if child.tag == "test-suite":
            _nunit(child, suites + [child.get("name") or ""], failed)
        elif child.tag == "test-case":
            if (child.get("result") or "").lower() in NUNIT_FAILED:
                name = NAME_JOIN.join([s for s in suites if s] + [child.get("name") or ""])
                failed.append({"name": " ".join(name.split()), "file": None, "message": _message(child)})
        else:
            _nunit(child, suites, failed)  # <results> and other wrappers
    return failed


def failed_tests(path):
    """The failing tests of a JUnit or NUnit 2/3 report: [{"name", "file", "message"}], [] when it parsed and nothing
    failed. None when the file is missing, larger than MAX_REPORT_BYTES, unreadable, not XML, or neither format."""
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
            return None
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError, ValueError):
        return None
    if root.tag in ("testsuites", "testsuite"):
        return _junit(root)
    if root.tag in ("test-results", "test-run") or root.find(".//test-case") is not None:
        return _nunit(root, [], [])
    return None


def failure_text(tests):
    """One line per failing test: "FAILED <name> [<file>] <message>", without the parts that are missing."""
    lines = []
    for t in tests:
        line = f"FAILED {t['name']}" + (f" [{t['file']}]" if t.get("file") else "")
        lines.append(line + (f" {t['message']}" if t.get("message") else ""))
    return lines
