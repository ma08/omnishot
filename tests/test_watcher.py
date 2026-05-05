"""Tests for watcher file-filter behavior."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from omnishot.watcher import ScreenshotHandler, ScreenshotResult, build_batch_clipboard_text


class WatcherFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = ScreenshotHandler(bucket="test-bucket")

    def test_should_process_regular_screenshot_png(self) -> None:
        path = Path("Screenshot 2026-03-03 at 1.00.00 PM.png")
        self.assertTrue(self.handler._should_process(path))

    def test_should_skip_dotfile(self) -> None:
        path = Path(".Screenshot 2026-03-03 at 1.00.00 PM.png")
        self.assertFalse(self.handler._should_process(path))

    def test_should_skip_non_png(self) -> None:
        path = Path("Screenshot 2026-03-03 at 1.00.00 PM.jpg")
        self.assertFalse(self.handler._should_process(path))

    def test_should_skip_already_renamed_file(self) -> None:
        path = Path("2026-03-03_01h00m00s_PST_cursor-settings-heavy-memory.png")
        self.assertFalse(self.handler._should_process(path))

    def test_build_batch_clipboard_text_for_s3_urls(self) -> None:
        results = [
            ScreenshotResult(
                presigned_url="https://example.local/one",
                smart_name="one.png",
                local_path=Path("/tmp/one.png"),
                bucket="bucket",
                s3_key="screenshots/one.png",
            ),
            ScreenshotResult(
                presigned_url="https://example.local/two",
                smart_name="two.png",
                local_path=Path("/tmp/two.png"),
                bucket="bucket",
                s3_key="screenshots/two.png",
            ),
        ]

        text = build_batch_clipboard_text(results, paste_mode="s3-url")

        self.assertEqual(text, "https://example.local/one\nhttps://example.local/two")

    def test_build_batch_clipboard_text_for_path_ref_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "one.png"
            second = Path(temp_dir) / "two.png"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            results = [
                ScreenshotResult(
                    presigned_url="https://example.local/one",
                    smart_name="one.png",
                    local_path=first,
                    bucket="bucket",
                    s3_key="screenshots/one.png",
                ),
                ScreenshotResult(
                    presigned_url="https://example.local/two",
                    smart_name="two.png",
                    local_path=second,
                    bucket="bucket",
                    s3_key="screenshots/two.png",
                ),
            ]

            text = build_batch_clipboard_text(
                results,
                paste_mode="path-ref",
                ssh_host_hint="work-mac",
            )

        self.assertEqual(text.count("screenshot-info:"), 2)
        self.assertIn("machine: work-mac", text)

    def test_build_batch_clipboard_text_accepts_legacy_local_agent_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "one.png"
            image_path.write_bytes(b"one")
            results = [
                ScreenshotResult(
                    presigned_url="https://example.local/one",
                    smart_name="one.png",
                    local_path=image_path,
                    bucket="bucket",
                    s3_key="screenshots/one.png",
                )
            ]

            text = build_batch_clipboard_text(
                results,
                paste_mode="local-agent",
                ssh_host_hint="work-mac",
            )

        self.assertIn("screenshot-info:", text)


if __name__ == "__main__":
    unittest.main()
