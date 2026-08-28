from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat, UnidentifiedImageError


WORK_SIZE = (1152, 2048)
FINAL_SIZE = (1080, 1920)
WHITE = (255, 255, 255)


class ImageValidationError(ValueError):
    pass


def load_image(path: Path) -> Image.Image:
    try:
        with Image.open(path) as opened:
            opened.load()
            return ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ImageValidationError(f"Unreadable image: {path}") from exc


def save_png_atomic(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    image.save(temp_path, format="PNG", optimize=False)
    temp_path.replace(path)


def normalize_to_canvas(source: Path, destination: Path, size: tuple[int, int] = WORK_SIZE) -> None:
    image = load_image(source)
    contained = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, WHITE)
    position = ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2)
    canvas.paste(contained, position)
    save_png_atomic(canvas, destination)


def normalize_reference(source: Path, destination: Path, max_edge: int = 1536) -> None:
    image = load_image(source)
    if max(image.size) > max_edge:
        scale = max_edge / max(image.size)
        target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(target, Image.Resampling.LANCZOS)
    save_png_atomic(image, destination)


def require_exact_size(path: Path, expected: tuple[int, int] = WORK_SIZE) -> None:
    image = load_image(path)
    if image.size != expected:
        raise ImageValidationError(
            f"Unexpected image size for {path}: {image.width}x{image.height}; "
            f"expected {expected[0]}x{expected[1]}"
        )


def normalize_generated_result(
    source: Path, destination: Path, size: tuple[int, int] = WORK_SIZE
) -> None:
    """Normalize a platform-generated image without cropping it."""
    normalize_to_canvas(source, destination, size)


def extract_line_layer(lineart_path: Path, destination: Path) -> None:
    lineart = load_image(lineart_path)
    luminance = ImageOps.grayscale(lineart)
    alpha = ImageOps.invert(luminance)
    layer = Image.new("RGBA", lineart.size, (0, 0, 0, 0))
    layer.putalpha(alpha)
    save_png_atomic(layer, destination)


def make_candidate_change_overlay(
    source_canvas_path: Path, lineart_path: Path, destination: Path
) -> dict[str, Any]:
    source = ImageOps.grayscale(load_image(source_canvas_path))
    lineart = ImageOps.grayscale(load_image(lineart_path))
    if source.size != lineart.size:
        raise ImageValidationError("Source canvas and line art are not aligned")
    added_darkness = ImageChops.subtract(source, lineart)
    candidate_mask = added_darkness.point(lambda value: 255 if value >= 28 else 0)
    base = lineart.convert("RGB")
    red = Image.new("RGB", base.size, (255, 48, 48))
    overlay = Image.composite(red, base, candidate_mask)
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, 760, 46), fill=(255, 255, 255))
    draw.text((12, 12), "Red = candidate added/shifted dark pixels; manual review required", fill=(180, 0, 0))
    save_png_atomic(overlay, destination)
    histogram = candidate_mask.histogram()
    candidate_pixels = sum(histogram[1:])
    return {
        "candidate_pixels": candidate_pixels,
        "candidate_ratio": round(candidate_pixels / (base.width * base.height), 6),
        "advisory_only": True,
    }


def _whiten_near_white_background(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    red_mask = red.point(lambda value: 255 if value >= 250 else 0)
    green_mask = green.point(lambda value: 255 if value >= 250 else 0)
    blue_mask = blue.point(lambda value: 255 if value >= 250 else 0)
    neutral_white = ImageChops.multiply(ImageChops.multiply(red_mask, green_mask), blue_mask)
    result = rgb.copy()
    result.paste(WHITE, mask=neutral_white)
    return result


def composite_line_layer(color_path: Path, line_layer_path: Path, destination: Path) -> None:
    color = _whiten_near_white_background(load_image(color_path)).convert("RGBA")
    with Image.open(line_layer_path) as opened:
        line_layer = opened.convert("RGBA")
    if color.size != line_layer.size:
        raise ImageValidationError("Color output and immutable line layer are not aligned")
    composite = Image.alpha_composite(color, line_layer).convert("RGB")
    save_png_atomic(composite, destination)


def _corner_is_white(image: Image.Image, corner: tuple[int, int], patch: int = 12) -> bool:
    x, y = corner
    left = 0 if x == 0 else image.width - patch
    top = 0 if y == 0 else image.height - patch
    crop = image.crop((left, top, left + patch, top + patch)).convert("RGB")
    extrema = crop.getextrema()
    return all(low == 255 and high == 255 for low, high in extrema)


def run_deterministic_qc(
    lineart_path: Path,
    color_path: Path,
    line_layer_path: Path,
    report_path: Path,
    expected_size: tuple[int, int] = WORK_SIZE,
) -> dict[str, Any]:
    lineart = load_image(lineart_path)
    color = load_image(color_path)
    with Image.open(line_layer_path) as opened:
        line_layer = opened.convert("RGBA")
    checks: dict[str, Any] = {
        "lineart_size": list(lineart.size),
        "color_size": list(color.size),
        "expected_size": list(expected_size),
        "line_layer_size": list(line_layer.size),
        "opaque_output": True,
    }
    errors: list[str] = []
    if lineart.size != expected_size:
        errors.append("lineart_size_mismatch")
    if color.size != expected_size:
        errors.append("color_size_mismatch")
    if line_layer.size != expected_size:
        errors.append("line_layer_size_mismatch")
    corners = [(0, 0), (1, 0), (0, 1), (1, 1)]
    checks["lineart_white_corners"] = all(_corner_is_white(lineart, item) for item in corners)
    checks["color_white_corners"] = all(_corner_is_white(color, item) for item in corners)
    if not checks["lineart_white_corners"]:
        errors.append("lineart_background_not_pure_white_at_corners")
    if not checks["color_white_corners"]:
        errors.append("color_background_not_pure_white_at_corners")
    line_alpha = line_layer.getchannel("A")
    strong_lines = line_alpha.point(lambda value: 255 if value >= 128 else 0)
    if strong_lines.getbbox():
        line_stats = ImageStat.Stat(ImageOps.grayscale(color), mask=strong_lines)
        checks["mean_luminance_on_strong_lines"] = round(line_stats.mean[0], 3)
        if line_stats.mean[0] > 40:
            errors.append("immutable_lines_not_visibly_dark")
    else:
        errors.append("line_layer_empty")
    report = {"status": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = report_path.with_name(f".{report_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(report_path)
    return report


def run_light_qc(
    lineart_path: Path,
    color_path: Path,
    report_path: Path,
    expected_size: tuple[int, int] = WORK_SIZE,
) -> dict[str, Any]:
    """Run only portable checks; this does not claim line-structure identity."""
    lineart = load_image(lineart_path)
    color = load_image(color_path)
    corners = [(0, 0), (1, 0), (0, 1), (1, 1)]
    checks: dict[str, Any] = {
        "lineart_size": list(lineart.size),
        "color_size": list(color.size),
        "expected_size": list(expected_size),
        "lineart_white_corners": all(_corner_is_white(lineart, item) for item in corners),
        "color_white_corners": all(_corner_is_white(color, item) for item in corners),
        "opaque_png_after_normalization": True,
        "line_structure_compared": False,
    }
    errors: list[str] = []
    if lineart.size != expected_size:
        errors.append("lineart_size_mismatch")
    if color.size != expected_size:
        errors.append("color_size_mismatch")
    if not checks["lineart_white_corners"]:
        errors.append("lineart_background_not_pure_white_at_corners")
    if not checks["color_white_corners"]:
        errors.append("color_background_not_pure_white_at_corners")
    report = {
        "status": "LIGHT_PASS" if not errors else "FAIL",
        "checks": checks,
        "errors": errors,
        "limitations": [
            "No deterministic immutable-line overlay was applied.",
            "Pixel-level line structure identity was not verified.",
            "Human visual review is required before finalization.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = report_path.with_name(f".{report_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(report_path)
    return report


def _fit_to_panel(image: Image.Image, panel_size: tuple[int, int]) -> Image.Image:
    contained = ImageOps.contain(image, panel_size, Image.Resampling.LANCZOS)
    panel = Image.new("RGB", panel_size, WHITE)
    panel.paste(contained, ((panel_size[0] - contained.width) // 2, (panel_size[1] - contained.height) // 2))
    return panel


def finalize_outputs(
    lineart_work: Path,
    color_work: Path,
    final_dir: Path,
    comparison_layout: str = "none",
) -> dict[str, str | None]:
    final_dir.mkdir(parents=True, exist_ok=True)
    lineart = load_image(lineart_work).resize(FINAL_SIZE, Image.Resampling.LANCZOS)
    color = load_image(color_work).resize(FINAL_SIZE, Image.Resampling.LANCZOS)
    lineart_path = final_dir / "lineart_1080x1920.png"
    color_path = final_dir / "colored_1080x1920.png"
    save_png_atomic(lineart, lineart_path)
    save_png_atomic(color, color_path)
    comparison_path: Path | None = None
    if comparison_layout == "side_by_side":
        left = _fit_to_panel(lineart, (FINAL_SIZE[0] // 2, FINAL_SIZE[1]))
        right = _fit_to_panel(color, (FINAL_SIZE[0] - FINAL_SIZE[0] // 2, FINAL_SIZE[1]))
        comparison = Image.new("RGB", FINAL_SIZE, WHITE)
        comparison.paste(left, (0, 0))
        comparison.paste(right, (left.width, 0))
        comparison_path = final_dir / "comparison_1080x1920.png"
        save_png_atomic(comparison, comparison_path)
    elif comparison_layout == "top_bottom":
        top = _fit_to_panel(lineart, (FINAL_SIZE[0], FINAL_SIZE[1] // 2))
        bottom = _fit_to_panel(color, (FINAL_SIZE[0], FINAL_SIZE[1] - FINAL_SIZE[1] // 2))
        comparison = Image.new("RGB", FINAL_SIZE, WHITE)
        comparison.paste(top, (0, 0))
        comparison.paste(bottom, (0, top.height))
        comparison_path = final_dir / "comparison_1080x1920.png"
        save_png_atomic(comparison, comparison_path)
    elif comparison_layout != "none":
        raise ValueError(f"Unsupported comparison layout: {comparison_layout}")
    return {
        "lineart": str(lineart_path),
        "colored": str(color_path),
        "comparison": str(comparison_path) if comparison_path else None,
    }
