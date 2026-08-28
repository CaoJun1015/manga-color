from __future__ import annotations

import json
import os
import secrets
import shutil
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .manifest import MANIFEST_NAME, load_manifest, save_manifest, sha256_bytes, sha256_file, utc_now


BUNDLE_NAME = "bundle.json"
BUNDLE_VERSION = 1
MAX_FILES = 100
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
SENSITIVE_KEYS = {"api_key", "authorization", "access_token", "refresh_token", "secret"}


class BundleError(ValueError):
    pass


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in SENSITIVE_KEYS or _contains_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _task_files(task_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in task_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(task_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if "__pycache__" in relative.parts or path.suffix.lower() in {".zip", ".pyc"}:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(task_dir).as_posix())


def export_bundle(task_dir: Path, output_path: Path) -> Path:
    task_dir = task_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    manifest = load_manifest(task_dir)
    if _contains_sensitive_key(manifest):
        raise BundleError("Task manifest contains a sensitive credential field")
    files = _task_files(task_dir)
    if not files or not (task_dir / MANIFEST_NAME).is_file():
        raise BundleError("Task bundle requires a manifest and task files")
    if len(files) > MAX_FILES:
        raise BundleError(f"Task contains too many files ({len(files)} > {MAX_FILES})")
    total = sum(path.stat().st_size for path in files)
    if total > MAX_UNCOMPRESSED_BYTES:
        raise BundleError("Task is too large to export")
    checksums = {
        path.relative_to(task_dir).as_posix(): sha256_file(path)
        for path in files
    }
    metadata = {
        "bundle_version": BUNDLE_VERSION,
        "origin_task_id": manifest.get("task_id"),
        "status": manifest.get("status"),
        "execution_profile": manifest.get("execution_profile"),
        "created_at": utc_now(),
        "files": checksums,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")
    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(BUNDLE_NAME, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        for path in files:
            archive.write(path, path.relative_to(task_dir).as_posix())
    os.replace(temp_path, output_path)
    return output_path


def _safe_member(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name:
        raise BundleError(f"Unsafe bundle path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or ":" in path.parts[0]:
        raise BundleError(f"Unsafe bundle path: {name}")
    if any(part in {"", "."} for part in path.parts):
        raise BundleError(f"Invalid bundle path: {name}")
    return path


def import_bundle(bundle_path: Path, output_root: Path, profile: str) -> Path:
    if profile not in {"desktop-full", "web-light"}:
        raise BundleError(f"Unsupported execution profile: {profile}")
    bundle_path = bundle_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if not bundle_path.is_file():
        raise BundleError(f"Task bundle not found: {bundle_path}")
    with zipfile.ZipFile(bundle_path, "r") as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 1:
            raise BundleError("Task bundle contains too many files")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise BundleError("Task bundle is too large")
        names = {info.filename for info in infos if not info.is_dir()}
        if any(((info.external_attr >> 16) & 0o170000) == 0o120000 for info in infos):
            raise BundleError("Task bundle may not contain symbolic links")
        for name in names:
            _safe_member(name)
        if BUNDLE_NAME not in names or MANIFEST_NAME not in names:
            raise BundleError("Task bundle is missing required metadata")
        try:
            metadata = json.loads(archive.read(BUNDLE_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError("Task bundle metadata is invalid") from exc
        if metadata.get("bundle_version") != BUNDLE_VERSION:
            raise BundleError("Unsupported task bundle version")
        expected = metadata.get("files")
        if not isinstance(expected, dict) or MANIFEST_NAME not in expected:
            raise BundleError("Task bundle checksum list is invalid")
        if names != {BUNDLE_NAME, *expected.keys()}:
            raise BundleError("Task bundle contains undeclared files")
        for name, checksum in expected.items():
            _safe_member(name)
            if name not in names or sha256_bytes(archive.read(name)) != checksum:
                raise BundleError(f"Task bundle checksum mismatch: {name}")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_id = f"{timestamp}_{secrets.token_hex(3)}_imported"
        task_dir = output_root / task_id
        task_dir.mkdir(parents=True, exist_ok=False)
        try:
            for name in expected:
                relative = _safe_member(name)
                destination = task_dir.joinpath(*relative.parts)
                resolved = destination.resolve()
                if task_dir.resolve() not in resolved.parents:
                    raise BundleError(f"Unsafe extraction target: {name}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
        except Exception:
            shutil.rmtree(task_dir)
            raise
    manifest = load_manifest(task_dir)
    origin_task_id = manifest.get("task_id") or metadata.get("origin_task_id")
    manifest["task_id"] = task_id
    manifest["origin_task_id"] = origin_task_id
    manifest["imported_at"] = utc_now()
    manifest["imported_from_bundle"] = bundle_path.name
    manifest["previous_execution_profile"] = manifest.get("execution_profile")
    manifest["execution_profile"] = profile
    manifest["lineart_lock"] = (
        "deterministic_overlay" if profile == "desktop-full" else "human_visual_only"
    )
    save_manifest(task_dir, manifest)
    return task_dir
