"""Tests for menubar shortcut helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import Quartz

from omnishot.menubar import (
    PasteboardSnapshot,
    _PAYLOAD_IMAGE,
    _PAYLOAD_PUBLIC_LINK,
    _is_global_paste_shortcut_event,
    _resolve_latest_local_image_path,
    _restore_pasteboard_snapshot_if_unchanged,
    _shortcut_for_event,
    _snapshot_pasteboard,
    _title_with_shortcut,
)
from omnishot.paste import PASTE_MODE_PATH_REF, PASTE_MODE_S3_URL


class FakeHistoryStore:
    def __init__(self, paths: list[Path]):
        self._entries = [SimpleNamespace(local_path=str(path)) for path in paths]

    def list_recent(self, *, limit: int = 20):
        return self._entries[:limit]


class FakeReadablePasteboardItem:
    def __init__(self, payload: dict[str, bytes]):
        self.payload = payload

    def types(self):
        return list(self.payload.keys())

    def dataForType_(self, paste_type: str):
        return self.payload.get(paste_type)


class FakeWritablePasteboardItem:
    def __init__(self):
        self.payload: dict[str, bytes] = {}

    def setData_forType_(self, data: bytes, paste_type: str) -> bool:
        self.payload[paste_type] = data
        return True


class FakePasteboard:
    def __init__(self, item_payloads: list[dict[str, bytes]] | None = None, *, change_count: int = 0):
        self._items = [FakeReadablePasteboardItem(payload) for payload in item_payloads or []]
        self._change_count = change_count
        self.clear_called = False
        self.written_objects: list[FakeWritablePasteboardItem] | None = None

    def pasteboardItems(self):
        return list(self._items)

    def changeCount(self) -> int:
        return self._change_count

    def clearContents(self) -> None:
        self.clear_called = True
        self._items = []
        self._change_count += 1

    def writeObjects_(self, objects) -> bool:
        self.written_objects = list(objects)
        self._items = list(objects)
        self._change_count += 1
        return True


class MenuBarShortcutHelperTests(unittest.TestCase):
    def test_shortcut_event_matches_path_ref_command_option_v(self) -> None:
        shortcut = _shortcut_for_event(
            event_type=Quartz.kCGEventKeyDown,
            keycode=9,
            flags=Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskAlternate,
            autorepeat=0,
        )

        assert shortcut is not None
        self.assertEqual(shortcut.payload, PASTE_MODE_PATH_REF)

    def test_shortcut_event_matches_image_command_shift_option_v(self) -> None:
        shortcut = _shortcut_for_event(
            event_type=Quartz.kCGEventKeyDown,
            keycode=9,
            flags=(
                Quartz.kCGEventFlagMaskCommand
                | Quartz.kCGEventFlagMaskShift
                | Quartz.kCGEventFlagMaskAlternate
            ),
            autorepeat=0,
        )

        assert shortcut is not None
        self.assertEqual(shortcut.payload, _PAYLOAD_IMAGE)

    def test_shortcut_event_matches_s3_command_control_option_v(self) -> None:
        shortcut = _shortcut_for_event(
            event_type=Quartz.kCGEventKeyDown,
            keycode=9,
            flags=(
                Quartz.kCGEventFlagMaskCommand
                | Quartz.kCGEventFlagMaskControl
                | Quartz.kCGEventFlagMaskAlternate
            ),
            autorepeat=0,
        )

        assert shortcut is not None
        self.assertEqual(shortcut.payload, PASTE_MODE_S3_URL)

    def test_shortcut_event_matches_public_link_command_control_shift_option_v(self) -> None:
        shortcut = _shortcut_for_event(
            event_type=Quartz.kCGEventKeyDown,
            keycode=9,
            flags=(
                Quartz.kCGEventFlagMaskCommand
                | Quartz.kCGEventFlagMaskControl
                | Quartz.kCGEventFlagMaskShift
                | Quartz.kCGEventFlagMaskAlternate
            ),
            autorepeat=0,
        )

        assert shortcut is not None
        self.assertEqual(shortcut.payload, _PAYLOAD_PUBLIC_LINK)

    def test_latest_menu_title_includes_shortcut_display(self) -> None:
        self.assertEqual(
            _title_with_shortcut("Generate Latest Public Link and Copy", _PAYLOAD_PUBLIC_LINK),
            "Generate Latest Public Link and Copy [Cmd+Control+Shift+Option+V]",
        )

    def test_shortcut_event_rejects_autorepeat(self) -> None:
        self.assertFalse(
            _is_global_paste_shortcut_event(
                event_type=Quartz.kCGEventKeyDown,
                keycode=9,
                flags=Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskAlternate,
                autorepeat=1,
            )
        )

    def test_resolve_latest_local_image_path_prefers_existing_cached_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cached_path = Path(temp_dir) / "cached.png"
            cached_path.write_bytes(b"cached")
            history_path = Path(temp_dir) / "history.png"
            history_path.write_bytes(b"history")

            resolved = _resolve_latest_local_image_path(
                cached_path,
                FakeHistoryStore([history_path]),
            )

        self.assertEqual(resolved, cached_path)

    def test_snapshot_pasteboard_captures_items(self) -> None:
        pasteboard = FakePasteboard(
            [{"public.png": b"png-bytes", "public.utf8-plain-text": b"hello"}],
            change_count=4,
        )

        snapshot = _snapshot_pasteboard(pasteboard)

        self.assertEqual(
            snapshot,
            PasteboardSnapshot(
                items=[{"public.png": b"png-bytes", "public.utf8-plain-text": b"hello"}],
                change_count=4,
            ),
        )

    def test_restore_pasteboard_snapshot_if_unchanged_restores_saved_items(self) -> None:
        snapshot = PasteboardSnapshot(items=[{"public.png": b"png-bytes"}], change_count=2)
        pasteboard = FakePasteboard(change_count=7)

        restored = _restore_pasteboard_snapshot_if_unchanged(
            pasteboard,
            snapshot,
            expected_change_count=7,
            item_factory=FakeWritablePasteboardItem,
            data_factory=lambda payload: payload,
        )

        self.assertTrue(restored)
        self.assertTrue(pasteboard.clear_called)
        assert pasteboard.written_objects is not None
        self.assertEqual(pasteboard.written_objects[0].payload, {"public.png": b"png-bytes"})

    def test_restore_pasteboard_snapshot_if_unchanged_skips_when_change_count_differs(self) -> None:
        snapshot = PasteboardSnapshot(items=[{"public.png": b"png-bytes"}], change_count=2)
        pasteboard = FakePasteboard(change_count=8)

        restored = _restore_pasteboard_snapshot_if_unchanged(
            pasteboard,
            snapshot,
            expected_change_count=7,
            item_factory=FakeWritablePasteboardItem,
            data_factory=lambda payload: payload,
        )

        self.assertFalse(restored)
        self.assertFalse(pasteboard.clear_called)
        self.assertIsNone(pasteboard.written_objects)


if __name__ == "__main__":
    unittest.main()
