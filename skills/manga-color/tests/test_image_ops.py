from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from manga_color_lib.image_ops import (  # noqa: E402
    FINAL_SIZE,
    WORK_SIZE,
    composite_line_layer,
    extract_line_layer,
    finalize_outputs,
    make_candidate_change_overlay,
    normalize_reference,
    normalize_to_canvas,
    run_deterministic_qc,
)


class ImageOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_normalize_canvas_preserves_whole_image_without_crop(self) -> None:
        source = self.root / "source.png"
        Image.new("RGB", (400, 200), (255, 0, 0)).save(source)
        output = self.root / "canvas.png"
        normalize_to_canvas(source, output)
        with Image.open(output) as image:
            self.assertEqual(image.size, WORK_SIZE)
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            nonwhite = image.convert("RGB").getbbox()
            self.assertIsNotNone(nonwhite)
            red_pixels = Image.new("L", image.size, 0)
            pixels = red_pixels.load()
            rgb = image.convert("RGB")
            for y in range(image.height):
                for x in range(image.width):
                    if rgb.getpixel((x, y)) == (255, 0, 0):
                        pixels[x, y] = 255
            self.assertEqual(red_pixels.getbbox(), (0, 736, 1152, 1312))

    def test_reference_only_downscales(self) -> None:
        source = self.root / "ref.png"
        Image.new("RGB", (3000, 1000), (10, 20, 30)).save(source)
        output = self.root / "ref-out.png"
        normalize_reference(source, output)
        with Image.open(output) as image:
            self.assertEqual(image.size, (1536, 512))

    def test_line_layer_and_composite(self) -> None:
        lineart = Image.new("RGB", WORK_SIZE, "white")
        draw = ImageDraw.Draw(lineart)
        draw.rectangle((300, 400, 850, 1600), outline="black", width=8)
        lineart_path = self.root / "lineart.png"
        lineart.save(lineart_path)
        layer_path = self.root / "line-layer.png"
        extract_line_layer(lineart_path, layer_path)
        with Image.open(layer_path) as layer:
            self.assertEqual(layer.getpixel((300, 400))[3], 255)
            self.assertEqual(layer.getpixel((10, 10))[3], 0)
        color = Image.new("RGB", WORK_SIZE, "white")
        ImageDraw.Draw(color).rectangle((304, 404, 846, 1596), fill=(220, 150, 130))
        color_path = self.root / "color.png"
        color.save(color_path)
        composite_path = self.root / "composite.png"
        composite_line_layer(color_path, layer_path, composite_path)
        with Image.open(composite_path) as composite:
            self.assertEqual(composite.getpixel((300, 400)), (0, 0, 0))
            self.assertEqual(composite.getpixel((0, 0)), (255, 255, 255))

    def test_candidate_overlay_marks_added_dark_pixels(self) -> None:
        source = Image.new("RGB", WORK_SIZE, "white")
        clean = source.copy()
        ImageDraw.Draw(clean).line((500, 500, 600, 500), fill="black", width=5)
        source_path = self.root / "source.png"
        clean_path = self.root / "clean.png"
        overlay_path = self.root / "overlay.png"
        source.save(source_path)
        clean.save(clean_path)
        metrics = make_candidate_change_overlay(source_path, clean_path, overlay_path)
        self.assertGreater(metrics["candidate_pixels"], 0)
        with Image.open(overlay_path) as overlay:
            self.assertEqual(overlay.getpixel((550, 500)), (255, 48, 48))

    def test_qc_and_finalize_outputs(self) -> None:
        lineart = Image.new("RGB", WORK_SIZE, "white")
        ImageDraw.Draw(lineart).rectangle((300, 400, 850, 1600), outline="black", width=8)
        lineart_path = self.root / "lineart.png"
        lineart.save(lineart_path)
        layer_path = self.root / "layer.png"
        extract_line_layer(lineart_path, layer_path)
        color_path = self.root / "color.png"
        color = Image.new("RGB", WORK_SIZE, "white")
        ImageDraw.Draw(color).rectangle((304, 404, 846, 1596), fill=(90, 140, 220))
        raw_path = self.root / "raw.png"
        color.save(raw_path)
        composite_line_layer(raw_path, layer_path, color_path)
        report_path = self.root / "qc.json"
        report = run_deterministic_qc(lineart_path, color_path, layer_path, report_path)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["status"], "PASS")
        outputs = finalize_outputs(lineart_path, color_path, self.root / "final", "top_bottom")
        for key in ["lineart", "colored", "comparison"]:
            self.assertIsNotNone(outputs[key])
            with Image.open(outputs[key]) as image:
                self.assertEqual(image.size, FINAL_SIZE)


if __name__ == "__main__":
    unittest.main()

