from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from manga_color_lib.bundle import BUNDLE_NAME, BundleError, export_bundle, import_bundle  # noqa: E402
from manga_color_lib.manifest import MANIFEST_NAME, load_manifest, save_manifest  # noqa: E402


class BundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task = self.root / "task"
        self.task.mkdir()
        Image.new("RGB", (16, 16), "white").save(self.task / "01_original.png")
        save_manifest(
            self.task,
            {
                "schema_version": 2,
                "task_id": "origin",
                "status": "AWAITING_CLEAN_RESULT",
                "execution_profile": "web-light",
                "provider": "native-imagegen",
                "actual_model": "platform-selected",
                "palette_source": "reference",
                "lineart_lock": "human_visual_only",
                "artifacts": {"original": "01_original.png"},
                "hashes": {},
                "pending_edit": None,
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_round_trip_creates_new_task_and_can_change_profile(self) -> None:
        bundle = export_bundle(self.task, self.root / "task.zip")
        imported = import_bundle(bundle, self.root / "imports", "desktop-full")
        manifest = load_manifest(imported)
        self.assertNotEqual(manifest["task_id"], "origin")
        self.assertEqual(manifest["origin_task_id"], "origin")
        self.assertEqual(manifest["execution_profile"], "desktop-full")
        self.assertEqual(manifest["lineart_lock"], "deterministic_overlay")

    def test_export_rejects_sensitive_manifest_fields(self) -> None:
        manifest = load_manifest(self.task)
        manifest["api_key"] = "do-not-export"
        save_manifest(self.task, manifest)
        with self.assertRaises(BundleError):
            export_bundle(self.task, self.root / "unsafe.zip")

    def test_import_rejects_zip_slip(self) -> None:
        bundle = self.root / "malicious.zip"
        manifest_bytes = (self.task / MANIFEST_NAME).read_bytes()
        metadata = {
            "bundle_version": 1,
            "files": {MANIFEST_NAME: "bad"},
        }
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr(BUNDLE_NAME, json.dumps(metadata))
            archive.writestr(MANIFEST_NAME, manifest_bytes)
            archive.writestr("../escaped.txt", "bad")
        with self.assertRaises(BundleError):
            import_bundle(bundle, self.root / "imports", "web-light")
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_import_rejects_windows_backslash_zip_slip(self) -> None:
        bundle = self.root / "malicious-windows.zip"
        manifest_bytes = (self.task / MANIFEST_NAME).read_bytes()
        metadata = {"bundle_version": 1, "files": {MANIFEST_NAME: "bad"}}
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr(BUNDLE_NAME, json.dumps(metadata))
            archive.writestr(MANIFEST_NAME, manifest_bytes)
            archive.writestr("..\\escaped.txt", "bad")
        with self.assertRaises(BundleError):
            import_bundle(bundle, self.root / "imports", "web-light")

    def test_import_rejects_checksum_tampering(self) -> None:
        bundle = export_bundle(self.task, self.root / "task.zip")
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(bundle, "r") as source, zipfile.ZipFile(tampered, "w") as destination:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "01_original.png":
                    data += b"tampered"
                destination.writestr(info, data)
        with self.assertRaises(BundleError):
            import_bundle(tampered, self.root / "imports", "web-light")


if __name__ == "__main__":
    unittest.main()
