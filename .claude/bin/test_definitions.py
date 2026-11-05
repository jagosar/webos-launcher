"""Regression tests over the flow's definitions: agents, commands, workflow, settings and documentation.

Prompts are code: if someone removes the injection defence, gives a reviewer Write
or documents a command that does not exist, these tests fail.

Run: python3 -m unittest discover -s .claude/bin -p "test_*.py"
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
AGENTS_DIR = CLAUDE / "agents"
COMMANDS_DIR = CLAUDE / "commands"
WORKFLOW = CLAUDE / "workflows" / "agentic-implement.js"
AGENTIC = CLAUDE / "bin" / "agentic.py"
sys.path.insert(0, str(AGENTIC.parent))
import agentic  # noqa: E402
import installer  # noqa: E402
import repo_scan  # noqa: E402

# Tests that hold only in the flow's own repository. The guard tolerates a project with no README (is_flow_repo does),
# and a guard that raised at import time would stop every test in this file from loading there.
FLOW_REPO_ONLY = unittest.skipUnless(agentic.is_flow_repo(ROOT), "the flow's own repo")

# Every agent run-pipeline starts with --json-schema lists StructuredOutput: with a `tools:` list that leaves it out,
# `claude -p --agent` (seen with CLI 2.1.316) returns no structured_output and the run stops with NO REPORT.
WRITE_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "Bash", "StructuredOutput"}
# Each agent's contract: model (alias), exact tools, the ID it claims its zone with, and its report.
AGENTS = {
    "implementer": {"model": "sonnet", "tools": WRITE_TOOLS, "claim": "<WP-ID>"},
    "test-author": {"model": "sonnet", "tools": WRITE_TOOLS, "claim": "AT"},
    "qa": {"model": "sonnet", "tools": WRITE_TOOLS, "claim": "QA"},
    "reviewer": {"model": "sonnet", "tools": {"Read", "Glob", "Grep", "Bash", "StructuredOutput"}, "claim": "REVIEW"},
    "verifier": {"model": "sonnet", "tools": {"Read", "Glob", "Grep", "StructuredOutput"}},
    "plan-reviewer": {"model": "opus", "tools": {"Read", "Glob", "Grep"}},
    "test-auditor": {"model": "sonnet", "tools": {"Read", "Glob", "Grep", "StructuredOutput"}},
    "gatekeeper": {"model": "haiku", "tools": {"Bash"}},
    "doc-updater": {"model": "sonnet", "tools": WRITE_TOOLS, "claim": "DOCS"},
}
COMMANDS = {"architect": "opus", "implement": "sonnet"}
# Manifest fields agentic.py understands: all must be documented in /architect.
MANIFEST_FIELDS = ["work_packages", "id", "wave", "depends_on", "files", "allow_bash", "acceptance", "verify",
                   "qa", "ignore", "agent", "mirror", "config_edit", "stack", "docs", "codemap"]
RULES_LIBRARY = CLAUDE / "rules-library"
RULE_SECTIONS = ["## Style", "## Errors", "## Tests", "## Security", "## Common commands"]
PERSONAL_PATH_RE = re.compile(r"/home/[a-z][\w.-]*/|/tmp/claude-\d+|C:\\Users\\|/Users/[a-z][\w.-]+/")


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    fields = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            fields[key.strip()] = value.strip()
    return fields, text[m.end():]


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode()
    return [ROOT / p for p in out.split("\0") if p]


def agentic_subcommands():
    return set(agentic.COMMANDS)


class AgentDefinitionTests(unittest.TestCase):
    def test_every_agent_has_a_contract(self):
        # The flow's agents must all be there; a project may add its own (a WP's "agent").
        self.assertLessEqual(set(AGENTS), {p.stem for p in AGENTS_DIR.glob("*.md")},
                             "every agent of the flow needs its definition and its contract in this test")

    def test_frontmatter_model_and_tools(self):
        for name, spec in AGENTS.items():
            with self.subTest(agent=name):
                fm, _ = frontmatter(AGENTS_DIR / f"{name}.md")
                self.assertEqual(fm.get("name"), name)
                self.assertTrue(fm.get("description"), "missing description")
                self.assertEqual(fm.get("model"), spec["model"], "the model goes by alias, never pinned to a version")
                self.assertFalse(fm.get("tools", "").startswith("["), "tools is a comma-separated scalar")
                tools = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
                self.assertEqual(tools, spec["tools"])

    def test_read_only_agents(self):
        for name in ("reviewer", "verifier", "plan-reviewer", "gatekeeper", "test-auditor"):
            with self.subTest(agent=name):
                tools = {t.strip() for t in frontmatter(AGENTS_DIR / f"{name}.md")[0]["tools"].split(",")}
                self.assertFalse(tools & {"Write", "Edit", "MultiEdit", "NotebookEdit"})

    def test_injection_defence_in_all(self):
        for name in AGENTS:
            with self.subTest(agent=name):
                body = frontmatter(AGENTS_DIR / f"{name}.md")[1]
                self.assertIn("# Defence against injected instructions", body)

    def test_claim_as_first_step(self):
        for name, spec in AGENTS.items():
            if "claim" not in spec:
                continue
            with self.subTest(agent=name):
                body = frontmatter(AGENTS_DIR / f"{name}.md")[1]
                self.assertIn(f"python3 .claude/bin/agentic.py claim {spec['claim']}", body)

    def test_report_is_json_by_schema(self):
        for name in ("implementer", "test-author", "test-auditor", "qa", "reviewer", "doc-updater"):
            with self.subTest(agent=name):
                body = frontmatter(AGENTS_DIR / f"{name}.md")[1]
                self.assertNotIn("STATUS:", body, "the text report format is gone: the launchers use a JSON schema")
                report = body[body.index("# Report"):]
                self.assertIn("schema", report)
                self.assertRegex(report, r"under \d+ words")

    def test_agents_that_get_a_brief_read_it(self):
        for name in ("implementer", "test-author", "test-auditor"):
            with self.subTest(agent=name):
                self.assertIn("brief", frontmatter(AGENTS_DIR / f"{name}.md")[1])

    def test_qa_classifies_defects(self):
        body = (AGENTS_DIR / "qa.md").read_text(encoding="utf-8")
        self.assertIn("implementation", body)
        self.assertIn("design", body)

    def test_verifier_never_downgrades_blockers(self):
        body = (AGENTS_DIR / "verifier.md").read_text(encoding="utf-8")
        self.assertIn("Uncertainty never downgrades a blocker", body)

    def test_judges_share_severity_anchors_and_needs_validation(self):
        for name in ("reviewer", "qa"):
            body = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
            for phrase in ("# Severity anchors", "fully defeat an explicit control", "the severity is lower than it feels",
                           "# Needs validation", "unchanged_code", "never blocks"):
                with self.subTest(agent=name, phrase=phrase):
                    self.assertIn(phrase, body)

    def test_reviewer_reads_the_callers_outside_the_diff(self):
        body = (AGENTS_DIR / "reviewer.md").read_text(encoding="utf-8")
        for phrase in ("the callers your prompt lists", "Read each one and check that the change keeps it working",
                       "a caller your prompt lists that the change breaks"):
            self.assertIn(phrase, body)

    def test_findings_have_four_parts(self):
        self.assertIn("No concrete case, no finding", (AGENTS_DIR / "reviewer.md").read_text(encoding="utf-8"))
        for name in ("reviewer", "qa"):
            body = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
            report = body[body.index("# Report"):]
            for field in ("`fix`", "`if_skipped`", "`needs_validation`"):
                with self.subTest(agent=name, field=field):
                    self.assertIn(field, report)

    def test_reports_say_what_was_not_checked(self):
        for name in ("implementer", "test-author", "test-auditor", "reviewer"):
            with self.subTest(agent=name):
                body = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn("`not_checked`", body[body.index("# Report"):])

    def test_implementer_reuses_before_writing(self):
        body = (AGENTS_DIR / "implementer.md").read_text(encoding="utf-8")
        for phrase in ("# Before writing", "already in this codebase", "standard library", "grep every caller", "root cause"):
            self.assertIn(phrase, body)
        common = (RULES_LIBRARY / "common.md").read_text(encoding="utf-8")
        for phrase in ("already in this codebase", "every caller"):
            self.assertIn(phrase, common)

    def test_test_auditor_knows_tautological_tests(self):
        body = (AGENTS_DIR / "test-auditor.md").read_text(encoding="utf-8")
        for phrase in ("Tautological", "passes by construction"):
            self.assertIn(phrase, body)

    def test_nothing_invented_in_docs_and_plans(self):
        self.assertIn("numbers included", (AGENTS_DIR / "doc-updater.md").read_text(encoding="utf-8"))
        architect = (COMMANDS_DIR / "architect.md").read_text(encoding="utf-8")
        for phrase in ("No invented figures", "not measured"):
            self.assertIn(phrase, architect)

    def test_architect_sequences_wide_refactors(self):
        doc = (COMMANDS_DIR / "architect.md").read_text(encoding="utf-8")
        parallelism = doc[doc.index("## Parallelism"):doc.index("## 2b.")]
        self.assertIn("expand-contract", parallelism)

    def test_implement_presents_needs_validation_and_not_checked(self):
        doc = (COMMANDS_DIR / "implement.md").read_text(encoding="utf-8")
        for phrase in ("Needs validation", "not_checked"):
            self.assertIn(phrase, doc)

    def test_implementer_has_four_statuses_and_knows_the_gate(self):
        body = (AGENTS_DIR / "implementer.md").read_text(encoding="utf-8")
        self.assertIn("# Statuses", body)
        for status in ("`DONE`", "`DONE_WITH_CONCERNS`", "`NEEDS_CONTEXT`", "`BLOCKED`"):
            self.assertIn(status, body)
        self.assertNotRegex(body, r"\bBLOCKER\b", "BLOCKER is no longer an implementer status")
        for phrase in ("# The gate", "twice at most", "never retry the same approach"):
            self.assertIn(phrase, body)
        report = body[body.index("# Report"):]
        for field in ("`concerns`", "`missing_context`", "`blocker`"):
            self.assertIn(field, report)

    def test_implement_presents_concerns_and_gate_denials(self):
        doc = (COMMANDS_DIR / "implement.md").read_text(encoding="utf-8")
        for phrase in ("`concern`", "`gate_denials`", "DONE_WITH_CONCERNS"):
            self.assertIn(phrase, doc)

    def test_gatekeeper_only_agentic(self):
        body = (AGENTS_DIR / "gatekeeper.md").read_text(encoding="utf-8")
        self.assertIn("python3 .claude/bin/agentic.py", body)

    def test_gatekeeper_does_not_poll_for_long_commands(self):
        # A `pgrep -f` wait loop matches its own command line and never ends: the stall seen at close-stage.
        body = (AGENTS_DIR / "gatekeeper.md").read_text(encoding="utf-8")
        section = body.split("# Long commands", 1)[1]
        for rule in ("timeout: 1800000", "Do not use `run_in_background`", "Never write a wait loop",
                     "`pgrep -f`", "`exit_code: 124`", "do not run the command again"):
            self.assertIn(rule, section)


    def test_gatekeeper_never_guesses_the_exit_code(self):
        # Bash shows "Exit code N" only when N is not 0; a gatekeeper that took the code from the output stopped a
        # run at a baseline that had exited 0.
        body = (AGENTS_DIR / "gatekeeper.md").read_text(encoding="utf-8")
        for rule in ("no `Exit code` line", "`exit_code: 0`", "Never deduce the exit code from the output"):
            self.assertIn(rule, body)

class CommandAndWorkflowTests(unittest.TestCase):
    def test_commands_and_models(self):
        # The flow's commands must all be there; a project may add its own.
        self.assertLessEqual(set(COMMANDS), {p.stem for p in COMMANDS_DIR.glob("*.md")})
        for name, model in COMMANDS.items():
            with self.subTest(command=name):
                self.assertEqual(frontmatter(COMMANDS_DIR / f"{name}.md")[0].get("model"), model)

    def test_manifest_fields_documented(self):
        doc = (COMMANDS_DIR / "architect.md").read_text(encoding="utf-8")
        for field in MANIFEST_FIELDS:
            with self.subTest(campo=field):
                self.assertTrue(f"`{field}`" in doc or f'"{field}"' in doc, f"{field} no aparece en /architect")

    def test_architect_confirms_model_before_writing(self):
        doc = (COMMANDS_DIR / "architect.md").read_text(encoding="utf-8")
        gate, writing = doc.find("## 2b. Confirm before writing"), doc.find("## 3. Writing architecture.md")
        self.assertTrue(0 <= gate < writing, "the model gate must come before writing architecture.md")
        for phrase in ("AskUserQuestion", "Pause to switch model", "Do not write the file", "No interaction"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, doc[gate:writing])

    def test_workflow_uses_existing_agents(self):
        src = WORKFLOW.read_text(encoding="utf-8")
        used = set(re.findall(r"agentType: '([\w-]+)'", src)) | {"implementer"}
        self.assertTrue(used, "no agentType found")
        self.assertLessEqual(used, set(AGENTS), f"agentType without a definition: {used - set(AGENTS)}")

    def test_workflow_and_agents_share_states(self):
        src = WORKFLOW.read_text(encoding="utf-8")
        for enum in ("['DONE', 'BLOCKER']", "['PASS', 'FAIL']", "['PASS', 'FAIL', 'BLOCKER']", "['implementation', 'design']"):
            with self.subTest(enum=enum):
                self.assertIn(enum, src)

    def test_workflow_calls_existing_commands(self):
        src = WORKFLOW.read_text(encoding="utf-8")
        called = set(re.findall(r"gate\(`([\w-]+)", src)) | set(re.findall(r"gate\('([\w-]+)", src))
        self.assertTrue(called)
        self.assertLessEqual(called, agentic_subcommands(), f"the workflow calls: {called - agentic_subcommands()}")

    def test_workflow_meta(self):
        src = WORKFLOW.read_text(encoding="utf-8")
        self.assertTrue(src.startswith("export const meta = {"), "meta must be the first statement")
        self.assertIn("name: 'agentic-implement'", src)
        for forbidden in ("Date.now(", "Math.random(", "import("):
            self.assertNotIn(forbidden, src, f"{forbidden} breaks the workflow's resumption")


class RulesLibraryTests(unittest.TestCase):
    """Every language agentic.py detects has its rules, with the same skeleton and its attribution."""

    def packs(self):
        return {p.stem for p in RULES_LIBRARY.glob("*.md")} - {"common"}

    def test_detection_and_library_match(self):
        detected = set(repo_scan.STACK_SIGNALS)
        self.assertEqual(detected, self.packs(), "a detectable language without rules, or rules that are never installed")

    def test_language_packs(self):
        for name in sorted(self.packs()):
            with self.subTest(language=name):
                text = (RULES_LIBRARY / f"{name}.md").read_text(encoding="utf-8")
                self.assertRegex(text, r'\A---\npaths:\n(  - "[^"]+"\n)+---\n', "paths: at the top, so they only "
                                 "load around files of that language")
                self.assertIn("](common.md)", text, "it must point to the common rules")
                self.assertIn("MIT License", text, "attribution to ECC")
                positions = [text.find(h) for h in RULE_SECTIONS]
                self.assertNotIn(-1, positions, "faltan secciones")
                self.assertEqual(positions, sorted(positions), "the sections follow the common order")
                self.assertRegex(text, r"\| build \|.*\n\| types \|.*\n\| lint \|.*\n\| tests \|", "fases de verify")

    def test_common_always_loaded_with_precedence(self):
        text = (RULES_LIBRARY / "common.md").read_text(encoding="utf-8")
        self.assertFalse(text.startswith("---"), "no paths: always loaded")
        self.assertIn("## Precedence", text)
        self.assertIn("`architecture.md`", text.split("## Precedence")[1].split("##")[0])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))

    def test_hook_registered(self):
        entries = self.settings["hooks"]["PreToolUse"]
        commands = [h["command"] for e in entries for h in e["hooks"]]
        # 2.5.0: the launcher hook.py, which runs the live agentic.py.
        self.assertIn('python3 "$CLAUDE_PROJECT_DIR/.claude/bin/hook.py"', commands)
        self.assertFalse([c for c in commands if "agentic.py" in c and c.strip().endswith("hook")],
                         "the old command must be gone")
        matchers = "|".join(e.get("matcher", "") for e in entries)
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"):
            self.assertIn(tool, matchers)

    def test_permissions_and_workflows(self):
        perms = self.settings["permissions"]
        self.assertIn("Workflow(agentic-implement)", perms["allow"])
        self.assertFalse([r for r in perms["allow"] if "state/guard" in r], "the frozen guard is gone (2.10.0)")
        self.assertTrue(self.settings.get("enableWorkflows"))
        for rule in ("Bash(git reset --hard:*)", "Bash(git clean:*)", "Bash(rm -rf:*)"):
            self.assertIn(rule, perms["deny"])
        self.assertFalse(any("git push" in r for r in perms["deny"]), "the hook blocks push only for agents")


    def test_the_gatekeeper_accepts_only_the_live_command(self):
        rule = next(line for line in (CLAUDE / "agents" / "gatekeeper.md").read_text(encoding="utf-8").splitlines()
                    if "are accepted" in line)
        self.assertIn("`python3 .claude/bin/agentic.py`", rule)
        self.assertNotIn("state/guard", rule)


class CIHardeningTests(unittest.TestCase):
    """The repo's CI and the guide's example use SHA-pinned actions and minimal permissions."""
    USES_RE = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@([^\s#]+)")

    def sources(self):
        yield ".github/workflows/agentic-flow-ci.yml", (ROOT / ".github/workflows/agentic-flow-ci.yml").read_text(encoding="utf-8")
        release = ROOT / ".github/workflows/agentic-flow-release.yml"
        if release.exists():  # the flow's own release workflow; projects do not carry it
            yield ".github/workflows/agentic-flow-release.yml", release.read_text(encoding="utf-8")
        guide_path = ROOT / "docs/how-to/ci-github-actions.md"
        if guide_path.exists():  # a project may not carry the flow's guides; its copy of the CI file is still checked
            guide = guide_path.read_text(encoding="utf-8")
            yield "docs/how-to/ci-github-actions.md", "\n".join(re.findall(r"```yaml\n(.*?)```", guide, re.S))

    def test_actions_pinned_by_sha(self):
        for name, text in self.sources():
            uses = self.USES_RE.findall(text)
            self.assertTrue(uses, f"{name}: no uses: found")
            for action, ref in uses:
                with self.subTest(file=name, action=action):
                    self.assertRegex(ref, r"^[0-9a-f]{40}$", "pin the action by full SHA, not by tag")

    def test_minimal_permissions_no_credentials(self):
        for name, text in self.sources():
            with self.subTest(file=name):
                self.assertRegex(text, r"permissions:\s*\n\s+contents: read")
                self.assertIn("persist-credentials: false", text)
                self.assertNotRegex(text, r"curl[^\n|]*\|\s*(ba)?sh", "nada de curl | sh")

    def test_npm_without_scripts_and_exact_version(self):
        for name, text in self.sources():
            installs = re.findall(r"npm (?:install|i|ci)\b[^\n]*", text)
            for line in installs:
                with self.subTest(file=name, line_no=line):
                    self.assertIn("--ignore-scripts", line, "no package runs code on install")
                    self.assertRegex(line, r"@\d+\.\d+\.\d+(\s|$)", "exact version")


class PluginTests(unittest.TestCase):
    """The plugin only installs and updates: it exposes no agents, hooks or prefixed workflows."""

    def setUp(self):
        self.plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))

    def test_manifest(self):
        self.assertEqual(self.plugin["name"], "agentic-flow")
        self.assertRegex(self.plugin["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(self.plugin.get("description") and self.plugin.get("author", {}).get("name"))
        for rel in self.plugin["commands"]:
            with self.subTest(command=rel):
                self.assertTrue(rel.startswith("./") and ".." not in rel)
                self.assertTrue((ROOT / rel).is_file())
        for key in ("agents", "hooks", "workflows", "skills", "mcpServers"):
            self.assertNotIn(key, self.plugin, f"{key}: the flow lives in the repo, not in the plugin")

    def test_no_folders_the_plugin_would_load_itself(self):
        for folder in ("agents", "commands", "skills", "hooks", "workflows", "bin", "output-styles"):
            with self.subTest(folder=folder):
                self.assertFalse((ROOT / folder).exists(), "Claude Code would load it as a plugin component")

    def test_marketplace_points_to_plugin(self):
        entry = self.market["plugins"][0]
        self.assertEqual(entry["name"], self.plugin["name"])
        self.assertEqual(entry["source"], "./")
        self.assertNotIn("version", entry, "the version lives in plugin.json; two copies end up out of sync")

    def test_the_version_has_its_upgrade_note(self):
        # One check for every release, instead of a version pinned in each release's acceptance tests.
        import upgrade
        newest = max(upgrade.NOTES, key=lambda v: tuple(int(x) for x in v.split(".")))
        self.assertEqual(self.plugin["version"], newest, "a release adds its note to upgrade.NOTES")
        self.assertTrue(upgrade.NOTES[newest])

    def test_init_uses_the_installer(self):
        fm, body = frontmatter(ROOT / "plugin" / "commands" / "init.md")
        self.assertEqual(fm.get("model"), "sonnet")
        self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/.claude/bin/agentic.py" install', body)
        self.assertIn("Do not commit or push", body)
        for flag in ("--dry-run", "--force", "--allow-dirty"):
            self.assertIn(flag, fm.get("argument-hint", ""))

    def test_what_is_installed_exists(self):
        globs = installer.INSTALL_GLOBS
        for pattern in globs:
            with self.subTest(patron=pattern):
                self.assertTrue(list(ROOT.glob(pattern)), "the installer would copy nothing")


    @FLOW_REPO_ONLY  # a project's README and its own skills are not the flow's to check
    def test_shipped_skills_are_valid_and_credited(self):
        skills = sorted((CLAUDE / "skills").glob("*/SKILL.md"))
        self.assertTrue(skills)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for path in skills:
            with self.subTest(skill=path.parent.name):
                fm, body = frontmatter(path)
                self.assertEqual(fm.get("name"), path.parent.name, "the name must match the folder")
                self.assertTrue(fm.get("description"))
                self.assertIn(f"`/{path.parent.name}`", readme, "listed in What's inside")
                self.assertIn("MIT", body, "third-party skills keep their licence notice")


class StructureTests(unittest.TestCase):
    """agentic.py is the entry point: a dispatch table and re-exports; the code lives in the modules by domain."""
    MODULES = ["core", "manifest_check", "repo_scan", "verification", "stage", "installer", "launch", "guardrail"]
    REEXPORTS = {
        "core": ["ROOT", "QUALITY_BASELINE", "SKIP_DIRS", "USAGE", "is_flow_repo", "maintainer_mode",
                 "never_owned_problem", "flow_path_refused", "locked_json", "usage_records", "status_to_state"],
        "manifest_check": ["validate", "frontmatter_model", "require_valid"], "repo_scan": ["PATH_RE"],
        "verification": ["attribute", "run_command", "quality_files"], "installer": ["merge_settings"],
        "launch": ["structured_report", "run_steps", "render_prompt", "load_spec"],
    }

    @FLOW_REPO_ONLY  # a project's code is its own; the ratchet there is the project's baseline
    def test_quality_is_green(self):
        r = subprocess.run([sys.executable, str(AGENTIC), "quality"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])

    def test_reexports_are_the_modules_objects(self):
        import importlib
        for module, names in self.REEXPORTS.items():
            mod = importlib.import_module(module)
            for name in names:
                with self.subTest(name=name):
                    self.assertIs(getattr(agentic, name), getattr(mod, name))
        src = AGENTIC.read_text(encoding="utf-8")
        for module in self.MODULES:
            self.assertIn(f"from {module} import", src)
        for name in ("validate", "install", "guard", "check_stage", "run_pipeline", "hook"):
            self.assertNotIn(f"\ndef {name}(", src, "a moved function must not keep a copy in agentic.py")

    def test_commands_match_the_docstring(self):
        listed = set(re.findall(r"^  ([a-z][\w-]+)", agentic.__doc__.split("Subcommands:")[1], re.M))
        self.assertEqual(set(agentic.COMMANDS), listed)
        self.assertEqual(len(agentic.COMMANDS), 34)

    @FLOW_REPO_ONLY  # a project does not carry the flow's own guides
    def test_docs_list_every_module(self):
        tree = (ROOT / "docs/how-it-works.md").read_text(encoding="utf-8")
        for module in self.MODULES:
            with self.subTest(module=module):
                self.assertIn(f"── {module}.py", tree)


class DocumentationDriftTests(unittest.TestCase):
    def docs(self):
        return [p for p in tracked_files() if p.suffix == ".md"]

    def test_cited_commands_exist(self):
        valid = agentic_subcommands()
        for path in self.docs():
            text = path.read_text(encoding="utf-8")
            # Code citations only: "python3 .claude/bin/agentic.py cmd" or "`agentic.py cmd"; wildcards (run-*) are ignored.
            found = re.findall(r"(?:python3 \.claude/bin/|`)agentic\.py ([a-z][\w-]*)(\*?)", text)
            for cmd in {c for c, star in found if not star}:
                with self.subTest(file=path.relative_to(ROOT).as_posix(), command=cmd):
                    self.assertIn(cmd, valid)

    def test_internal_links(self):
        def anchors(text):
            return {re.sub(r"[^\w\s-]", "", h.strip().lower(), flags=re.U).replace(" ", "-")
                    for h in re.findall(r"^#+ (.+)$", text, re.M)}
        for path in self.docs():
            text = path.read_text(encoding="utf-8")
            for link in re.findall(r"\]\(([^)\s]+)\)", text):
                if link.startswith(("http://", "https://", "mailto:")):
                    continue
                target_rel, _, frag = link.partition("#")
                target = (path.parent / target_rel) if target_rel else path
                with self.subTest(file=path.relative_to(ROOT).as_posix(), link=link):
                    self.assertTrue(target.exists(), "enlace roto")
                    if frag and target.suffix == ".md":
                        self.assertIn(frag, anchors(target.read_text(encoding="utf-8")), "ancla inexistente")

    def test_well_formed_mermaid_blocks(self):
        kinds = ("flowchart", "sequenceDiagram", "stateDiagram-v2", "graph")
        for path in self.docs():
            text = path.read_text(encoding="utf-8")
            for block in re.findall(r"```mermaid\n(.*?)```", text, re.S):
                with self.subTest(file=path.relative_to(ROOT).as_posix()):
                    self.assertTrue(block.strip().startswith(kinds), "tipo de diagrama desconocido")

    def test_no_personal_paths(self):
        me = Path(__file__).resolve()
        for path in tracked_files():
            if path.resolve() == me or path.suffix in (".gif", ".png", ".svg"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                self.assertIsNone(PERSONAL_PATH_RE.search(text), "personal path in a tracked file")



class MovedDefinitionTests(unittest.TestCase):
    """Moved from the retired per-release acceptance files (design 0017): test_acceptance ac_009 and ac_012,
    test_acceptance_portable ac_010 and test_acceptance_v24 ac_006."""

    def test_an_unknown_command_prints_the_usage(self):
        r = subprocess.run([sys.executable, str(AGENTIC), "no-such-command"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Subcommands", r.stderr)

    def test_the_plan_templates_use_the_headings_the_slicer_reads(self):
        architect = (COMMANDS_DIR / "architect.md").read_text(encoding="utf-8")
        self.assertIn("#### WP-NN · ", architect)
        self.assertIn("`acs`", architect)
        reviewer = (CLAUDE / "agents" / "plan-reviewer.md").read_text(encoding="utf-8")
        self.assertIn("#### WP-", reviewer)
        self.assertIn("- AC-NNN ·", reviewer)

    def test_agents_and_settings_call_agentic_py_through_python3(self):
        settings = (CLAUDE / "settings.json").read_text(encoding="utf-8")
        self.assertIn("Bash(python3 .claude/bin/agentic.py:*)", json.loads(settings)["permissions"]["allow"])
        workflow = (CLAUDE / "workflows" / "agentic-implement.js").read_text(encoding="utf-8")
        self.assertIn("python3 .claude/bin/agentic.py", workflow)
        implementer = (CLAUDE / "agents" / "implementer.md").read_text(encoding="utf-8")
        for text in (settings, workflow, implementer):
            self.assertNotIn("sh .claude/bin/agentic", text)

    def test_implement_checks_the_hook_before_anything_else(self):
        text = (COMMANDS_DIR / "implement.md").read_text(encoding="utf-8")
        pre = text.split("# 1. Pre-flight validation")[1].split("# 2.")[0]
        steps = [line for line in pre.splitlines() if re.match(r"\d+\. ", line)]
        self.assertIn("agentic.py hooks-check", steps[0])
        self.assertTrue(any("agentic.py validate" in s for s in steps[1:]))

if __name__ == "__main__":
    unittest.main()
