from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_NAME = "00_manifest.json"
SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upgrade_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    version = int(manifest.get("schema_version", 1))
    if version > SCHEMA_VERSION:
        raise ValueError(f"Unsupported manifest schema version: {version}")
    if version < 2:
        references = manifest.get("reference_files", [])
        manifest.setdefault("execution_profile", "desktop-full")
        manifest.setdefault("actual_model", manifest.get("model"))
        manifest.setdefault("palette_source", "reference" if references else "inferred")
        manifest.setdefault("lineart_lock", "deterministic_overlay")
        manifest.setdefault("pending_edit", None)
        manifest.setdefault("review", {})
        if manifest.get("lineart_approved_at"):
            manifest["review"].setdefault("lineart_approved_at", manifest["lineart_approved_at"])
    manifest["schema_version"] = SCHEMA_VERSION
    return manifest


def load_manifest(task_dir: Path) -> dict[str, Any]:
    path = task_dir / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"Task manifest not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Task manifest must be a JSON object")
    return upgrade_manifest(value)


def save_manifest(task_dir: Path, manifest: dict[str, Any]) -> Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / MANIFEST_NAME
    temp_path = task_dir / f".{MANIFEST_NAME}.tmp"
    manifest = upgrade_manifest(manifest)
    manifest["updated_at"] = utc_now()
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)
    return path


def record_hash(manifest: dict[str, Any], task_dir: Path, relative_path: str) -> str:
    checksum = sha256_file(task_dir / relative_path)
    manifest.setdefault("hashes", {})[relative_path] = checksum
    return checksum


def verify_hashes(
    manifest: dict[str, Any], task_dir: Path, relative_paths: list[str]
) -> list[str]:
    expected = manifest.get("hashes", {})
    mismatches: list[str] = []
    for relative_path in relative_paths:
        path = task_dir / relative_path
        if not path.is_file() or expected.get(relative_path) != sha256_file(path):
            mismatches.append(relative_path)
    return mismatches
