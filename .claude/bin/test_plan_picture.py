"""Tests of plan_picture.render: layout, escaping and determinism, on manifests built here.

Run: python3 -m unittest discover -s .claude/bin -p "test_plan_picture.py"
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_picture  # noqa: E402
from test_agentic import MANIFEST, Repo  # noqa: E402


def last_json(text):
    return json.loads([line for line in text.splitlines() if line.startswith("{")][-1])


def wp(ident, wave, depends_on=(), files=("src/x.py",), acs=()):
    return {"id": ident, "wave": wave, "depends_on": list(depends_on), "files": list(files), "acs": list(acs)}


class RenderTests(unittest.TestCase):
    def test_columns_by_wave_and_rows_by_id(self):
        page = plan_picture.render({"work_packages": [wp("WP-02", 1), wp("WP-03", 2), wp("WP-01", 1)]})
        spots = {m[0]: (int(m[1]), int(m[2])) for m in
                 re.findall(r'<g id="wp-(WP-\d+)"><rect x="(\d+)" y="(\d+)"', page)}
        self.assertEqual(spots["WP-01"][0], spots["WP-02"][0])
        self.assertLess(spots["WP-01"][1], spots["WP-02"][1])
        self.assertGreater(spots["WP-03"][0], spots["WP-01"][0])

    def test_a_dependency_outside_the_manifest_draws_no_line(self):
        page = plan_picture.render({"work_packages": [wp("WP-01", 1, ["WP-09"]), wp("WP-02", 2, ["WP-01"])]})
        self.assertEqual(re.findall(r'<line class="dep" data-from="([^"]+)" data-to="([^"]+)"', page),
                         [("WP-01", "WP-02")])
        self.assertIn("<td>WP-09</td>", page, "the table still lists it")

    def test_counts_and_every_file_and_criterion_are_shown(self):
        page = plan_picture.render({"work_packages": [wp("WP-01", 1, files=["a.py", "b/"], acs=["AC-001"])]})
        self.assertIn("2 files · 1 ACs", page)
        self.assertIn("<td>a.py, b/</td>", page)
        self.assertIn("<td>AC-001</td>", page)

    def test_the_title_and_ids_are_escaped(self):
        page = plan_picture.render({"work_packages": [wp('W"<x>', 1)]}, title="<b>&")
        self.assertIn("<title>&lt;b&gt;&amp;</title>", page)
        self.assertIn('<g id="wp-W&quot;&lt;x&gt;"', page)
        self.assertNotIn("<x>", page)

    def test_an_empty_plan_is_still_a_page(self):
        page = plan_picture.render({})
        self.assertEqual(page.count("<svg"), 1)
        self.assertEqual(page.count("<table>"), 1)
        self.assertNotIn("<g id=", page)

    def test_same_input_same_output_with_integer_coordinates(self):
        manifest = {"work_packages": [wp(f"WP-{i:02d}", i % 3 + 1, [f"WP-{i - 1:02d}"] if i > 1 else [])
                                      for i in range(1, 8)]}
        page = plan_picture.render(manifest)
        self.assertEqual(page, plan_picture.render(manifest))
        self.assertFalse(re.search(r'(?:x|y|x1|y1|x2|y2|width|height)="\d+\.\d', page))
        self.assertNotIn("<script", page)
        self.assertNotIn("http", page)


class CommandTests(unittest.TestCase):
    """From test_acceptance_v212 (design 0019, AC-005): the command writes the page, the same way every time."""

    def test_the_command_draws_the_manifest_and_refuses_an_invalid_one(self):
        manifest = dict(MANIFEST, work_packages=[
            {"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py"], "acs": ["AC-001"]},
            {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["src/<b>.py"], "acs": ["AC-002"]},
            {"id": "WP-03", "wave": 2, "depends_on": ["WP-01"], "files": ["src/c.py"], "acs": ["AC-003"]}])
        repo = Repo(manifest)
        self.addCleanup(repo.cleanup)
        r = repo.run("plan-picture")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(last_json(r.stdout), {"path": ".claude/state/plan.html", "packages": 3, "waves": 2})
        page = (repo.dir / ".claude/state/plan.html").read_bytes()
        self.assertEqual(repo.run("plan-picture").returncode, 0)
        self.assertEqual((repo.dir / ".claude/state/plan.html").read_bytes(), page, "deterministic")
        html = page.decode("utf-8")
        self.assertEqual(html.count("<svg"), 1)
        for wp in ("WP-01", "WP-02", "WP-03"):
            self.assertIn(f'<g id="wp-{wp}"', html)
            self.assertIn(f"<td>{wp}</td>", html)
        self.assertEqual(html.count('<line class="dep" data-from="WP-01" data-to="WP-03"'), 1)
        self.assertEqual(html.count('<line class="dep"'), 1)
        self.assertIn("src/&lt;b&gt;.py", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("http", html)
        bad = Repo(dict(MANIFEST, work_packages=[{"id": "WP-01", "wave": 1, "files": ["architecture.md"]}]))
        self.addCleanup(bad.cleanup)
        self.assertEqual(bad.run("plan-picture").returncode, 1)
        self.assertFalse((bad.dir / ".claude/state/plan.html").exists())


if __name__ == "__main__":
    unittest.main()
