"""Tests for S3 upload helper utilities that don't require AWS calls."""

from __future__ import annotations

import unittest
from pathlib import Path

from omnishot.upload import _content_type, build_public_url


class UploadHelpersTests(unittest.TestCase):
    def test_content_type_override_png(self) -> None:
        self.assertEqual(_content_type(Path("image.png")), "image/png")

    def test_content_type_without_suffix_defaults_to_octet_stream(self) -> None:
        self.assertEqual(_content_type(Path("file_without_suffix")), "application/octet-stream")

    def test_build_public_url_encodes_key(self) -> None:
        url = build_public_url("my-bucket", "screenshots/2026/03/03/hello world#.png")
        self.assertEqual(
            url,
            "https://my-bucket.s3.amazonaws.com/screenshots/2026/03/03/hello%20world%23.png",
        )

    def test_build_public_url_with_custom_base(self) -> None:
        url = build_public_url(
            "ignored-bucket",
            "screenshots/name.png",
            public_base_url="https://cdn.example.com/assets/",
        )
        self.assertEqual(url, "https://cdn.example.com/assets/screenshots/name.png")


if __name__ == "__main__":
    unittest.main()
