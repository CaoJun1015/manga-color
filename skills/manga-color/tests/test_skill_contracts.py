from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = SKILL_DIR.parents[1]


class SkillContractTests(unittest.TestCase):
    def test_skill_metadata_and_invocation(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        metadata = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("name: manga-color", skill)
        self.assertIn("$manga-color", skill)
        self.assertNotIn("[TODO", skill)
        self.assertIn('display_name: "Manga Color"', metadata)
        self.assertIn("allow_implicit_invocation: true", metadata)
        plugin = (PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        self.assertIn('"name": "manga-color"', plugin)
        self.assertIn('"skills": "./skills/"', plugin)

    def test_web_kit_builder_is_present(self) -> None:
        self.assertTrue((SKILL_DIR / "scripts" / "build_web_kit.py").is_file())
        self.assertTrue((SKILL_DIR / "references" / "web-portable.md").is_file())

    def test_cli_status_failure_is_machine_readable(self) -> None:
        command = [
            sys.executable,
            str(SKILL_DIR / "scripts" / "manga_color.py"),
            "status",
            "--task",
            str(SKILL_DIR / "not-a-task"),
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 3)
        self.assertIn('"status": "FAILED"', result.stdout)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
