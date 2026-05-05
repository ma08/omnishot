"""Tests for local history storage and link-refresh behavior."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from omnishot.history import (
    HistoryStore,
    presigned_url_remaining_seconds,
    resolve_machine_name,
    should_regenerate_presigned_url,
)


class HistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "history.db"
        self.store = HistoryStore(db_path=self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_record_upload_and_list_recent(self) -> None:
        entry_id = self.store.record_upload(
            local_path=Path("/tmp/example.png"),
            smart_name="2026-03-02_22h00m00s_PST_example.png",
            bucket="test-bucket",
            s3_key="screenshots/2026/03/02/example.png",
            presigned_url="https://example.local/presigned",
            presigned_expiry_seconds=3600,
            machine_name="machine-1",
        )

        entries = self.store.list_recent(limit=5)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].id, entry_id)
        self.assertEqual(entries[0].bucket, "test-bucket")
        self.assertEqual(entries[0].smart_name, "2026-03-02_22h00m00s_PST_example.png")
        self.assertEqual(entries[0].machine_name, "machine-1")

    def test_should_regenerate_when_near_expiry(self) -> None:
        entry_id = self.store.record_upload(
            local_path=Path("/tmp/example.png"),
            smart_name="near-expiry.png",
            bucket="test-bucket",
            s3_key="screenshots/near-expiry.png",
            presigned_url="https://example.local/near-expiry",
            presigned_expiry_seconds=3600,
        )
        generated_at = datetime.now(timezone.utc) - timedelta(seconds=3550)
        self.store.update_presigned_link(
            entry_id=entry_id,
            presigned_url="https://example.local/near-expiry",
            expiry_seconds=3600,
            generated_at_utc=generated_at,
        )

        entry = self.store.get_entry(entry_id)
        assert entry is not None
        self.assertTrue(
            should_regenerate_presigned_url(
                entry,
                refresh_threshold_seconds=300,
                now=datetime.now(timezone.utc),
            )
        )

    def test_should_not_regenerate_when_fresh(self) -> None:
        entry_id = self.store.record_upload(
            local_path=Path("/tmp/example.png"),
            smart_name="fresh.png",
            bucket="test-bucket",
            s3_key="screenshots/fresh.png",
            presigned_url="https://example.local/fresh",
            presigned_expiry_seconds=3600,
        )
        generated_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        self.store.update_presigned_link(
            entry_id=entry_id,
            presigned_url="https://example.local/fresh",
            expiry_seconds=3600,
            generated_at_utc=generated_at,
        )

        entry = self.store.get_entry(entry_id)
        assert entry is not None
        remaining = presigned_url_remaining_seconds(entry, now=datetime.now(timezone.utc))
        assert remaining is not None
        self.assertGreater(remaining, 300)
        self.assertFalse(
            should_regenerate_presigned_url(
                entry,
                refresh_threshold_seconds=300,
                now=datetime.now(timezone.utc),
            )
        )

    def test_mark_public_link(self) -> None:
        entry_id = self.store.record_upload(
            local_path=Path("/tmp/example.png"),
            smart_name="public.png",
            bucket="test-bucket",
            s3_key="screenshots/public.png",
            presigned_url="https://example.local/public",
            presigned_expiry_seconds=3600,
        )
        self.store.mark_public(entry_id=entry_id, public_url="https://cdn.example.com/screenshots/public.png")

        entry = self.store.get_entry(entry_id)
        assert entry is not None
        self.assertTrue(entry.is_public)
        self.assertEqual(entry.public_url, "https://cdn.example.com/screenshots/public.png")

    def test_resolve_machine_name_reads_botfiles_machine_rc_when_env_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            botfiles_root = Path(temp_dir) / "botfiles"
            machine_rc = botfiles_root / "secrets" / "local" / "machine.rc"
            machine_rc.parent.mkdir(parents=True)
            machine_rc.write_text('export SYSTEM_NAME="Work-Macbook"\n', encoding="utf-8")

            with patch.dict(
                "os.environ",
                {"BOTFILES_ROOT": str(botfiles_root)},
                clear=True,
            ):
                self.assertEqual(resolve_machine_name(), "Work-Macbook")


if __name__ == "__main__":
    unittest.main()
