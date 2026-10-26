"""Regression tests over the flow's definitions: agents, commands, workflow, settings and documentation.

Prompts are code: if someone removes the injection defence, gives a reviewer Write
or documents a command that does not exist, these tests fail.

Run: python3 -m unittest discover -s .claude/bin -p "test_*.py"
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
AGENTS_DIR = CLAUDE / "agents"
COMMANDS_DIR = CLAUDE / "commands"
WORKFLOW = CLAUDE / "workflows" / "agentic-implement.js"
AGENTIC = CLAUDE / "bin" / "agentic.py"

WRITE_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "Bash"}
# Each agent's contract: model (alias), exact tools, the ID it claims its zone with, and its report.
AGENTS = {
    "implementer": {"model": "sonnet", "tools": WRITE_TOOLS, "claim": "<WP-ID>", "status": "STATUS: DONE | BLOCKER"},
    "test-author": {"model": "sonnet", "tools": WRITE_TOOLS, "claim": "AT", "status": "STATUS: DONE | BLOCKER"},
    "qa": {"model": "sonnet", "tools": WRITE_TOOLS, "claim": "QA", "status": "STATUS: PASS | FAIL | BLOCKER"},
    "reviewer": {"model": "sonnet", "tools": {"Read", "Glob", "Grep", "Bash"}, "claim": "REVIEW", "status": "STATUS: PASS | FAIL"},
    "verifier": {"model": "sonnet", "tools": {"Read", "Glob", "Grep"}},
    "plan-reviewer": {"model": "opus", "tools": {"Read", "Glob", "Grep"}},
    "gatekeeper": {"model": "sonnet", "tools": {"Bash"}},
    "doc-updater": {"model": "sonnet", "tools": WRITE_TOOLS, "claim": "DOCS", "status": "STATUS: DONE | BLOCKER"},
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
    src = AGENTIC.read_text(encoding="utf-8")
    cmds = set(re.findall(r'cmd == "([\w-]+)"', src))
    for group in re.findall(r"cmd in \(([^)]*)\)", src):
        cmds |= set(re.findall(r'"([\w-]+)"', group))
    return cmds


class AgentDefinitionTests(unittest.TestCase):
    def test_every_agent_has_a_contract(self):
        self.assertEqual({p.stem for p in AGENTS_DIR.glob("*.md")}, set(AGENTS),
                         "every new agent needs its contract in this test")

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
        for name in ("reviewer", "verifier", "plan-reviewer", "gatekeeper"):
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

    def test_report_format_and_json_note(self):
        for name, spec in AGENTS.items():
            if "status" not in spec:
                continue
            with self.subTest(agent=name):
                body = frontmatter(AGENTS_DIR / f"{name}.md")[1]
                self.assertIn(spec["status"], body)
                self.assertIn("JSON with a schema", body, "it must accept the workflow's structured format")
                self.assertRegex(body, r"under \d+ words")

    def test_qa_classifies_defects(self):
        body = (AGENTS_DIR / "qa.md").read_text(encoding="utf-8")
        self.assertIn("implementation", body)
        self.assertIn("design", body)

    def test_verifier_never_downgrades_blockers(self):
        body = (AGENTS_DIR / "verifier.md").read_text(encoding="utf-8")
        self.assertIn("Uncertainty never downgrades a blocker", body)

    def test_gatekeeper_only_agentic(self):
        body = (AGENTS_DIR / "gatekeeper.md").read_text(encoding="utf-8")
        self.assertIn("python3 .claude/bin/agentic.py", body)


class CommandAndWorkflowTests(unittest.TestCase):
    def test_commands_and_models(self):
        self.assertEqual({p.stem for p in COMMANDS_DIR.glob("*.md")}, set(COMMANDS))
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
        src = AGENTIC.read_text(encoding="utf-8")
        block = re.search(r"STACK_SIGNALS = \{(.*?)\n\}", src, re.S).group(1)
        detected = set(re.findall(r'^\s+"([a-z]+)": \(', block, re.M))
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
        self.assertTrue(any("agentic.py" in c and c.strip().endswith("hook") for c in commands))
        matchers = "|".join(e.get("matcher", "") for e in entries)
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"):
            self.assertIn(tool, matchers)

    def test_permissions_and_workflows(self):
        perms = self.settings["permissions"]
        self.assertIn("Workflow(agentic-implement)", perms["allow"])
        self.assertTrue(self.settings.get("enableWorkflows"))
        for rule in ("Bash(git reset --hard:*)", "Bash(git clean:*)", "Bash(rm -rf:*)"):
            self.assertIn(rule, perms["deny"])
        self.assertFalse(any("git push" in r for r in perms["deny"]), "the hook blocks push only for agents")


class CIHardeningTests(unittest.TestCase):
    """The repo's CI and the guide's example use SHA-pinned actions and minimal permissions."""
    USES_RE = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@([^\s#]+)")

    def sources(self):
        yield ".github/workflows/agentic-flow-ci.yml", (ROOT / ".github/workflows/agentic-flow-ci.yml").read_text(encoding="utf-8")
        guide = (ROOT / "docs/how-to/ci-github-actions.md").read_text(encoding="utf-8")
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

    def test_init_uses_the_installer(self):
        fm, body = frontmatter(ROOT / "plugin" / "commands" / "init.md")
        self.assertEqual(fm.get("model"), "sonnet")
        self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/.claude/bin/agentic.py" install', body)
        self.assertIn("Do not commit or push", body)

    def test_what_is_installed_exists(self):
        src = AGENTIC.read_text(encoding="utf-8")
        globs = re.findall(r'"(\.claude/[^"]+)"', re.search(r"INSTALL_GLOBS = \[(.*?)\]", src, re.S).group(1))
        for pattern in globs:
            with self.subTest(patron=pattern):
                self.assertTrue(list(ROOT.glob(pattern)), "the installer would copy nothing")


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


if __name__ == "__main__":
    unittest.main()
