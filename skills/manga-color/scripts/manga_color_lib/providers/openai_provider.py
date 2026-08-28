from __future__ import annotations

import base64
import os
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable

from ..models import ImageEditRequest, ImageResult, ProviderError


class OpenAIImageProvider:
    name = "openai"

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._sleep = sleep_func

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        key = self._api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderError(
                "OPENAI_API_KEY is not configured. Set it in the environment; do not paste it into chat.",
                code="missing_api_key",
                retryable=False,
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError(
                "The openai package is missing. Install manga-color/requirements.txt first.",
                code="missing_dependency",
                retryable=False,
            ) from exc
        self._client = OpenAI(api_key=key)
        return self._client

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code in {408, 409, 429} or (isinstance(status_code, int) and status_code >= 500):
            return True
        return exc.__class__.__name__ in {
            "APITimeoutError",
            "APIConnectionError",
            "RateLimitError",
            "InternalServerError",
        }

    @staticmethod
    def _usage_to_dict(usage: Any) -> dict[str, Any]:
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return usage.model_dump(exclude_none=True)
        if isinstance(usage, dict):
            return usage
        return {"value": str(usage)}

    def edit_image(self, request: ImageEditRequest) -> ImageResult:
        if not request.images:
            raise ProviderError("At least one input image is required", code="missing_images")
        for image_path in request.images:
            if not image_path.is_file():
                raise ProviderError(f"Input image not found: {image_path}", code="missing_image")
        client = self._get_client()
        started = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(1, request.max_retries + 2):
            try:
                with ExitStack() as stack:
                    handles = [stack.enter_context(path.open("rb")) for path in request.images]
                    response = client.images.edit(
                        model=request.model,
                        image=handles,
                        prompt=request.prompt,
                        size=request.size,
                        quality=request.quality,
                        output_format=request.output_format,
                        background=request.background,
                    )
                data = getattr(response, "data", None)
                if not data:
                    raise ProviderError("OpenAI returned no image data", code="empty_response")
                item = data[0]
                encoded = item.get("b64_json") if isinstance(item, dict) else getattr(item, "b64_json", None)
                if not encoded:
                    raise ProviderError("OpenAI response did not contain b64_json", code="missing_b64")
                try:
                    image_bytes = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as exc:
                    raise ProviderError("OpenAI returned invalid base64 image data", code="invalid_b64") from exc
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = request.output_path.with_name(f".{request.output_path.name}.tmp")
                temp_path.write_bytes(image_bytes)
                temp_path.replace(request.output_path)
                return ImageResult(
                    output_path=request.output_path,
                    provider=self.name,
                    model=request.model,
                    request_id=getattr(response, "_request_id", None) or getattr(response, "request_id", None),
                    elapsed_seconds=time.monotonic() - started,
                    attempts=attempt,
                    usage=self._usage_to_dict(getattr(response, "usage", None)),
                )
            except ProviderError:
                raise
            except Exception as exc:
                last_error = exc
                retryable = self._is_retryable(exc)
                if not retryable or attempt > request.max_retries:
                    code = getattr(exc, "code", None) or exc.__class__.__name__
                    raise ProviderError(
                        f"OpenAI image edit failed ({code})",
                        code=str(code),
                        retryable=retryable,
                    ) from exc
                self._sleep(float(2 ** (attempt - 1)))
        raise ProviderError(
            f"OpenAI image edit failed after retries: {type(last_error).__name__}",
            code="retry_exhausted",
        )

