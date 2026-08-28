from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .bundle import BundleError, export_bundle, import_bundle
from .image_ops import (
    WORK_SIZE,
    composite_line_layer,
    extract_line_layer,
    finalize_outputs,
    load_image,
    make_candidate_change_overlay,
    normalize_generated_result,
    normalize_reference,
    normalize_to_canvas,
    require_exact_size,
    run_deterministic_qc,
    run_light_qc,
    save_png_atomic,
)
from .manifest import load_manifest, record_hash, save_manifest, utc_now, verify_hashes
from .models import ImageEditRequest, ImageProvider, ProviderError
from .prompts import build_clean_prompt, build_color_prompt


PROVIDERS = {"native", "openai"}
PROFILES = {"desktop-full", "web-light"}


class PipelineError(RuntimeError):
    def __init__(self, message: str, *, code: str = "pipeline_error", status: str = "FAILED", task_dir: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.task_dir = task_dir

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "status": self.status, "task_dir": str(self.task_dir) if self.task_dir else None, "error": {"code": self.code, "message": str(self)}, "next_action": "resolve_error"}


class MangaColorPipeline:
    WORK_SIZE_TEXT = "1152x2048"
    FINAL_SIZE_TEXT = "1080x1920"

    def __init__(self, provider: ImageProvider | None = None) -> None:
        self.provider = provider

    @staticmethod
    def _safe_task_name(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
        return cleaned[:48] or "manga"

    def _new_task_dir(self, output_root: Path, task_name: str) -> tuple[str, Path]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_id = f"{timestamp}_{secrets.token_hex(3)}_{self._safe_task_name(task_name)}"
        task_dir = output_root.expanduser().resolve() / task_id
        (task_dir / "final").mkdir(parents=True, exist_ok=False)
        (task_dir / "qc").mkdir(parents=True, exist_ok=True)
        return task_id, task_dir

    @staticmethod
    def _copy_as_png(source: Path, destination: Path) -> None:
        save_png_atomic(load_image(source), destination)

    @staticmethod
    def _absolute_mapping(task_dir: Path, values: dict[str, Any]) -> dict[str, Any]:
        return {key: str((task_dir / value).resolve()) if isinstance(value, str) and value else value for key, value in values.items()}

    def _summary(self, task_dir: Path, manifest: dict[str, Any], message: str) -> dict[str, Any]:
        next_actions = {
            "NEEDS_INPUT": "configure_environment_or_supply_input",
            "AWAITING_CLEAN_RESULT": "run_native_imagegen_then_submit_clean_result",
            "REVIEW_LINEART": "review_lineart_then_approve_or_retry",
            "AWAITING_COLOR_RESULT": "run_native_imagegen_then_submit_color_result",
            "REVIEW_QC": "visually_review_then_finalize_or_retry_color",
            "COMPLETED": "deliver_outputs",
            "FAILED": "inspect_error",
        }
        pending = manifest.get("pending_edit")
        if isinstance(pending, dict):
            pending = dict(pending)
            pending["images"] = [str((task_dir / item).resolve()) for item in pending.get("images", [])]
        return {
            "ok": manifest.get("status") not in {"FAILED", "NEEDS_INPUT"},
            "status": manifest.get("status"),
            "task_dir": str(task_dir.resolve()),
            "execution_profile": manifest.get("execution_profile"),
            "provider": manifest.get("provider"),
            "actual_model": manifest.get("actual_model"),
            "palette_source": manifest.get("palette_source"),
            "lineart_lock": manifest.get("lineart_lock"),
            "artifacts": self._absolute_mapping(task_dir, manifest.get("artifacts", {})),
            "outputs": self._absolute_mapping(task_dir, manifest.get("outputs", {})),
            "pending_edit": pending,
            "qc_status": manifest.get("qc_status"),
            "next_action": next_actions.get(manifest.get("status"), "inspect_status"),
            "message": message,
        }

    def _get_openai_provider(self) -> ImageProvider:
        if self.provider is not None:
            return self.provider
        from .providers import OpenAIImageProvider
        self.provider = OpenAIImageProvider()
        return self.provider

    @staticmethod
    def _pending_edit(stage: str, images: list[str], prompt: str) -> dict[str, Any]:
        return {"stage": stage, "images": images, "prompt": prompt, "size": "1152x2048", "quality": "high", "output_format": "png"}

    def _clean_prompt(self, manifest: dict[str, Any], feedback: str = "") -> str:
        return build_clean_prompt(manifest.get("character_hint", ""), feedback)

    def _color_prompt(self, manifest: dict[str, Any], feedback: str = "") -> str:
        return build_color_prompt(manifest.get("palette_notes", ""), feedback, allow_inferred_palette=manifest.get("palette_source") == "inferred", character_hint=manifest.get("character_hint", ""))

    def _set_native_request(self, task_dir: Path, manifest: dict[str, Any], stage: str, feedback: str = "") -> None:
        if stage == "clean":
            images, prompt = ["03_work_canvas_1152x2048.png"], self._clean_prompt(manifest, feedback)
            manifest["status"] = "AWAITING_CLEAN_RESULT"
        else:
            images, prompt = ["04_lineart_work.png", *manifest.get("reference_files", [])], self._color_prompt(manifest, feedback)
            manifest["status"] = "AWAITING_COLOR_RESULT"
        manifest["pending_edit"] = self._pending_edit(stage, images, prompt)
        save_manifest(task_dir, manifest)

    def _record_provider_result(self, manifest: dict[str, Any], stage: str, result: Any) -> None:
        payload = result.to_dict()
        payload["output_path"] = Path(payload["output_path"]).name
        manifest.setdefault("requests", []).append({"stage": stage, **payload})
        manifest["actual_model"] = result.model or manifest.get("actual_model")

    def _provider_failure(self, task_dir: Path, manifest: dict[str, Any], stage: str, error: ProviderError) -> PipelineError:
        status = "NEEDS_INPUT" if error.code in {"missing_api_key", "missing_dependency"} else "FAILED"
        manifest["status"] = status
        manifest.setdefault("errors", []).append({"at": utc_now(), "stage": stage, "code": error.code, "message": str(error), "retryable": error.retryable})
        save_manifest(task_dir, manifest)
        return PipelineError(str(error), code=error.code, status=status, task_dir=task_dir)

    def _stage_failure(self, task_dir: Path, manifest: dict[str, Any], stage: str, error: Exception) -> PipelineError:
        manifest["status"] = "FAILED"
        manifest.setdefault("errors", []).append({"at": utc_now(), "stage": stage, "code": error.__class__.__name__, "message": str(error), "retryable": False})
        save_manifest(task_dir, manifest)
        return PipelineError(str(error), code=error.__class__.__name__, status="FAILED", task_dir=task_dir)

    def _accept_clean_result(self, task_dir: Path, manifest: dict[str, Any], supplied: Path, *, model: str, native: bool = True) -> None:
        output = task_dir / "04_lineart_work.png"
        normalize_generated_result(supplied, output)
        require_exact_size(output, WORK_SIZE)
        overlay = task_dir / "qc" / "candidate_change_overlay.png"
        metrics = make_candidate_change_overlay(task_dir / "03_work_canvas_1152x2048.png", output, overlay)
        manifest["candidate_change_metrics"] = metrics
        manifest["artifacts"].update({"lineart_work": "04_lineart_work.png", "candidate_change_overlay": "qc/candidate_change_overlay.png"})
        record_hash(manifest, task_dir, "04_lineart_work.png")
        record_hash(manifest, task_dir, "qc/candidate_change_overlay.png")
        if native:
            manifest.setdefault("requests", []).append({"stage": "clean", "provider": "native-imagegen", "model": model, "output_path": "04_lineart_work.png", "request_id": None, "elapsed_seconds": 0.0, "attempts": 1, "usage": {}})
        manifest["actual_model"] = model
        manifest["pending_edit"] = None
        manifest["status"] = "REVIEW_LINEART"
        manifest.setdefault("review", {})["lineart_approved_at"] = None
        save_manifest(task_dir, manifest)

    def _process_color_result(self, task_dir: Path, manifest: dict[str, Any], supplied: Path, *, model: str, native: bool) -> None:
        raw = task_dir / "07_color_raw.png"
        normalize_generated_result(supplied, raw)
        require_exact_size(raw, WORK_SIZE)
        composed = task_dir / "08_color_composited_work.png"
        report_path = task_dir / "qc" / "qc_report.json"
        if manifest["execution_profile"] == "desktop-full":
            line_layer = task_dir / "05_line_layer.png"
            if not line_layer.is_file():
                extract_line_layer(task_dir / "04_lineart_work.png", line_layer)
                manifest["artifacts"]["line_layer"] = "05_line_layer.png"
                record_hash(manifest, task_dir, "05_line_layer.png")
            composite_line_layer(raw, line_layer, composed)
            report = run_deterministic_qc(task_dir / "04_lineart_work.png", composed, line_layer, report_path)
        else:
            shutil.copyfile(raw, composed)
            report = run_light_qc(task_dir / "04_lineart_work.png", composed, report_path)
        manifest["artifacts"].update({"color_raw": "07_color_raw.png", "color_composited_work": "08_color_composited_work.png", "qc_report": "qc/qc_report.json"})
        for relative in ["07_color_raw.png", "08_color_composited_work.png", "qc/qc_report.json"]:
            record_hash(manifest, task_dir, relative)
        if native:
            manifest.setdefault("requests", []).append({"stage": "color", "provider": "native-imagegen", "model": model, "output_path": "07_color_raw.png", "request_id": None, "elapsed_seconds": 0.0, "attempts": 1, "usage": {}})
            manifest["actual_model"] = model
        manifest["pending_edit"] = None
        manifest["qc_status"] = report["status"]
        manifest["status"] = "REVIEW_QC"
        save_manifest(task_dir, manifest)

    def _run_api_stage(self, task_dir: Path, manifest: dict[str, Any], stage: str, feedback: str = "") -> None:
        if stage == "clean":
            images, output, prompt = (task_dir / "03_work_canvas_1152x2048.png",), task_dir / "api_clean_result.png", self._clean_prompt(manifest, feedback)
        else:
            images = (task_dir / "04_lineart_work.png", *(task_dir / value for value in manifest.get("reference_files", [])))
            output, prompt = task_dir / "api_color_result.png", self._color_prompt(manifest, feedback)
        manifest["status"] = "CLEANING" if stage == "clean" else "COLORING"
        save_manifest(task_dir, manifest)
        request = ImageEditRequest(images=tuple(images), prompt=prompt, output_path=output, model=manifest["requested_model"])
        try:
            result = self._get_openai_provider().edit_image(request)
        except ProviderError as exc:
            raise self._provider_failure(task_dir, manifest, stage, exc) from exc
        try:
            self._record_provider_result(manifest, stage, result)
            require_exact_size(output, WORK_SIZE)
            if stage == "clean":
                self._accept_clean_result(task_dir, manifest, output, model=result.model, native=False)
            else:
                self._process_color_result(task_dir, manifest, output, model=result.model, native=False)
            output.unlink(missing_ok=True)
        except Exception as exc:
            raise self._stage_failure(task_dir, manifest, f"{stage}_validation", exc) from exc

    def start(self, *, source: Path, references: list[Path], output_root: Path, task_name: str = "manga", character_hint: str = "", palette_notes: str = "", allow_inferred_palette: bool = False, provider_name: str = "native", profile: str = "desktop-full", model: str | None = None) -> dict[str, Any]:
        if provider_name not in PROVIDERS:
            raise PipelineError(f"Unsupported provider: {provider_name}", code="unsupported_provider", status="NEEDS_INPUT")
        if profile not in PROFILES:
            raise PipelineError(f"Unsupported profile: {profile}", code="unsupported_profile", status="NEEDS_INPUT")
        if provider_name == "native" and model:
            raise PipelineError("Native ImageGen uses the platform-selected model; --model is not accepted.", code="native_model_not_selectable", status="NEEDS_INPUT")
        source = source.expanduser().resolve()
        references = [item.expanduser().resolve() for item in references]
        if not source.is_file():
            raise PipelineError(f"Source image not found: {source}", code="missing_source", status="NEEDS_INPUT")
        if not references and not allow_inferred_palette:
            raise PipelineError("At least one color reference is required unless inferred palette is explicitly allowed.", code="missing_references", status="NEEDS_INPUT")
        if len(references) > 3:
            raise PipelineError("A maximum of three color references is supported", code="too_many_references", status="NEEDS_INPUT")
        for reference in references:
            if not reference.is_file():
                raise PipelineError(f"Reference image not found: {reference}", code="missing_reference", status="NEEDS_INPUT")
        try:
            load_image(source)
            for reference in references:
                load_image(reference)
        except Exception as exc:
            raise PipelineError(str(exc), code="unreadable_image", status="NEEDS_INPUT") from exc
        task_id, task_dir = self._new_task_dir(output_root, task_name)
        requested_model = (model or "gpt-image-2") if provider_name == "openai" else None
        manifest: dict[str, Any] = {
            "schema_version": 2, "task_id": task_id, "status": "VALIDATING", "created_at": utc_now(),
            "execution_profile": profile, "provider": "native-imagegen" if provider_name == "native" else "openai",
            "requested_model": requested_model, "actual_model": "platform-selected" if provider_name == "native" else None,
            "work_size": self.WORK_SIZE_TEXT, "final_size": self.FINAL_SIZE_TEXT, "character_hint": character_hint,
            "palette_notes": palette_notes, "palette_source": "reference" if references else "inferred",
            "lineart_lock": "deterministic_overlay" if profile == "desktop-full" else "human_visual_only",
            "retry_counts": {"clean": 0, "color": 0}, "reference_files": [], "artifacts": {}, "outputs": {},
            "hashes": {}, "requests": [], "errors": [], "pending_edit": None, "review": {}, "qc_status": None,
        }
        save_manifest(task_dir, manifest)
        original = task_dir / "01_original.png"
        self._copy_as_png(source, original)
        manifest["artifacts"]["original"] = "01_original.png"
        record_hash(manifest, task_dir, "01_original.png")
        for index, reference in enumerate(references, start=1):
            relative = f"02_color_reference_{index:02d}.png"
            normalize_reference(reference, task_dir / relative)
            manifest["reference_files"].append(relative)
            manifest["artifacts"][f"color_reference_{index:02d}"] = relative
            record_hash(manifest, task_dir, relative)
        work = task_dir / "03_work_canvas_1152x2048.png"
        normalize_to_canvas(original, work)
        manifest["artifacts"]["work_canvas"] = "03_work_canvas_1152x2048.png"
        record_hash(manifest, task_dir, "03_work_canvas_1152x2048.png")
        save_manifest(task_dir, manifest)
        if provider_name == "native":
            self._set_native_request(task_dir, manifest, "clean")
            return self._summary(task_dir, manifest, "Clean-line-art ImageGen request is ready for the signed-in platform.")
        self._run_api_stage(task_dir, manifest, "clean")
        return self._summary(task_dir, manifest, "Line art is ready for mandatory manual review.")

    def _load_and_verify(self, task_dir: Path, required: list[str]) -> dict[str, Any]:
        task_dir = task_dir.expanduser().resolve()
        manifest = load_manifest(task_dir)
        mismatches = verify_hashes(manifest, task_dir, required)
        if mismatches:
            raise PipelineError(f"Task files changed since the previous stage: {', '.join(mismatches)}", code="integrity_mismatch", status="NEEDS_INPUT", task_dir=task_dir)
        return manifest

    def submit_result(self, task_dir: Path, stage: str, image: Path, actual_model: str | None = None) -> dict[str, Any]:
        task_dir, image = task_dir.expanduser().resolve(), image.expanduser().resolve()
        if stage not in {"clean", "color"}:
            raise PipelineError("submit-result stage must be clean or color", code="invalid_stage", status="NEEDS_INPUT")
        if not image.is_file():
            raise PipelineError(f"Generated image not found: {image}", code="missing_result", status="NEEDS_INPUT", task_dir=task_dir)
        manifest = load_manifest(task_dir)
        if manifest.get("provider") != "native-imagegen":
            raise PipelineError("submit-result is only valid for native ImageGen tasks", code="invalid_provider", task_dir=task_dir)
        expected = "AWAITING_CLEAN_RESULT" if stage == "clean" else "AWAITING_COLOR_RESULT"
        if manifest.get("status") != expected or (manifest.get("pending_edit") or {}).get("stage") != stage:
            raise PipelineError(f"submit-result {stage} requires {expected}, got {manifest.get('status')}", code="invalid_state", task_dir=task_dir)
        model = actual_model or "platform-selected"
        try:
            if stage == "clean":
                self._accept_clean_result(task_dir, manifest, image, model=model)
                message = "Line art is ready. Review the original, line art, and advisory change overlay."
            else:
                self._process_color_result(task_dir, manifest, image, model=model, native=True)
                message = "Color result is ready for mandatory human review."
        except Exception as exc:
            raise self._stage_failure(task_dir, manifest, f"native_{stage}_validation", exc) from exc
        return self._summary(task_dir, manifest, message)

    def approve_lineart(self, task_dir: Path) -> dict[str, Any]:
        task_dir = task_dir.expanduser().resolve()
        current = load_manifest(task_dir)
        manifest = self._load_and_verify(task_dir, ["01_original.png", "03_work_canvas_1152x2048.png", "04_lineart_work.png", *current.get("reference_files", [])])
        if manifest.get("status") != "REVIEW_LINEART":
            raise PipelineError(f"approve-lineart requires REVIEW_LINEART, got {manifest.get('status')}", code="invalid_state", task_dir=task_dir)
        line_layer = task_dir / "05_line_layer.png"
        extract_line_layer(task_dir / "04_lineart_work.png", line_layer)
        manifest["artifacts"]["line_layer"] = "05_line_layer.png"
        record_hash(manifest, task_dir, "05_line_layer.png")
        manifest.setdefault("review", {})["lineart_approved_at"] = utc_now()
        save_manifest(task_dir, manifest)
        if manifest.get("provider") == "native-imagegen":
            self._set_native_request(task_dir, manifest, "color")
            return self._summary(task_dir, manifest, "Line art approved; native color ImageGen request is ready.")
        self._run_api_stage(task_dir, manifest, "color")
        return self._summary(task_dir, manifest, "Color composite is ready for QC and human review.")

    def _retry(self, task_dir: Path, stage: str, feedback: str) -> dict[str, Any]:
        task_dir = task_dir.expanduser().resolve()
        manifest = load_manifest(task_dir)
        allowed = {"clean": {"REVIEW_LINEART", "REVIEW_QC", "FAILED"}, "color": {"REVIEW_QC", "FAILED"}}
        if manifest.get("status") not in allowed[stage]:
            raise PipelineError(f"retry-{stage} is not allowed from {manifest.get('status')}", code="invalid_state", task_dir=task_dir)
        count = manifest.setdefault("retry_counts", {}).get(stage, 0)
        if count >= 2:
            raise PipelineError(f"{stage.title()} stage retry limit reached", code="retry_limit", status="NEEDS_INPUT", task_dir=task_dir)
        manifest["retry_counts"][stage] = count + 1
        manifest.setdefault("review", {})[f"{stage}_feedback"] = feedback
        if manifest.get("provider") == "native-imagegen":
            self._set_native_request(task_dir, manifest, stage, feedback)
        else:
            self._run_api_stage(task_dir, manifest, stage, feedback)
        return self._summary(task_dir, manifest, f"{stage.title()} retry prepared; the stage requires review again.")

    def retry_clean(self, task_dir: Path, feedback: str) -> dict[str, Any]:
        return self._retry(task_dir, "clean", feedback)

    def retry_color(self, task_dir: Path, feedback: str) -> dict[str, Any]:
        return self._retry(task_dir, "color", feedback)

    def finalize(self, task_dir: Path, comparison_layout: str = "none", human_approved: bool = False) -> dict[str, Any]:
        task_dir = task_dir.expanduser().resolve()
        manifest = self._load_and_verify(task_dir, ["04_lineart_work.png", "08_color_composited_work.png", "qc/qc_report.json"])
        if manifest.get("status") != "REVIEW_QC":
            raise PipelineError(f"finalize requires REVIEW_QC, got {manifest.get('status')}", code="invalid_state", task_dir=task_dir)
        if manifest.get("execution_profile") == "desktop-full":
            if manifest.get("qc_status") != "PASS":
                raise PipelineError("Deterministic QC did not pass", code="qc_failed", status="NEEDS_INPUT", task_dir=task_dir)
        else:
            if manifest.get("qc_status") != "LIGHT_PASS":
                raise PipelineError("Portable checks did not pass", code="qc_failed", status="NEEDS_INPUT", task_dir=task_dir)
            if not human_approved:
                raise PipelineError("web-light requires explicit human approval before finalization", code="human_review_required", status="NEEDS_INPUT", task_dir=task_dir)
            manifest["automated_qc_status"] = "LIGHT_PASS"
            manifest["qc_status"] = "HUMAN_REVIEW_ONLY"
            manifest.setdefault("review", {})["color_approved_at"] = utc_now()
        outputs = finalize_outputs(task_dir / "04_lineart_work.png", task_dir / "08_color_composited_work.png", task_dir / "final", comparison_layout)
        manifest["outputs"] = {}
        for key, absolute in outputs.items():
            if absolute:
                relative = Path(absolute).relative_to(task_dir).as_posix()
                manifest["outputs"][key] = relative
                manifest["artifacts"][f"final_{key}"] = relative
                record_hash(manifest, task_dir, relative)
            else:
                manifest["outputs"][key] = None
        manifest["comparison_layout"] = comparison_layout
        manifest["completed_at"] = utc_now()
        manifest["status"] = "COMPLETED"
        save_manifest(task_dir, manifest)
        return self._summary(task_dir, manifest, "Final 1080x1920 PNG files are complete.")

    def export_task(self, task_dir: Path, output: Path) -> dict[str, Any]:
        try:
            bundle = export_bundle(task_dir, output)
        except BundleError as exc:
            raise PipelineError(str(exc), code="bundle_export_failed", status="NEEDS_INPUT", task_dir=task_dir) from exc
        result = self.status(task_dir)
        result["bundle"] = str(bundle)
        result["message"] = "Portable task bundle exported."
        return result

    def import_task(self, bundle: Path, output_root: Path, profile: str) -> dict[str, Any]:
        if profile not in PROFILES:
            raise PipelineError(f"Unsupported profile: {profile}", code="unsupported_profile", status="NEEDS_INPUT")
        task_dir: Path | None = None
        try:
            task_dir = import_bundle(bundle, output_root, profile)
            manifest = load_manifest(task_dir)
            for relative in ["01_original.png", "03_work_canvas_1152x2048.png", "04_lineart_work.png", "07_color_raw.png", *manifest.get("reference_files", [])]:
                candidate = task_dir / relative
                if candidate.is_file():
                    load_image(candidate)
            if profile == "desktop-full" and (task_dir / "04_lineart_work.png").is_file():
                layer = task_dir / "05_line_layer.png"
                extract_line_layer(task_dir / "04_lineart_work.png", layer)
                manifest["artifacts"]["line_layer"] = "05_line_layer.png"
                record_hash(manifest, task_dir, "05_line_layer.png")
                if manifest.get("status") in {"REVIEW_QC", "COMPLETED"} and (task_dir / "07_color_raw.png").is_file():
                    composed = task_dir / "08_color_composited_work.png"
                    composite_line_layer(task_dir / "07_color_raw.png", layer, composed)
                    report = run_deterministic_qc(task_dir / "04_lineart_work.png", composed, layer, task_dir / "qc" / "qc_report.json")
                    manifest["qc_status"] = report["status"]
                    manifest["status"] = "REVIEW_QC"
                    for relative in ["08_color_composited_work.png", "qc/qc_report.json"]:
                        record_hash(manifest, task_dir, relative)
            save_manifest(task_dir, manifest)
        except (BundleError, OSError, ValueError, json.JSONDecodeError) as exc:
            if task_dir is not None and task_dir.is_dir():
                shutil.rmtree(task_dir)
            raise PipelineError(str(exc), code="bundle_import_failed", status="NEEDS_INPUT") from exc
        return self._summary(task_dir, manifest, "Portable task bundle imported as a new task.")

    def status(self, task_dir: Path) -> dict[str, Any]:
        task_dir = task_dir.expanduser().resolve()
        return self._summary(task_dir, load_manifest(task_dir), "Task status loaded.")
