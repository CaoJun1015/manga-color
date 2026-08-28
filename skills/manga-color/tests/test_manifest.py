from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from manga_color_lib.manifest import MANIFEST_NAME, load_manifest, save_manifest  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_v1_is_upgraded_on_next_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            legacy = {
                "schema_version": 1,
                "task_id": "legacy",
                "status": "REVIEW_LINEART",
                "provider": "openai",
                "model": "gpt-image-2",
                "reference_files": ["02_color_reference_01.png"],
                "lineart_approved_at": None,
            }
            (task / MANIFEST_NAME).write_text(json.dumps(legacy), encoding="utf-8")
            upgraded = load_manifest(task)
            self.assertEqual(upgraded["schema_version"], 2)
            self.assertEqual(upgraded["execution_profile"], "desktop-full")
            self.assertEqual(upgraded["palette_source"], "reference")
            save_manifest(task, upgraded)
            persisted = json.loads((task / MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], 2)
            self.assertEqual(persisted["actual_model"], "gpt-image-2")


if __name__ == "__main__":
    unittest.main()
