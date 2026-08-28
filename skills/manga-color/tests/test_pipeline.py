from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from manga_color_lib.image_ops import FINAL_SIZE, WORK_SIZE, save_png_atomic  # noqa: E402
from manga_color_lib.manifest import load_manifest  # noqa: E402
from manga_color_lib.models import ImageEditRequest, ImageResult  # noqa: E402
from manga_color_lib.pipeline import MangaColorPipeline, PipelineError  # noqa: E402
from manga_color_lib.providers import OpenAIImageProvider  # noqa: E402


def make_lineart(path: Path, size: tuple[int, int] = WORK_SIZE) -> None:
    canvas = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((330, 250, 820, 730), outline="black", width=9)
    draw.rectangle((390, 720, 760, 1700), outline="black", width=9)
    save_png_atomic(canvas, path)


def make_color(path: Path, size: tuple[int, int] = WORK_SIZE) -> None:
    canvas = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((338, 258, 812, 722), fill=(235, 190, 165))
    draw.rectangle((398, 728, 752, 1692), fill=(70, 120, 210))
    save_png_atomic(canvas, path)


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[ImageEditRequest] = []

    def edit_image(self, request: ImageEditRequest) -> ImageResult:
        self.calls.append(request)
        (make_lineart if len(request.images) == 1 else make_color)(request.output_path)
        return ImageResult(request.output_path, self.name, request.model, request_id=f"fake-{len(self.calls)}")


class WrongSizeProvider(FakeProvider):
    def edit_image(self, request: ImageEditRequest) -> ImageResult:
        self.calls.append(request)
        save_png_atomic(Image.new("RGB", (64, 64), "white"), request.output_path)
        return ImageResult(request.output_path, self.name, request.model)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        source = Image.new("RGB", (600, 800), "white")
        ImageDraw.Draw(source).rectangle((100, 100, 500, 700), outline="black", width=5)
        source.save(self.source)
        self.reference = self.root / "reference.png"
        Image.new("RGB", (400, 700), (70, 120, 210)).save(self.reference)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def start_api_task(self) -> tuple[MangaColorPipeline, Path, dict]:
        provider = FakeProvider()
        pipeline = MangaColorPipeline(provider)
        result = pipeline.start(source=self.source, references=[self.reference], output_root=self.root / "api-jobs", task_name="hero", provider_name="openai")
        return pipeline, Path(result["task_dir"]), result

    def start_native_task(self, profile: str = "desktop-full") -> tuple[MangaColorPipeline, Path, dict]:
        pipeline = MangaColorPipeline()
        result = pipeline.start(source=self.source, references=[self.reference], output_root=self.root / "native-jobs", task_name="hero", profile=profile)
        return pipeline, Path(result["task_dir"]), result

    def test_openai_end_to_end_and_full_line_lock(self) -> None:
        pipeline, task_dir, started = self.start_api_task()
        self.assertEqual(started["status"], "REVIEW_LINEART")
        self.assertEqual(started["lineart_lock"], "deterministic_overlay")
        colored = pipeline.approve_lineart(task_dir)
        self.assertEqual(colored["status"], "REVIEW_QC")
        self.assertEqual(colored["qc_status"], "PASS")
        finished = pipeline.finalize(task_dir)
        self.assertEqual(finished["status"], "COMPLETED")
        for key in ["final_lineart", "final_colored"]:
            with Image.open(finished["artifacts"][key]) as image:
                self.assertEqual(image.size, FINAL_SIZE)
        manifest = load_manifest(task_dir)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["actual_model"], "gpt-image-2")
        self.assertNotIn("api_key", json.dumps(manifest).lower())

    def test_native_handoff_has_two_manual_gates(self) -> None:
        pipeline, task_dir, started = self.start_native_task()
        self.assertEqual(started["status"], "AWAITING_CLEAN_RESULT")
        self.assertEqual(started["actual_model"], "platform-selected")
        self.assertEqual(started["pending_edit"]["stage"], "clean")
        lineart = self.root / "native-line.png"
        make_lineart(lineart)
        reviewed = pipeline.submit_result(task_dir, "clean", lineart)
        self.assertEqual(reviewed["status"], "REVIEW_LINEART")
        color_request = pipeline.approve_lineart(task_dir)
        self.assertEqual(color_request["status"], "AWAITING_COLOR_RESULT")
        color = self.root / "native-color.png"
        make_color(color)
        reviewed_color = pipeline.submit_result(task_dir, "color", color)
        self.assertEqual(reviewed_color["status"], "REVIEW_QC")
        self.assertEqual(reviewed_color["qc_status"], "PASS")
        self.assertEqual(pipeline.finalize(task_dir)["status"], "COMPLETED")

    def test_web_light_discloses_limit_and_requires_human_approval(self) -> None:
        pipeline, task_dir, started = self.start_native_task("web-light")
        lineart = self.root / "web-line.png"
        color = self.root / "web-color.png"
        make_lineart(lineart)
        make_color(color)
        pipeline.submit_result(task_dir, "clean", lineart)
        pipeline.approve_lineart(task_dir)
        reviewed = pipeline.submit_result(task_dir, "color", color)
        self.assertEqual(reviewed["lineart_lock"], "human_visual_only")
        self.assertEqual(reviewed["qc_status"], "LIGHT_PASS")
        with self.assertRaises(PipelineError) as context:
            pipeline.finalize(task_dir)
        self.assertEqual(context.exception.code, "human_review_required")
        complete = pipeline.finalize(task_dir, human_approved=True)
        self.assertEqual(complete["qc_status"], "HUMAN_REVIEW_ONLY")

    def test_native_rejects_model_name_and_does_not_need_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            _, _, result = self.start_native_task()
        self.assertEqual(result["provider"], "native-imagegen")
        with self.assertRaises(PipelineError) as context:
            MangaColorPipeline().start(source=self.source, references=[self.reference], output_root=self.root / "bad", model="gpt-image-2")
        self.assertEqual(context.exception.code, "native_model_not_selectable")

    def test_openai_missing_key_leaves_resumable_task(self) -> None:
        pipeline = MangaColorPipeline(OpenAIImageProvider())
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PipelineError) as context:
                pipeline.start(source=self.source, references=[self.reference], output_root=self.root / "missing-key", provider_name="openai")
        self.assertEqual(context.exception.code, "missing_api_key")
        self.assertEqual(load_manifest(context.exception.task_dir)["status"], "NEEDS_INPUT")

    def test_requires_reference_without_explicit_inference(self) -> None:
        with self.assertRaises(PipelineError) as context:
            MangaColorPipeline().start(source=self.source, references=[], output_root=self.root / "jobs")
        self.assertEqual(context.exception.code, "missing_references")

    def test_inferred_palette_is_recorded(self) -> None:
        result = MangaColorPipeline().start(source=self.source, references=[], output_root=self.root / "jobs", allow_inferred_palette=True, character_hint="confirmed version")
        self.assertEqual(result["palette_source"], "inferred")
        pipeline = MangaColorPipeline()
        task_dir = Path(result["task_dir"])
        lineart = self.root / "inferred-line.png"
        make_lineart(lineart)
        pipeline.submit_result(task_dir, "clean", lineart)
        color_request = pipeline.approve_lineart(task_dir)
        self.assertIn("explicitly allowed", color_request["pending_edit"]["prompt"])

    def test_detects_modified_lineart_before_approval(self) -> None:
        pipeline, task_dir, _ = self.start_native_task()
        lineart = self.root / "line.png"
        make_lineart(lineart)
        pipeline.submit_result(task_dir, "clean", lineart)
        Image.new("RGB", WORK_SIZE, "black").save(task_dir / "04_lineart_work.png")
        with self.assertRaises(PipelineError) as context:
            pipeline.approve_lineart(task_dir)
        self.assertEqual(context.exception.code, "integrity_mismatch")

    def test_native_clean_retry_limit_is_two(self) -> None:
        pipeline, task_dir, _ = self.start_native_task()
        lineart = self.root / "line.png"
        make_lineart(lineart)
        for index in range(2):
            pipeline.submit_result(task_dir, "clean", lineart)
            pipeline.retry_clean(task_dir, f"retry {index}")
        pipeline.submit_result(task_dir, "clean", lineart)
        with self.assertRaises(PipelineError) as context:
            pipeline.retry_clean(task_dir, "third")
        self.assertEqual(context.exception.code, "retry_limit")

    def test_openai_wrong_size_records_failed_task(self) -> None:
        pipeline = MangaColorPipeline(WrongSizeProvider())
        with self.assertRaises(PipelineError) as context:
            pipeline.start(source=self.source, references=[self.reference], output_root=self.root / "wrong-size", provider_name="openai")
        self.assertEqual(load_manifest(context.exception.task_dir)["errors"][-1]["stage"], "clean_validation")


if __name__ == "__main__":
    unittest.main()
