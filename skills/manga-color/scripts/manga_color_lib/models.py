from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ImageEditRequest:
    images: tuple[Path, ...]
    prompt: str
    output_path: Path
    model: str = "gpt-image-2"
    size: str = "1152x2048"
    quality: str = "high"
    output_format: str = "png"
    background: str = "opaque"
    max_retries: int = 2


@dataclass(frozen=True)
class ImageResult:
    output_path: Path
    provider: str
    model: str
    request_id: str | None = None
    elapsed_seconds: float = 0.0
    attempts: int = 1
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "provider": self.provider,
            "model": self.model,
            "request_id": self.request_id,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "attempts": self.attempts,
            "usage": self.usage,
        }


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ImageProvider(Protocol):
    name: str

    def edit_image(self, request: ImageEditRequest) -> ImageResult:
        ...

