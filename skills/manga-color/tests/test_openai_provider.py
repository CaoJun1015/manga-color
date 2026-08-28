from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from manga_color_lib.models import ImageEditRequest, ProviderError  # noqa: E402
from manga_color_lib.providers import OpenAIImageProvider  # noqa: E402


class FakeImagesEndpoint:
    def __init__(self, response: object, failures: list[Exception] | None = None) -> None:
        self.response = response
        self.failures = list(failures or [])
        self.calls: list[dict] = []

    def edit(self, **kwargs):
        self.calls.append(kwargs)
        for handle in kwargs["image"]:
            self.assert_open(handle)
        if self.failures:
            raise self.failures.pop(0)
        return self.response

    @staticmethod
    def assert_open(handle) -> None:
        if handle.closed:
            raise AssertionError("input file was closed before the SDK call")


class RetryableError(Exception):
    status_code = 429
    code = "rate_limit"


class BadRequestError(Exception):
    status_code = 400
    code = "bad_request"


class OpenAIProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_one = self.root / "one.png"
        self.input_two = self.root / "two.png"
        image = Image.new("RGB", (32, 32), "white")
        image.save(self.input_one)
        image.save(self.input_two)
        output_bytes = self.input_one.read_bytes()
        self.response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(output_bytes).decode("ascii"))],
            _request_id="req-test",
            usage={"total_tokens": 12},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self) -> ImageEditRequest:
        return ImageEditRequest(
            images=(self.input_one, self.input_two),
            prompt="test prompt",
            output_path=self.root / "out.png",
        )

    def test_sends_ordered_images_and_required_defaults(self) -> None:
        endpoint = FakeImagesEndpoint(self.response)
        client = SimpleNamespace(images=endpoint)
        provider = OpenAIImageProvider(client=client, sleep_func=lambda _: None)
        result = provider.edit_image(self.request())
        self.assertTrue(result.output_path.is_file())
        self.assertEqual(result.request_id, "req-test")
        call = endpoint.calls[0]
        self.assertEqual([Path(handle.name).name for handle in call["image"]], ["one.png", "two.png"])
        self.assertEqual(call["model"], "gpt-image-2")
        self.assertEqual(call["size"], "1152x2048")
        self.assertEqual(call["quality"], "high")
        self.assertEqual(call["output_format"], "png")
        self.assertEqual(call["background"], "opaque")
        self.assertNotIn("input_fidelity", call)

    def test_retries_transient_errors_twice(self) -> None:
        endpoint = FakeImagesEndpoint(self.response, [RetryableError(), RetryableError()])
        sleeps: list[float] = []
        provider = OpenAIImageProvider(
            client=SimpleNamespace(images=endpoint), sleep_func=sleeps.append
        )
        result = provider.edit_image(self.request())
        self.assertEqual(result.attempts, 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_stops_after_two_transient_retries(self) -> None:
        endpoint = FakeImagesEndpoint(
            self.response, [RetryableError(), RetryableError(), RetryableError()]
        )
        sleeps: list[float] = []
        provider = OpenAIImageProvider(
            client=SimpleNamespace(images=endpoint), sleep_func=sleeps.append
        )
        with self.assertRaises(ProviderError) as context:
            provider.edit_image(self.request())
        self.assertTrue(context.exception.retryable)
        self.assertEqual(len(endpoint.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_does_not_retry_bad_request_or_expose_details(self) -> None:
        endpoint = FakeImagesEndpoint(self.response, [BadRequestError("secret-bearing detail")])
        provider = OpenAIImageProvider(client=SimpleNamespace(images=endpoint), sleep_func=lambda _: None)
        with self.assertRaises(ProviderError) as context:
            provider.edit_image(self.request())
        self.assertFalse(context.exception.retryable)
        self.assertNotIn("secret-bearing", str(context.exception))
        self.assertEqual(len(endpoint.calls), 1)

    def test_missing_api_key_fails_before_import_or_network(self) -> None:
        provider = OpenAIImageProvider()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError) as context:
                provider.edit_image(self.request())
        self.assertEqual(context.exception.code, "missing_api_key")
        self.assertIn("environment", str(context.exception))


if __name__ == "__main__":
    unittest.main()
