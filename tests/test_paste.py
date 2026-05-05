"""Tests for paste-mode config and path-ref payload helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omnishot.paste import (
    ENV_DEFAULT_PASTE_MODE,
    ENV_MACHINE_SSH_ALIAS,
    ENV_SSH_HOST_HINT,
    AppConfig,
    MachineMetadata,
    build_path_ref_payload,
    load_app_config,
    normalize_paste_mode,
    resolve_machine_metadata,
    resolve_runtime_config,
    save_app_config,
)


class PasteHelperTests(unittest.TestCase):
    def test_normalize_paste_mode_accepts_aliases(self) -> None:
        self.assertEqual(normalize_paste_mode("path-ref"), "path-ref")
        self.assertEqual(normalize_paste_mode("local"), "path-ref")
        self.assertEqual(normalize_paste_mode("local-agent"), "path-ref")
        self.assertEqual(normalize_paste_mode("s3_url"), "s3-url")

    def test_normalize_paste_mode_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            normalize_paste_mode("clipboard-sandwich")

    def test_save_and_load_app_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            save_app_config(
                AppConfig(default_paste_mode="s3-url", ssh_host_hint="work-mac"),
                config_path,
            )

            loaded = load_app_config(config_path)

        self.assertEqual(loaded.default_paste_mode, "s3-url")
        self.assertEqual(loaded.ssh_host_hint, "work-mac")

    def test_resolve_runtime_config_prefers_cli_then_env_then_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            save_app_config(
                AppConfig(default_paste_mode="s3-url", ssh_host_hint="persisted-host"),
                config_path,
            )
            resolved = resolve_runtime_config(
                default_paste_mode=None,
                ssh_host_hint=None,
                config_path=config_path,
                env={
                    ENV_DEFAULT_PASTE_MODE: "local-agent",
                    ENV_SSH_HOST_HINT: "env-host",
                },
            )

        self.assertEqual(resolved.default_paste_mode, "path-ref")
        self.assertEqual(resolved.ssh_host_hint, "env-host")

    def test_build_path_ref_payload_is_compact_and_retrievable(self) -> None:
        payload = build_path_ref_payload(
            local_path=Path("/Users/alex/Pictures/Screenshots/example image.png"),
            smart_name="example image.png",
            machine_metadata=MachineMetadata(machine="work-mac"),
        )

        self.assertEqual(
            payload,
            "\n".join(
                [
                    "screenshot-info:",
                    "  machine: work-mac",
                    "  path: /Users/alex/Pictures/Screenshots/example image.png",
                ]
            ),
        )

    def test_resolve_machine_metadata_prefers_explicit_hint_then_env(self) -> None:
        with patch.dict("os.environ", {ENV_MACHINE_SSH_ALIAS: "work-mac"}):
            self.assertEqual(resolve_machine_metadata().machine, "work-mac")
            self.assertEqual(
                resolve_machine_metadata(ssh_host_hint="override-host").machine,
                "override-host",
            )


if __name__ == "__main__":
    unittest.main()
