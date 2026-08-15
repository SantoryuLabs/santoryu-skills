"""Regression tests for skill installation.

The bug these guard: `install` used to stamp the checkout's absolute path into
the copied SKILL.md, so an install died the moment the repo was moved or
renamed -- which is exactly what happened when santoryu-cursor became
santoryu-skills. Installed markdown must name console commands and nothing else.

Run: python -m unittest discover -s tests
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from santoryu import skills  # noqa: E402

DRIVE_LETTER_PATH = re.compile(r"[A-Za-z]:[\\/]")
POSIX_HOME_PATH = re.compile(r"/(?:home|Users)/")
SCRIPT_INVOCATION = re.compile(r"\b(?:santoryu\.py|query_json\.py|\{\{CMD\}\})")

STALE_INSTALL = 'py "C:\\Users\\faruk\\santoryu-cursor\\santoryu.py" cursor --list-models\n'


class InstallSkillsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.claude_dir = Path(tmp.name) / ".claude"
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.claude_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_installs_every_packaged_skill(self):
        written = skills.install_skills()
        names = {p.parent.name for p in written}
        self.assertEqual({"santoryu", "query-json"}, names)
        for target in written:
            self.assertTrue(target.is_file(), f"{target} was not written")
            self.assertEqual("SKILL.md", target.name)

    def test_installed_markdown_carries_no_filesystem_path(self):
        for target in skills.install_skills():
            text = target.read_text(encoding="utf-8")
            with self.subTest(skill=target.parent.name):
                self.assertIsNone(DRIVE_LETTER_PATH.search(text), "drive-letter path leaked into skill")
                self.assertIsNone(POSIX_HOME_PATH.search(text), "home path leaked into skill")
                self.assertIsNone(SCRIPT_INVOCATION.search(text), "skill still invokes a script by filename")
                self.assertNotIn(sys.executable, text)

    def test_installed_markdown_names_the_console_commands(self):
        installed = {p.parent.name: p.read_text(encoding="utf-8") for p in skills.install_skills()}
        self.assertIn("santoryu cursor", installed["santoryu"])
        self.assertIn("santoryu install", installed["santoryu"])
        self.assertIn("query-json summary", installed["query-json"])

    def test_overwrites_a_stale_install(self):
        stale = self.claude_dir / "skills" / "santoryu" / "SKILL.md"
        stale.parent.mkdir(parents=True)
        stale.write_text(STALE_INSTALL, encoding="utf-8")

        skills.install_skills()

        self.assertNotIn("santoryu-cursor", stale.read_text(encoding="utf-8"))

    def test_is_idempotent(self):
        first = [p.read_text(encoding="utf-8") for p in skills.install_skills()]
        second = [p.read_text(encoding="utf-8") for p in skills.install_skills()]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
