"""Tests for CLI argument parsing."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from omnishot.main import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_REFRESH_THRESHOLD_SECONDS,
    DEFAULT_WATCH_DIR,
    ENV_BUCKET,
    main,
    parse_args,
)


class MainParseArgsTests(unittest.TestCase):
    def test_defaults_to_watch_when_no_subcommand(self) -> None:
        args = parse_args([])
        self.assertEqual(args.command, "watch")
        self.assertEqual(args.watch_dir, DEFAULT_WATCH_DIR)

    def test_upload_subcommand_parsing(self) -> None:
        args = parse_args(["upload", "example.png", "--bucket", "demo", "--expiry", "120"])
        self.assertEqual(args.command, "upload")
        self.assertEqual(str(args.file), "example.png")
        self.assertEqual(args.bucket, "demo")
        self.assertEqual(args.expiry, 120)

    def test_menubar_subcommand_parsing(self) -> None:
        args = parse_args(
            [
                "menubar",
                "--refresh-threshold-seconds",
                "42",
                "--history-limit",
                "9",
            ]
        )
        self.assertEqual(args.command, "menubar")
        self.assertEqual(args.refresh_threshold_seconds, 42)
        self.assertEqual(args.history_limit, 9)

    def test_menubar_defaults(self) -> None:
        args = parse_args(["menubar"])
        self.assertEqual(args.refresh_threshold_seconds, DEFAULT_REFRESH_THRESHOLD_SECONDS)
        self.assertEqual(args.history_limit, DEFAULT_HISTORY_LIMIT)
        self.assertIsNone(args.default_paste_mode)

    def test_default_paste_mode_parsing(self) -> None:
        args = parse_args(["watch", "--default-paste-mode", "path-ref"])
        self.assertEqual(args.default_paste_mode, "path-ref")

    def test_default_paste_mode_accepts_legacy_local_agent_alias(self) -> None:
        args = parse_args(["watch", "--default-paste-mode", "local-agent"])
        self.assertEqual(args.default_paste_mode, "path-ref")

    def test_ssh_host_hint_parsing(self) -> None:
        args = parse_args(["menubar", "--ssh-host-hint", "work-mac"])
        self.assertEqual(args.ssh_host_hint, "work-mac")

    def test_bucket_can_default_from_env(self) -> None:
        with patch.dict("os.environ", {ENV_BUCKET: "example-screenshots"}):
            args = parse_args(["menubar"])

        self.assertEqual(args.bucket, "example-screenshots")

    def test_main_requires_bucket_or_env(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                result = main(["upload", "example.png"])

        self.assertEqual(result, 2)
        self.assertIn(f"--bucket is required or set {ENV_BUCKET}", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
