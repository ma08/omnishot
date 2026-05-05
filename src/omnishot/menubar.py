"""macOS menu-bar app for omnishot."""

from __future__ import annotations

from collections.abc import Callable
import subprocess
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import AppKit
import Quartz
import objc
from PyObjCTools import AppHelper

from .history import (
    HistoryEntry,
    HistoryStore,
    presigned_url_remaining_seconds,
    should_regenerate_presigned_url,
)
from .notify import copy_to_clipboard, log, send_notification
from .paste import (
    AppConfig,
    PASTE_MODE_PATH_REF,
    PASTE_MODE_S3_URL,
    build_path_ref_payload,
    load_app_config,
    normalize_paste_mode,
    save_app_config,
)
from .upload import build_public_url, generate_presigned_url, make_object_public
from .watcher import start_watching

_SHORTCUT_HISTORY_LOOKUP_LIMIT = 50
_SHORTCUT_PASTE_KEYCODE = 9
_SHORTCUT_RESTORE_DELAY_SECONDS = 0.35
_SHORTCUT_RELEVANT_FLAGS = (
    Quartz.kCGEventFlagMaskCommand
    | Quartz.kCGEventFlagMaskAlternate
    | Quartz.kCGEventFlagMaskControl
    | Quartz.kCGEventFlagMaskShift
)
_SHORTCUT_EVENT_MASK = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
_SHORTCUT_REENABLE_EVENT_TYPES = {
    Quartz.kCGEventTapDisabledByTimeout,
    Quartz.kCGEventTapDisabledByUserInput,
}
_PAYLOAD_IMAGE = "image"
_PAYLOAD_PUBLIC_LINK = "public-link"


def _to_local_time(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %I:%M%p")


@dataclass(frozen=True, slots=True)
class ShortcutDefinition:
    payload: str
    display: str
    flags: int
    menu_title: str


_SHORTCUTS = (
    ShortcutDefinition(
        payload=PASTE_MODE_PATH_REF,
        display="Cmd+Option+V",
        flags=Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskAlternate,
        menu_title="Paste Latest Path Reference",
    ),
    ShortcutDefinition(
        payload=_PAYLOAD_IMAGE,
        display="Cmd+Shift+Option+V",
        flags=(
            Quartz.kCGEventFlagMaskCommand
            | Quartz.kCGEventFlagMaskShift
            | Quartz.kCGEventFlagMaskAlternate
        ),
        menu_title="Paste Latest Image",
    ),
    ShortcutDefinition(
        payload=PASTE_MODE_S3_URL,
        display="Cmd+Control+Option+V",
        flags=(
            Quartz.kCGEventFlagMaskCommand
            | Quartz.kCGEventFlagMaskControl
            | Quartz.kCGEventFlagMaskAlternate
        ),
        menu_title="Paste Latest S3 URL",
    ),
    ShortcutDefinition(
        payload=_PAYLOAD_PUBLIC_LINK,
        display="Cmd+Control+Shift+Option+V",
        flags=(
            Quartz.kCGEventFlagMaskCommand
            | Quartz.kCGEventFlagMaskControl
            | Quartz.kCGEventFlagMaskShift
            | Quartz.kCGEventFlagMaskAlternate
        ),
        menu_title="Paste Latest Public Link",
    ),
)


@dataclass(frozen=True, slots=True)
class ShortcutAccessState:
    listen_access: bool
    post_access: bool

    @property
    def is_enabled(self) -> bool:
        return self.listen_access and self.post_access

    def menu_status_title(self) -> str:
        suffix = "ready" if self.is_enabled else "permissions required"
        return f"Shortcuts: V variants ({suffix})"

    def missing_permissions_message(self) -> str:
        missing: list[str] = []
        if not self.listen_access:
            missing.append("Input Monitoring")
        if not self.post_access:
            missing.append("Accessibility")
        if not missing:
            return "Paste shortcuts are ready."
        if len(missing) == 1:
            return f"Grant {missing[0]} access in System Settings."
        return f"Grant {missing[0]} and {missing[1]} access in System Settings."


@dataclass(frozen=True, slots=True)
class PasteboardSnapshot:
    items: list[dict[str, bytes]]
    change_count: int


def _request_shortcut_access(*, prompt_user: bool) -> ShortcutAccessState:
    listen_access = bool(Quartz.CGPreflightListenEventAccess())
    if not listen_access and prompt_user:
        listen_access = bool(Quartz.CGRequestListenEventAccess())

    post_access = bool(Quartz.CGPreflightPostEventAccess())
    if not post_access and prompt_user:
        post_access = bool(Quartz.CGRequestPostEventAccess())

    return ShortcutAccessState(listen_access=listen_access, post_access=post_access)


def _shortcut_for_event(
    *,
    event_type: int,
    keycode: int,
    flags: int,
    autorepeat: int,
) -> ShortcutDefinition | None:
    if event_type != Quartz.kCGEventKeyDown:
        return None
    if keycode != _SHORTCUT_PASTE_KEYCODE:
        return None
    if autorepeat:
        return None
    relevant_flags = int(flags) & _SHORTCUT_RELEVANT_FLAGS
    for shortcut in _SHORTCUTS:
        if relevant_flags == shortcut.flags:
            return shortcut
    return None


def _is_global_paste_shortcut_event(
    *,
    event_type: int,
    keycode: int,
    flags: int,
    autorepeat: int,
) -> bool:
    return (
        _shortcut_for_event(
            event_type=event_type,
            keycode=keycode,
            flags=flags,
            autorepeat=autorepeat,
        )
        is not None
    )


def _shortcut_display_for_payload(payload: str) -> str | None:
    for shortcut in _SHORTCUTS:
        if shortcut.payload == payload:
            return shortcut.display
    return None


def _title_with_shortcut(title: str, payload: str) -> str:
    display = _shortcut_display_for_payload(payload)
    if display is None:
        return title
    return f"{title} [{display}]"


def _snapshot_pasteboard(pasteboard) -> PasteboardSnapshot:
    items: list[dict[str, bytes]] = []
    for item in pasteboard.pasteboardItems() or []:
        item_payload: dict[str, bytes] = {}
        for paste_type in item.types() or []:
            data = item.dataForType_(paste_type)
            if data is None:
                continue
            item_payload[str(paste_type)] = bytes(data)
        items.append(item_payload)
    return PasteboardSnapshot(items=items, change_count=int(pasteboard.changeCount()))


def _restore_pasteboard_snapshot(
    pasteboard,
    snapshot: PasteboardSnapshot,
    *,
    item_factory: Callable[[], object] | None = None,
    data_factory: Callable[[bytes], object] | None = None,
) -> bool:
    item_factory = item_factory or (lambda: AppKit.NSPasteboardItem.alloc().init())
    data_factory = data_factory or (
        lambda payload: AppKit.NSData.dataWithBytes_length_(payload, len(payload))
    )

    pasteboard.clearContents()
    if not snapshot.items:
        return True

    objects = []
    for item_payload in snapshot.items:
        item = item_factory()
        wrote_item = False
        for paste_type, payload in item_payload.items():
            if item.setData_forType_(data_factory(payload), paste_type):
                wrote_item = True
        if wrote_item:
            objects.append(item)

    if not objects:
        return True
    return bool(pasteboard.writeObjects_(objects))


def _restore_pasteboard_snapshot_if_unchanged(
    pasteboard,
    snapshot: PasteboardSnapshot,
    expected_change_count: int,
    *,
    item_factory: Callable[[], object] | None = None,
    data_factory: Callable[[bytes], object] | None = None,
) -> bool:
    if int(pasteboard.changeCount()) != int(expected_change_count):
        return False
    return _restore_pasteboard_snapshot(
        pasteboard,
        snapshot,
        item_factory=item_factory,
        data_factory=data_factory,
    )


def _resolve_latest_local_image_path(
    latest_local_path: Path | None,
    history_store: HistoryStore,
    *,
    history_limit: int = _SHORTCUT_HISTORY_LOOKUP_LIMIT,
) -> Path | None:
    if latest_local_path is not None and latest_local_path.exists():
        return latest_local_path

    for entry in history_store.list_recent(limit=max(1, history_limit)):
        path = Path(entry.local_path)
        if path.exists():
            return path

    return None


def _state_value(is_on: bool) -> int:
    return int(getattr(AppKit, "NSControlStateValueOn", 1) if is_on else 0)


@dataclass(slots=True)
class MenuBarConfig:
    watch_dir: Path
    bucket: str
    prefix: str
    expiry: int
    no_describe: bool
    no_ocr: bool
    no_rename: bool
    no_notify: bool
    verbose: bool
    refresh_threshold_seconds: int
    history_limit: int
    public_base_url: str | None
    default_paste_mode: str
    ssh_host_hint: str | None


class MenuBarAppDelegate(AppKit.NSObject):
    """Owns status bar UI and watcher lifecycle."""

    def initWithConfig_(self, config: MenuBarConfig):  # noqa: N802
        self = objc.super(MenuBarAppDelegate, self).init()
        if self is None:
            return None
        self.config = config
        self.history = HistoryStore()
        self.app_config = AppConfig(
            default_paste_mode=normalize_paste_mode(config.default_paste_mode),
            ssh_host_hint=config.ssh_host_hint,
        )
        self.status_item: AppKit.NSStatusItem | None = None
        self.menu: AppKit.NSMenu | None = None
        self._watcher_stop_event = threading.Event()
        self._watcher_thread: threading.Thread | None = None
        self._latest_local_image_lock = threading.Lock()
        self._latest_local_image_path: Path | None = None
        self._shortcut_access = ShortcutAccessState(listen_access=False, post_access=False)
        self._shortcut_event_tap = None
        self._shortcut_run_loop_source = None
        self._shortcut_event_callback: Callable[..., object] | None = None
        self._shortcut_install_error: str | None = None
        self._clipboard_restore_snapshot: PasteboardSnapshot | None = None
        self._clipboard_restore_expected_change_count: int | None = None
        self._clipboard_restore_token = 0
        return self

    def applicationDidFinishLaunching_(self, _notification) -> None:  # noqa: N802
        self._setup_status_item()
        self._refresh_shortcut_access(prompt_user=True)
        self._start_watcher_thread()

    def applicationWillTerminate_(self, _notification) -> None:  # noqa: N802
        self._stop_watcher_thread()
        self._teardown_shortcut_tap()

    def menuWillOpen_(self, _menu) -> None:  # noqa: N802
        self._refresh_shortcut_access(prompt_user=False)
        self._rebuild_menu()

    def _setup_status_item(self) -> None:
        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        button = self.status_item.button()
        if button is not None:
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "photo.on.rectangle",
                "Omnishot",
            )
            if image is not None:
                image.setTemplate_(True)
                button.setImage_(image)
            else:
                button.setTitle_("Shot")
            button.setToolTip_("omnishot")

        self.menu = AppKit.NSMenu.alloc().initWithTitle_("omnishot")
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self)
        self.status_item.setMenu_(self.menu)

    def _watcher_is_running(self) -> bool:
        return self._watcher_thread is not None and self._watcher_thread.is_alive()

    def _start_watcher_thread(self) -> None:
        if self._watcher_is_running():
            return

        self._watcher_stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._run_watcher,
            name="omnishot-watcher",
            daemon=True,
        )
        self._watcher_thread.start()
        log("[menubar] Watcher started", self.config.verbose, is_debug=True)

    def _run_watcher(self) -> None:
        try:
            start_watching(
                watch_dir=self.config.watch_dir,
                bucket=self.config.bucket,
                prefix=self.config.prefix,
                expiry=self.config.expiry,
                no_describe=self.config.no_describe,
                no_ocr=self.config.no_ocr,
                no_rename=self.config.no_rename,
                no_notify=self.config.no_notify,
                verbose=self.config.verbose,
                stop_event=self._watcher_stop_event,
                default_paste_mode=self._current_default_paste_mode,
                ssh_host_hint=self._current_ssh_host_hint,
                on_local_image_ready=self._record_local_image_ready,
            )
        except Exception as e:
            log(f"[menubar] Watcher crashed: {e}", verbose=True)
            if not self.config.no_notify:
                send_notification("omnishot watcher stopped", str(e))

    def _stop_watcher_thread(self) -> None:
        self._watcher_stop_event.set()
        thread = self._watcher_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._watcher_thread = None

    def _current_default_paste_mode(self) -> str:
        return normalize_paste_mode(self.app_config.default_paste_mode)

    def _current_ssh_host_hint(self) -> str | None:
        return self.app_config.ssh_host_hint

    def _save_app_config(self) -> None:
        save_app_config(self.app_config)

    def _record_local_image_ready(self, path: Path, _smart_name: str) -> None:
        resolved = path.expanduser().resolve(strict=False)
        with self._latest_local_image_lock:
            self._latest_local_image_path = resolved
        log(
            f"[menubar] Latest local image ready: {resolved.name}",
            self.config.verbose,
            is_debug=True,
        )

    def _latest_local_image_candidate(self) -> Path | None:
        with self._latest_local_image_lock:
            return self._latest_local_image_path

    def _shortcut_is_ready(self) -> bool:
        return (
            self._shortcut_access.is_enabled
            and self._shortcut_event_tap is not None
            and self._shortcut_run_loop_source is not None
            and self._shortcut_install_error is None
        )

    def _shortcut_status_title(self) -> str:
        if self._shortcut_install_error:
            return "Shortcuts: V variants (unavailable)"
        return self._shortcut_access.menu_status_title()

    def _shortcut_help_message(self) -> str:
        if self._shortcut_install_error:
            return self._shortcut_install_error
        return self._shortcut_access.missing_permissions_message()

    def _refresh_shortcut_access(self, *, prompt_user: bool) -> ShortcutAccessState:
        self._shortcut_access = _request_shortcut_access(prompt_user=prompt_user)
        if not self._shortcut_access.is_enabled:
            self._shortcut_install_error = None
            self._teardown_shortcut_tap()
            return self._shortcut_access

        self._install_shortcut_tap()
        return self._shortcut_access

    def _install_shortcut_tap(self) -> bool:
        if self._shortcut_event_tap is not None and self._shortcut_run_loop_source is not None:
            Quartz.CGEventTapEnable(self._shortcut_event_tap, True)
            self._shortcut_install_error = None
            return True

        callback = self._handle_shortcut_event_tap
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            _SHORTCUT_EVENT_MASK,
            callback,
            None,
        )
        if tap is None:
            self._shortcut_install_error = "Unable to install the global shortcut event tap."
            log(f"[menubar] {self._shortcut_install_error}", self.config.verbose, is_debug=True)
            return False

        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        if source is None:
            Quartz.CFMachPortInvalidate(tap)
            self._shortcut_install_error = "Unable to install the global shortcut run-loop source."
            log(f"[menubar] {self._shortcut_install_error}", self.config.verbose, is_debug=True)
            return False

        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(),
            source,
            Quartz.kCFRunLoopCommonModes,
        )
        Quartz.CGEventTapEnable(tap, True)

        self._shortcut_event_callback = callback
        self._shortcut_event_tap = tap
        self._shortcut_run_loop_source = source
        self._shortcut_install_error = None
        log("[menubar] Global V-variant shortcuts enabled", self.config.verbose, is_debug=True)
        return True

    def _teardown_shortcut_tap(self) -> None:
        source = self._shortcut_run_loop_source
        if source is not None:
            try:
                Quartz.CFRunLoopRemoveSource(
                    Quartz.CFRunLoopGetCurrent(),
                    source,
                    Quartz.kCFRunLoopCommonModes,
                )
            except Exception:
                pass
            try:
                Quartz.CFRunLoopSourceInvalidate(source)
            except Exception:
                pass

        tap = self._shortcut_event_tap
        if tap is not None:
            try:
                Quartz.CGEventTapEnable(tap, False)
            except Exception:
                pass
            try:
                Quartz.CFMachPortInvalidate(tap)
            except Exception:
                pass

        self._shortcut_run_loop_source = None
        self._shortcut_event_tap = None
        self._shortcut_event_callback = None

    def _handle_shortcut_event_tap(self, _proxy, event_type, event, _user_info):
        if event_type in _SHORTCUT_REENABLE_EVENT_TYPES and self._shortcut_event_tap is not None:
            Quartz.CGEventTapEnable(self._shortcut_event_tap, True)
            return event

        try:
            keycode = int(
                Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            )
            flags = int(Quartz.CGEventGetFlags(event))
            autorepeat = int(
                Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
            )
            shortcut = _shortcut_for_event(
                event_type=int(event_type),
                keycode=keycode,
                flags=flags,
                autorepeat=autorepeat,
            )
            if shortcut is not None:
                AppHelper.callAfter(self._paste_latest_payload_from_shortcut, shortcut.payload)
        except Exception as e:
            log(
                f"[menubar] Global shortcut callback failed: {e}",
                self.config.verbose,
                is_debug=True,
            )

        return event

    def _add_item(
        self,
        menu: AppKit.NSMenu,
        *,
        title: str,
        action: str | None = None,
        enabled: bool = True,
        represented: object | None = None,
        state: bool = False,
    ) -> AppKit.NSMenuItem:
        selector = action if action is not None else None
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
        item.setEnabled_(enabled)
        item.setState_(_state_value(state))
        if action is not None:
            item.setTarget_(self)
        if represented is not None:
            item.setRepresentedObject_(represented)
        menu.addItem_(item)
        return item

    def _entry_title(self, entry: HistoryEntry) -> str:
        when = _to_local_time(entry.created_at_utc)
        return f"{when}  {entry.smart_name}"

    def _add_history_entries(self) -> None:
        if self.menu is None:
            return
        entries = self.history.list_recent(limit=self.config.history_limit)
        if not entries:
            self._add_item(self.menu, title="No uploads yet", enabled=False)
            return

        for entry in entries:
            parent = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                self._entry_title(entry),
                None,
                "",
            )
            submenu = AppKit.NSMenu.alloc().initWithTitle_(entry.smart_name)
            parent.setSubmenu_(submenu)

            self._add_item(submenu, title="Copy S3 URL", action="copyLinkForEntry:", represented=entry.id)
            self._add_item(
                submenu,
                title="Copy Path Reference",
                action="copyLocalPayloadForEntry:",
                represented=entry.id,
            )
            self._add_item(
                submenu,
                title="Generate Public Link and Copy Public Link",
                action="generatePublicLinkForEntry:",
                represented=entry.id,
            )
            self._add_item(
                submenu,
                title="Copy Image",
                action="copyImageForEntry:",
                represented=entry.id,
            )
            submenu.addItem_(AppKit.NSMenuItem.separatorItem())
            local_exists = Path(entry.local_path).exists()
            self._add_item(
                submenu,
                title="Open Local File",
                action="openLocalFileForEntry:",
                represented=entry.id,
                enabled=local_exists,
            )
            self._add_item(
                submenu,
                title="Reveal In Finder",
                action="revealInFinderForEntry:",
                represented=entry.id,
                enabled=local_exists,
            )

            self.menu.addItem_(parent)

    def _rebuild_menu(self) -> None:
        if self.menu is None:
            return
        self.menu.removeAllItems()

        watcher_status = "running" if self._watcher_is_running() else "stopped"
        self._add_item(self.menu, title=f"Watcher: {watcher_status}", enabled=False)
        self._add_item(self.menu, title=self._shortcut_status_title(), enabled=False)
        if not self._shortcut_is_ready():
            self._add_item(
                self.menu,
                title="Request Shortcut Permissions",
                action="requestShortcutPermissions:",
            )
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._add_item(
            self.menu,
            title="Default: Path Reference",
            action="setDefaultPasteMode:",
            represented=PASTE_MODE_PATH_REF,
            state=self._current_default_paste_mode() == PASTE_MODE_PATH_REF,
        )
        self._add_item(
            self.menu,
            title="Default: S3 URL",
            action="setDefaultPasteMode:",
            represented=PASTE_MODE_S3_URL,
            state=self._current_default_paste_mode() == PASTE_MODE_S3_URL,
        )
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._add_item(
            self.menu,
            title=_title_with_shortcut("Copy Latest Path Reference", PASTE_MODE_PATH_REF),
            action="copyLatestLocalPayload:",
        )
        self._add_item(
            self.menu,
            title=_title_with_shortcut("Copy Latest S3 URL", PASTE_MODE_S3_URL),
            action="copyLatestLink:",
        )
        self._add_item(
            self.menu,
            title=_title_with_shortcut("Copy Latest Image", _PAYLOAD_IMAGE),
            action="copyLatestImage:",
        )
        self._add_item(
            self.menu,
            title=_title_with_shortcut(
                "Generate Latest Public Link and Copy",
                _PAYLOAD_PUBLIC_LINK,
            ),
            action="generatePublicLinkForLatest:",
        )
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._add_history_entries()

        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add_item(self.menu, title="Open Screenshots Folder", action="openScreenshotsFolder:")
        self._add_item(self.menu, title="Restart Watcher", action="restartWatcher:")
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add_item(self.menu, title="Quit", action="quitApp:")

    def _entry_from_sender(self, sender) -> HistoryEntry | None:
        represented = sender.representedObject()
        if represented is None:
            return None
        try:
            entry_id = int(represented)
        except (TypeError, ValueError):
            return None
        return self.history.get_entry(entry_id)

    def _copy_link_to_clipboard(self, title: str, link: str) -> None:
        if copy_to_clipboard(link):
            if not self.config.no_notify:
                send_notification(title, "Copied to clipboard", link)
            return
        if not self.config.no_notify:
            send_notification(title, "Failed to copy link")

    def _copy_text_to_pasteboard(self, text: str, pasteboard=None) -> bool:
        pasteboard = pasteboard or AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        paste_type = getattr(AppKit, "NSPasteboardTypeString", "public.utf8-plain-text")
        return bool(pasteboard.setString_forType_(text, paste_type))

    def _copy_image_to_clipboard(self, image_path: Path, pasteboard=None) -> bool:
        """Copy both image data and file-copy metadata to clipboard."""
        pasteboard = pasteboard or AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()

        wrote_any = False
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(image_path))
        file_url = AppKit.NSURL.fileURLWithPath_(str(image_path))
        objects = []
        if file_url is not None:
            objects.append(file_url)
        if image is not None:
            objects.append(image)

        if objects:
            try:
                wrote_any = bool(pasteboard.writeObjects_(objects)) or wrote_any
            except Exception as e:
                log(f"[menubar] Failed writing clipboard objects: {e}", self.config.verbose, is_debug=True)

        # Legacy Finder compatibility: expose explicit file list on pasteboard.
        try:
            if pasteboard.setPropertyList_forType_([str(image_path)], AppKit.NSFilenamesPboardType):
                wrote_any = True
        except Exception as e:
            log(
                f"[menubar] Failed writing Finder file list to clipboard: {e}",
                self.config.verbose,
                is_debug=True,
            )

        # Cursor/VS Code explorer paste path uses a custom file-list clipboard
        # format ("code/file-list") containing newline-separated file URIs.
        try:
            uri = file_url.absoluteString() if file_url is not None else image_path.as_uri()
            payload = uri.encode("utf-8")
            data = AppKit.NSData.dataWithBytes_length_(payload, len(payload))
            if pasteboard.setData_forType_(data, "code/file-list"):
                wrote_any = True
        except Exception as e:
            log(
                f"[menubar] Failed writing code/file-list clipboard payload: {e}",
                self.config.verbose,
                is_debug=True,
            )

        return wrote_any

    def _build_local_payload_for_entry(self, entry: HistoryEntry) -> str:
        return build_path_ref_payload(
            local_path=Path(entry.local_path),
            smart_name=entry.smart_name,
            ssh_host_hint=self._current_ssh_host_hint(),
        )

    def _latest_history_entry(self, *, require_existing_local_file: bool = False) -> HistoryEntry | None:
        for entry in self.history.list_recent(limit=max(self.config.history_limit, 1)):
            if require_existing_local_file and not Path(entry.local_path).exists():
                continue
            return entry
        return None

    def _resolve_latest_paste_target(self) -> Path | None:
        latest_local_path = self._latest_local_image_candidate()
        return _resolve_latest_local_image_path(
            latest_local_path,
            self.history,
            history_limit=max(self.config.history_limit, _SHORTCUT_HISTORY_LOOKUP_LIMIT),
        )

    def _presigned_link_for_entry(self, entry: HistoryEntry) -> tuple[str | None, bool]:
        needs_refresh = should_regenerate_presigned_url(
            entry,
            refresh_threshold_seconds=self.config.refresh_threshold_seconds,
            now=datetime.now(timezone.utc),
        )
        link = entry.last_presigned_url
        if needs_refresh:
            link = generate_presigned_url(
                entry.bucket,
                entry.s3_key,
                expiry=self.config.expiry,
                verbose=self.config.verbose,
            )
            if not link:
                return None, needs_refresh
            self.history.update_presigned_link(
                entry_id=entry.id,
                presigned_url=link,
                expiry_seconds=self.config.expiry,
            )
        return link, needs_refresh

    def _clear_pending_clipboard_restore(self) -> None:
        self._clipboard_restore_snapshot = None
        self._clipboard_restore_expected_change_count = None

    def _clear_pending_clipboard_restore_if_stale(self, pasteboard) -> None:
        expected_change_count = self._clipboard_restore_expected_change_count
        if expected_change_count is None:
            return
        if int(pasteboard.changeCount()) == int(expected_change_count):
            return
        self._clear_pending_clipboard_restore()

    def _post_command_v(self) -> bool:
        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        if source is None:
            return False

        for is_key_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(source, _SHORTCUT_PASTE_KEYCODE, is_key_down)
            if event is None:
                return False
            Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, event)

        return True

    def _restore_clipboard_after_shortcut(self, token: int) -> None:
        if token != self._clipboard_restore_token:
            return

        snapshot = self._clipboard_restore_snapshot
        expected_change_count = self._clipboard_restore_expected_change_count
        self._clear_pending_clipboard_restore()
        if snapshot is None or expected_change_count is None:
            return

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        restored = _restore_pasteboard_snapshot_if_unchanged(
            pasteboard,
            snapshot,
            expected_change_count,
        )
        log(
            f"[menubar] Clipboard restore {'applied' if restored else 'skipped'}",
            self.config.verbose,
            is_debug=True,
        )

    def _write_payload_to_pasteboard(self, payload: str, pasteboard) -> tuple[bool, str]:
        if payload == _PAYLOAD_IMAGE:
            image_path = self._resolve_latest_paste_target()
            if image_path is None:
                return False, "No local screenshot is available yet."
            return self._copy_image_to_clipboard(image_path, pasteboard=pasteboard), image_path.name

        if payload == PASTE_MODE_PATH_REF:
            image_path = self._resolve_latest_paste_target()
            if image_path is None:
                return False, "No local screenshot is available yet."
            text = build_path_ref_payload(
                local_path=image_path,
                smart_name=image_path.name,
                ssh_host_hint=self._current_ssh_host_hint(),
            )
            return self._copy_text_to_pasteboard(text, pasteboard=pasteboard), image_path.name

        if payload == PASTE_MODE_S3_URL:
            entry = self._latest_history_entry()
            if entry is None:
                return False, "No upload history entry is available yet."
            link, _needs_refresh = self._presigned_link_for_entry(entry)
            if not link:
                return False, "No S3 URL is available yet."
            return self._copy_text_to_pasteboard(link, pasteboard=pasteboard), entry.smart_name

        if payload == _PAYLOAD_PUBLIC_LINK:
            entry = self._latest_history_entry()
            if entry is None:
                return False, "No upload history entry is available yet."
            public_url, message = self._public_link_for_entry(entry)
            if public_url is None:
                return False, message
            return self._copy_text_to_pasteboard(public_url, pasteboard=pasteboard), entry.smart_name

        return False, f"Unsupported payload: {payload}"

    def _paste_latest_payload_from_shortcut(self, payload: str) -> None:
        if not self._shortcut_is_ready():
            self._refresh_shortcut_access(prompt_user=True)
            if not self._shortcut_is_ready():
                if not self.config.no_notify:
                    send_notification("Paste shortcut unavailable", self._shortcut_help_message())
                return

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        self._clear_pending_clipboard_restore_if_stale(pasteboard)
        if self._clipboard_restore_snapshot is None:
            self._clipboard_restore_snapshot = _snapshot_pasteboard(pasteboard)

        copied, label = self._write_payload_to_pasteboard(payload, pasteboard)
        if not copied:
            if self._clipboard_restore_snapshot is not None:
                _restore_pasteboard_snapshot(pasteboard, self._clipboard_restore_snapshot)
            self._clear_pending_clipboard_restore()
            if not self.config.no_notify:
                send_notification("Paste shortcut failed", label)
            return

        self._clipboard_restore_expected_change_count = int(pasteboard.changeCount())
        self._clipboard_restore_token += 1
        restore_token = self._clipboard_restore_token

        if not self._post_command_v():
            self._restore_clipboard_after_shortcut(restore_token)
            if not self.config.no_notify:
                send_notification("Paste shortcut failed", "Unable to post Cmd+V.")
            return

        AppHelper.callLater(
            _SHORTCUT_RESTORE_DELAY_SECONDS,
            self._restore_clipboard_after_shortcut,
            restore_token,
        )
        log(
            f"[menubar] Pasted {payload} via shortcut: {label}",
            self.config.verbose,
            is_debug=True,
        )

    def copyLinkForEntry_(self, sender) -> None:  # noqa: N802
        entry = self._entry_from_sender(sender)
        if entry is None:
            if not self.config.no_notify:
                send_notification("Copy link failed", "Entry not found")
            return

        link, needs_refresh = self._presigned_link_for_entry(entry)

        if not link:
            if not self.config.no_notify:
                send_notification("Copy link failed", "No URL available")
            return

        if not needs_refresh:
            remaining = presigned_url_remaining_seconds(entry, now=datetime.now(timezone.utc))
            log(
                f"[menubar] Reusing existing presigned URL for {entry.smart_name} "
                f"(remaining={remaining}s)",
                self.config.verbose,
                is_debug=True,
            )
        self._copy_link_to_clipboard("Link refreshed and copied" if needs_refresh else "Link copied", link)

    def copyLocalPayloadForEntry_(self, sender) -> None:  # noqa: N802
        entry = self._entry_from_sender(sender)
        if entry is None:
            if not self.config.no_notify:
                send_notification("Copy path reference failed", "Entry not found")
            return

        path = Path(entry.local_path)
        if not path.exists():
            if not self.config.no_notify:
                send_notification("Copy path reference failed", "Local file not found")
            return

        payload = self._build_local_payload_for_entry(entry)
        if copy_to_clipboard(payload):
            if not self.config.no_notify:
                send_notification("Path reference copied", entry.smart_name)
            return

        if not self.config.no_notify:
            send_notification("Copy path reference failed", "Unable to write clipboard")

    def copyLatestLocalPayload_(self, _sender) -> None:  # noqa: N802
        image_path = self._resolve_latest_paste_target()
        if image_path is None:
            if not self.config.no_notify:
                send_notification("Copy path reference failed", "No local screenshot is available yet.")
            return
        payload = build_path_ref_payload(
            local_path=image_path,
            smart_name=image_path.name,
            ssh_host_hint=self._current_ssh_host_hint(),
        )
        if copy_to_clipboard(payload):
            if not self.config.no_notify:
                send_notification("Path reference copied", image_path.name)
            return
        if not self.config.no_notify:
            send_notification("Copy path reference failed", "Unable to write clipboard")

    def copyLatestLink_(self, _sender) -> None:  # noqa: N802
        entry = self._latest_history_entry()
        if entry is None:
            if not self.config.no_notify:
                send_notification("Copy S3 URL failed", "No upload history entry is available yet.")
            return
        link, needs_refresh = self._presigned_link_for_entry(entry)
        if not link:
            if not self.config.no_notify:
                send_notification("Copy S3 URL failed", "No URL available")
            return
        self._copy_link_to_clipboard(
            "Link refreshed and copied" if needs_refresh else "Link copied",
            link,
        )

    def copyLatestImage_(self, _sender) -> None:  # noqa: N802
        image_path = self._resolve_latest_paste_target()
        if image_path is None:
            if not self.config.no_notify:
                send_notification("Copy image failed", "No local screenshot is available yet.")
            return
        copied = self._copy_image_to_clipboard(image_path)
        if copied:
            if not self.config.no_notify:
                send_notification("Image copied", "Paste in apps or Finder")
            return
        if not self.config.no_notify:
            send_notification("Copy image failed", "Unable to write image to clipboard")

    def generatePublicLinkForLatest_(self, _sender) -> None:  # noqa: N802
        entry = self._latest_history_entry()
        if entry is None:
            if not self.config.no_notify:
                send_notification("Public link failed", "No upload history entry is available yet.")
            return
        self._generate_public_link_for_entry(entry)

    def setDefaultPasteMode_(self, sender) -> None:  # noqa: N802
        represented = sender.representedObject()
        try:
            mode = normalize_paste_mode(str(represented))
        except ValueError:
            if not self.config.no_notify:
                send_notification("Default paste mode unchanged", "Invalid paste mode")
            return

        self.app_config = replace(self.app_config, default_paste_mode=mode)
        self._save_app_config()
        if not self.config.no_notify:
            send_notification("Default paste mode updated", mode)
        self._rebuild_menu()

    @staticmethod
    def _public_link_error_message(diagnostics: dict[str, object]) -> str:
        error = str(diagnostics.get("error") or "")
        error_code = str(diagnostics.get("error_code") or "")
        if error_code == "AccessDenied" and "BlockPublicAcls" in error:
            return (
                "Bucket blocks public ACLs (BlockPublicAcls). "
                "Disable that setting or use a public bucket policy."
            )
        return error if error else "Unable to make object public"

    def _public_link_for_entry(self, entry: HistoryEntry) -> tuple[str | None, str]:
        diagnostics: dict[str, object] = {}
        updated = make_object_public(
            entry.bucket,
            entry.s3_key,
            verbose=self.config.verbose,
            diagnostics=diagnostics,
        )
        if not updated:
            return None, self._public_link_error_message(diagnostics)

        public_url = build_public_url(
            entry.bucket,
            entry.s3_key,
            public_base_url=self.config.public_base_url,
        )
        self.history.mark_public(entry_id=entry.id, public_url=public_url)
        return public_url, entry.smart_name

    def _generate_public_link_for_entry(self, entry: HistoryEntry) -> None:
        public_url, message = self._public_link_for_entry(entry)
        if public_url is None:
            if not self.config.no_notify:
                send_notification("Public link failed", message)
            return
        self._copy_link_to_clipboard("Public link copied", public_url)

    def generatePublicLinkForEntry_(self, sender) -> None:  # noqa: N802
        entry = self._entry_from_sender(sender)
        if entry is None:
            if not self.config.no_notify:
                send_notification("Public link failed", "Entry not found")
            return
        self._generate_public_link_for_entry(entry)

    def copyImageForEntry_(self, sender) -> None:  # noqa: N802
        entry = self._entry_from_sender(sender)
        if entry is None:
            if not self.config.no_notify:
                send_notification("Copy image failed", "Entry not found")
            return

        path = Path(entry.local_path)
        if not path.exists():
            if not self.config.no_notify:
                send_notification("Copy image failed", "Local file not found")
            return

        copied = self._copy_image_to_clipboard(path)
        if copied:
            if not self.config.no_notify:
                send_notification("Image copied", "Paste in apps or Finder")
            return

        if not self.config.no_notify:
            send_notification("Copy image failed", "Unable to write image to clipboard")

    def requestShortcutPermissions_(self, _sender) -> None:  # noqa: N802
        self._refresh_shortcut_access(prompt_user=True)
        if not self.config.no_notify:
            if self._shortcut_is_ready():
                send_notification("Paste shortcuts ready", "V variants enabled")
            else:
                send_notification("Paste shortcuts unavailable", self._shortcut_help_message())
        self._rebuild_menu()

    def openLocalFileForEntry_(self, sender) -> None:  # noqa: N802
        entry = self._entry_from_sender(sender)
        if entry is None:
            return
        path = Path(entry.local_path)
        if not path.exists():
            if not self.config.no_notify:
                send_notification("Open failed", "Local file not found")
            return
        subprocess.run(["open", str(path)], check=False)

    def revealInFinderForEntry_(self, sender) -> None:  # noqa: N802
        entry = self._entry_from_sender(sender)
        if entry is None:
            return
        path = Path(entry.local_path)
        if not path.exists():
            if not self.config.no_notify:
                send_notification("Reveal failed", "Local file not found")
            return
        subprocess.run(["open", "-R", str(path)], check=False)

    def openScreenshotsFolder_(self, _sender) -> None:  # noqa: N802
        self.config.watch_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(self.config.watch_dir)], check=False)

    def restartWatcher_(self, _sender) -> None:  # noqa: N802
        self._stop_watcher_thread()
        self._start_watcher_thread()
        if not self.config.no_notify:
            send_notification("Watcher restarted", str(self.config.watch_dir))

    def quitApp_(self, _sender) -> None:  # noqa: N802
        self._stop_watcher_thread()
        AppKit.NSApp().terminate_(None)


def run_menubar(
    *,
    watch_dir: Path,
    bucket: str,
    prefix: str,
    expiry: int,
    no_describe: bool,
    no_ocr: bool,
    no_rename: bool,
    no_notify: bool,
    verbose: bool,
    refresh_threshold_seconds: int,
    history_limit: int,
    public_base_url: str | None,
    default_paste_mode: str,
    ssh_host_hint: str | None,
) -> None:
    """Run the status-bar widget and watcher in one process."""
    persisted_config = load_app_config()
    app_config = AppConfig(
        default_paste_mode=normalize_paste_mode(default_paste_mode or persisted_config.default_paste_mode),
        ssh_host_hint=ssh_host_hint or persisted_config.ssh_host_hint,
    )
    config = MenuBarConfig(
        watch_dir=watch_dir.expanduser().resolve(),
        bucket=bucket,
        prefix=prefix,
        expiry=expiry,
        no_describe=no_describe,
        no_ocr=no_ocr,
        no_rename=no_rename,
        no_notify=no_notify,
        verbose=verbose,
        refresh_threshold_seconds=max(0, refresh_threshold_seconds),
        history_limit=max(1, history_limit),
        public_base_url=public_base_url,
        default_paste_mode=app_config.default_paste_mode,
        ssh_host_hint=app_config.ssh_host_hint,
    )

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    delegate = MenuBarAppDelegate.alloc().initWithConfig_(config)
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()
