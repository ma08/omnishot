"""File system watcher for new screenshots."""

from collections.abc import Callable
from dataclasses import dataclass
import re
import threading
import time
from pathlib import Path
from typing import Any

from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .enrich import build_filename, enrich_screenshot_with_context, s3_key_for_file
from .history import HistoryStore
from .notify import copy_to_clipboard, log, send_notification
from .paste import (
    PASTE_MODE_PATH_REF,
    PASTE_MODE_S3_URL,
    build_path_ref_payload,
    normalize_paste_mode,
)
from .tracing import (
    flush_langfuse,
    get_langfuse_client,
    get_trace_id,
    get_trace_url,
    start_span,
    update_observation,
)
from .upload import generate_presigned_url, upload_to_s3

# Pattern matching smart-renamed files (starts with date_time_tz)
_SMART_NAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}h\d{2}m\d{2}s_")

# How long to wait for more screenshots before flushing the batch (seconds).
# Multi-monitor Cmd+Shift+3 saves files within ~1s of each other.
_BATCH_WINDOW = 2.0

_HISTORY_STORE: HistoryStore | None = None


@dataclass(frozen=True, slots=True)
class ScreenshotResult:
    presigned_url: str | None
    smart_name: str
    local_path: Path
    bucket: str
    s3_key: str | None


def _get_history_store() -> HistoryStore:
    global _HISTORY_STORE
    if _HISTORY_STORE is None:
        _HISTORY_STORE = HistoryStore()
    return _HISTORY_STORE


def build_batch_clipboard_text(
    results: list[ScreenshotResult],
    *,
    paste_mode: str,
    ssh_host_hint: str | None = None,
) -> str:
    mode = normalize_paste_mode(paste_mode)
    if mode == PASTE_MODE_S3_URL:
        return "\n".join(result.presigned_url for result in results if result.presigned_url)
    if mode == PASTE_MODE_PATH_REF:
        return "\n\n".join(
            build_path_ref_payload(
                local_path=result.local_path,
                smart_name=result.smart_name,
                ssh_host_hint=ssh_host_hint,
            )
            for result in results
            if result.local_path.exists()
        )
    raise ValueError(f"unsupported paste mode: {paste_mode!r}")


class ScreenshotHandler(FileSystemEventHandler):
    """Handles new screenshot file events, batching multi-monitor captures."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "screenshots",
        expiry: int = 86400,
        no_describe: bool = False,
        no_ocr: bool = False,
        no_rename: bool = False,
        no_notify: bool = False,
        verbose: bool = False,
        default_paste_mode: str | Callable[[], str] = PASTE_MODE_PATH_REF,
        ssh_host_hint: str | Callable[[], str | None] | None = None,
        on_local_image_ready: Callable[[Path, str], None] | None = None,
    ):
        super().__init__()
        self.bucket = bucket
        self.prefix = prefix
        self.expiry = expiry
        self.no_describe = no_describe
        self.no_ocr = no_ocr
        self.no_rename = no_rename
        self.no_notify = no_notify
        self.verbose = verbose
        self.default_paste_mode = default_paste_mode
        self.ssh_host_hint = ssh_host_hint
        self.on_local_image_ready = on_local_image_ready

        self._batch: list[Path] = []
        self._batch_lock = threading.Lock()
        self._batch_timer: threading.Timer | None = None

    def _should_process(self, path: Path) -> bool:
        """Check if a file should be processed."""
        if path.name.startswith("."):
            log(f"[watcher] Skipping dot-file: {path.name}", self.verbose, is_debug=True)
            return False

        if path.suffix.lower() != ".png":
            return False

        if _SMART_NAME_PATTERN.match(path.name):
            log(f"[watcher] Skipping already-renamed: {path.name}", self.verbose, is_debug=True)
            return False

        return True

    def _current_default_paste_mode(self) -> str:
        value = self.default_paste_mode() if callable(self.default_paste_mode) else self.default_paste_mode
        return normalize_paste_mode(value)

    def _current_ssh_host_hint(self) -> str | None:
        value = self.ssh_host_hint() if callable(self.ssh_host_hint) else self.ssh_host_hint
        return value or None

    def _report_local_image_ready(self, path: Path, smart_name: str) -> None:
        if self.on_local_image_ready is None:
            return
        try:
            self.on_local_image_ready(path, smart_name)
        except Exception as e:
            log(
                f"[watcher] Failed reporting local image ready for {path.name}: {e}",
                self.verbose,
                is_debug=True,
            )

    def _enqueue(self, path: Path) -> None:
        """Add a file to the batch and reset the flush timer."""
        log(f"[watcher] New screenshot detected: {path.name}", self.verbose, is_debug=True)

        # Expose the original path immediately, then update after any smart rename.
        self._report_local_image_ready(path, path.name)

        with self._batch_lock:
            self._batch.append(path)

            # Reset the timer — wait for more files before flushing
            if self._batch_timer is not None:
                self._batch_timer.cancel()
            self._batch_timer = threading.Timer(_BATCH_WINDOW, self._flush_batch)
            self._batch_timer.start()

    def _flush_batch(self) -> None:
        """Process all queued screenshots and put all URLs on clipboard."""
        with self._batch_lock:
            batch = list(self._batch)
            self._batch.clear()
            self._batch_timer = None

        if not batch:
            return

        count = len(batch)
        log(f"[batch] Processing {count} screenshot{'s' if count > 1 else ''}...")

        results: list[ScreenshotResult] = []
        names: list[str] = []
        for path in batch:
            # Wait briefly for file to be fully written
            time.sleep(0.3)
            if not path.exists() or path.stat().st_size == 0:
                log(f"[batch] File disappeared or empty: {path.name}", self.verbose, is_debug=True)
                continue

            result = process_screenshot(
                path,
                bucket=self.bucket,
                prefix=self.prefix,
                expiry=self.expiry,
                no_describe=self.no_describe,
                no_ocr=self.no_ocr,
                no_rename=self.no_rename,
                no_notify=True,  # suppress per-file clipboard+notification; batch handles it
                verbose=self.verbose,
                default_paste_mode=self._current_default_paste_mode(),
                ssh_host_hint=self._current_ssh_host_hint(),
                on_local_image_ready=self._report_local_image_ready,
            )
            if result.presigned_url or result.local_path.exists():
                results.append(result)
                names.append(result.smart_name)

        if not results:
            return

        paste_mode = self._current_default_paste_mode()
        clipboard_text = build_batch_clipboard_text(
            results,
            paste_mode=paste_mode,
            ssh_host_hint=self._current_ssh_host_hint(),
        )
        if not clipboard_text:
            return

        if copy_to_clipboard(clipboard_text):
            log(f"[batch] {len(results)} {paste_mode} payload{'s' if len(results) > 1 else ''} copied to clipboard")
        else:
            log(f"[batch] Failed to copy to clipboard")

        # Single notification for the batch
        if not self.no_notify:
            first_url = next((result.presigned_url for result in results if result.presigned_url), None)
            if len(results) == 1:
                send_notification("Screenshot processed", names[0], first_url)
            else:
                send_notification(
                    f"{len(results)} screenshots processed",
                    ", ".join(names),
                    first_url,  # click opens first URL when available
                )

    def drain_pending_batch(self) -> None:
        """Flush any pending timer batch before shutdown."""
        timer: threading.Timer | None = None
        with self._batch_lock:
            timer = self._batch_timer
            self._batch_timer = None

        if timer is not None:
            timer.cancel()
            timer.join()

        self._flush_batch()

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._should_process(path):
            self._enqueue(path)

    def on_moved(self, event: FileMovedEvent) -> None:
        """Handle file renames — macOS renames .Screenshot... → Screenshot... when done."""
        if event.is_directory:
            return
        path = Path(event.dest_path)
        if self._should_process(path):
            self._enqueue(path)


def process_screenshot(
    image_path: Path,
    bucket: str,
    prefix: str = "screenshots",
    expiry: int = 86400,
    no_describe: bool = False,
    no_ocr: bool = False,
    no_rename: bool = False,
    no_notify: bool = False,
    verbose: bool = False,
    default_paste_mode: str = PASTE_MODE_PATH_REF,
    ssh_host_hint: str | None = None,
    on_local_image_ready: Callable[[Path, str], None] | None = None,
) -> ScreenshotResult:
    """Process a single screenshot through the full pipeline.

    Returns a result containing the presigned URL, smart name, and final local path.
    """
    log(f"[pipeline] Processing: {image_path.name}")

    langfuse = get_langfuse_client(verbose=verbose)
    trace_id: str | None = None
    input_size_bytes: int | None = None
    input_stat_error: str | None = None
    try:
        input_size_bytes = image_path.stat().st_size
    except OSError as e:
        input_stat_error = str(e)

    description: str | None = None
    enrich_context: dict[str, Any] = {}
    smart_name = image_path.name
    upload_ok = False
    url: str | None = None
    s3_key: str | None = None
    rename_error: str | None = None
    rename_status = "skipped"
    rename_collision_count = 0
    upload_diagnostics: dict[str, Any] = {}
    presign_diagnostics: dict[str, Any] = {}
    history_entry_id: int | None = None
    history_error: str | None = None

    with start_span(
        langfuse,
        name="screenshot-pipeline",
        input={
            "file_name": image_path.name,
            "file_path": str(image_path),
            "file_size_bytes": input_size_bytes,
            "bucket": bucket,
            "prefix": prefix,
            "expiry": expiry,
            "flags": {
                "no_describe": no_describe,
                "no_ocr": no_ocr,
                "no_rename": no_rename,
                "no_notify": no_notify,
            },
        },
        metadata={
            "component": "watcher.process_screenshot",
            "input_stat_error": input_stat_error,
        },
        verbose=verbose,
    ) as pipeline_span:
        with start_span(
            langfuse,
            name="enrich-screenshot",
            input={
                "file_path": str(image_path),
                "flags": {
                    "no_describe": no_describe,
                    "no_ocr": no_ocr,
                },
            },
            verbose=verbose,
        ) as enrich_span:
            description, enrich_context = enrich_screenshot_with_context(
                image_path,
                no_describe=no_describe,
                no_ocr=no_ocr,
                verbose=verbose,
            )

            update_observation(
                enrich_span,
                verbose=verbose,
                output={
                    "description": description,
                    "strategy": enrich_context.get("strategy"),
                },
                metadata={
                    "apple_fm": enrich_context.get("apple_fm"),
                    "ocr": enrich_context.get("ocr"),
                },
            )

        log(f"[pipeline] Description: {description or '(none)'}", verbose, is_debug=True)

        with start_span(
            langfuse,
            name="build-smart-filename",
            input={"description": description},
            verbose=verbose,
        ) as filename_span:
            smart_name = build_filename(description)
            update_observation(
                filename_span,
                verbose=verbose,
                output={"smart_name": smart_name},
            )
        log(f"[pipeline] Smart name: {smart_name}", verbose, is_debug=True)

        with start_span(
            langfuse,
            name="rename-local-file",
            input={
                "source_path": str(image_path),
                "requested_name": smart_name,
                "rename_enabled": not no_rename,
            },
            verbose=verbose,
        ) as rename_span:
            if not no_rename:
                new_path = image_path.parent / smart_name
                if new_path.exists():
                    stem = new_path.stem
                    suffix = new_path.suffix
                    counter = 1
                    while new_path.exists():
                        new_path = image_path.parent / f"{stem}-{counter}{suffix}"
                        counter += 1
                    rename_collision_count = counter - 1
                try:
                    image_path.rename(new_path)
                    log(f"[pipeline] Renamed: {image_path.name} → {new_path.name}")
                    image_path = new_path
                    smart_name = new_path.name
                    rename_status = "renamed"
                except OSError as e:
                    rename_error = str(e)
                    rename_status = "rename_failed"
                    log(f"[pipeline] Rename failed: {e}, continuing with original name")
            else:
                smart_name = image_path.name
                rename_status = "rename_skipped"

            update_observation(
                rename_span,
                verbose=verbose,
                output={
                    "status": rename_status,
                    "final_name": smart_name,
                    "final_path": str(image_path),
                    "collision_count": rename_collision_count,
                },
                metadata={"error": rename_error},
            )

        if on_local_image_ready is not None:
            try:
                on_local_image_ready(image_path, smart_name)
            except Exception as e:
                log(f"[watcher] Failed updating local image ready path: {e}", verbose, is_debug=True)

        with start_span(
            langfuse,
            name="upload-to-s3",
            input={
                "local_path": str(image_path),
                "bucket": bucket,
                "prefix": prefix,
                "file_name": smart_name,
            },
            verbose=verbose,
        ) as upload_span:
            s3_key = s3_key_for_file(smart_name, prefix)
            upload_ok = upload_to_s3(
                image_path,
                s3_key,
                bucket,
                verbose,
                diagnostics=upload_diagnostics,
            )
            update_observation(
                upload_span,
                verbose=verbose,
                output={
                    "uploaded": upload_ok,
                    "s3_key": s3_key,
                },
                metadata=upload_diagnostics,
            )

        if upload_ok:
            with start_span(
                langfuse,
                name="generate-presigned-url",
                input={
                    "bucket": bucket,
                    "s3_key": s3_key,
                    "expiry": expiry,
                },
                verbose=verbose,
            ) as presigned_span:
                url = generate_presigned_url(
                    bucket,
                    str(s3_key),
                    expiry,
                    verbose,
                    diagnostics=presign_diagnostics,
                )
                update_observation(
                    presigned_span,
                    verbose=verbose,
                    output={
                        "url_generated": bool(url),
                        "url": url,
                    },
                    metadata=presign_diagnostics,
                )

        if upload_ok and url and s3_key:
            try:
                history_entry_id = _get_history_store().record_upload(
                    local_path=image_path,
                    smart_name=smart_name,
                    bucket=bucket,
                    s3_key=str(s3_key),
                    presigned_url=url,
                    presigned_expiry_seconds=expiry,
                )
            except Exception as e:
                history_error = str(e)
                log(f"[history] Failed to save upload history: {e}", verbose=True)

        clipboard_copied: bool | None = None
        notification_sent: bool | None = None
        if not no_notify:
            with start_span(
                langfuse,
                name="copy-payload-and-notify",
                input={"url_present": bool(url), "default_paste_mode": default_paste_mode},
                verbose=verbose,
            ) as notify_span:
                result_for_clipboard = ScreenshotResult(
                    presigned_url=url,
                    smart_name=smart_name,
                    local_path=image_path,
                    bucket=bucket,
                    s3_key=s3_key,
                )
                clipboard_text = build_batch_clipboard_text(
                    [result_for_clipboard],
                    paste_mode=default_paste_mode,
                    ssh_host_hint=ssh_host_hint,
                )
                if clipboard_text:
                    clipboard_copied = copy_to_clipboard(clipboard_text)
                    if clipboard_copied:
                        log(
                            f"[pipeline] {normalize_paste_mode(default_paste_mode)} "
                            "payload copied to clipboard"
                        )
                    notification_sent = send_notification("Screenshot processed", smart_name, url)
                else:
                    clipboard_copied = False
                    notification_sent = False
                update_observation(
                    notify_span,
                    verbose=verbose,
                    output={
                        "clipboard_copied": clipboard_copied,
                        "notification_sent": notification_sent,
                    },
                )

        pipeline_status = "success"
        if not upload_ok:
            pipeline_status = "upload_failed"
        elif not url:
            pipeline_status = "presigned_url_failed"

        update_observation(
            pipeline_span,
            verbose=verbose,
            output={
                "status": pipeline_status,
                "description": description,
                "smart_name": smart_name,
                "s3_key": s3_key,
                "url": url,
                "clipboard_copied": clipboard_copied,
                "notification_sent": notification_sent,
                "history_entry_id": history_entry_id,
            },
            metadata={
                "enrich_context": enrich_context,
                "upload": upload_diagnostics,
                "presign": presign_diagnostics,
                "history_error": history_error,
            },
        )
        trace_id = get_trace_id(pipeline_span)

    trace_url = get_trace_url(langfuse, trace_id)
    if trace_url:
        log(f"[trace] Langfuse trace: {trace_url}", verbose, is_debug=True)

    if not upload_ok:
        log(f"[pipeline] Upload failed for {image_path.name}")
        return ScreenshotResult(
            presigned_url=None,
            smart_name=smart_name,
            local_path=image_path,
            bucket=bucket,
            s3_key=s3_key,
        )

    if not url:
        log(f"[pipeline] Presigned URL generation failed")
        return ScreenshotResult(
            presigned_url=None,
            smart_name=smart_name,
            local_path=image_path,
            bucket=bucket,
            s3_key=s3_key,
        )

    # Print URL
    log(url)

    return ScreenshotResult(
        presigned_url=url,
        smart_name=smart_name,
        local_path=image_path,
        bucket=bucket,
        s3_key=s3_key,
    )


def start_watching(
    watch_dir: Path,
    bucket: str,
    prefix: str = "screenshots",
    expiry: int = 86400,
    no_describe: bool = False,
    no_ocr: bool = False,
    no_rename: bool = False,
    no_notify: bool = False,
    verbose: bool = False,
    stop_event: threading.Event | None = None,
    default_paste_mode: str | Callable[[], str] = PASTE_MODE_PATH_REF,
    ssh_host_hint: str | Callable[[], str | None] | None = None,
    on_local_image_ready: Callable[[Path, str], None] | None = None,
) -> None:
    """Start watching a directory for new screenshots. Blocks until interrupted."""
    watch_dir.mkdir(parents=True, exist_ok=True)

    handler = ScreenshotHandler(
        bucket=bucket,
        prefix=prefix,
        expiry=expiry,
        no_describe=no_describe,
        no_ocr=no_ocr,
        no_rename=no_rename,
        no_notify=no_notify,
        verbose=verbose,
        default_paste_mode=default_paste_mode,
        ssh_host_hint=ssh_host_hint,
        on_local_image_ready=on_local_image_ready,
    )

    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()

    log(f"[watcher] Watching {watch_dir} for new screenshots...")
    log(f"[watcher] Bucket: {bucket}, prefix: {prefix}, expiry: {expiry}s")
    log(f"[watcher] Press Ctrl+C to stop")

    try:
        while observer.is_alive():
            if stop_event is not None and stop_event.is_set():
                log("[watcher] Stop signal received")
                observer.stop()
                break
            observer.join(timeout=1)
    except KeyboardInterrupt:
        log("\n[watcher] Stopping...")
        observer.stop()

    observer.join()
    handler.drain_pending_batch()
    # Flush once on shutdown so long-running watch mode stays non-blocking per screenshot.
    flush_langfuse(get_langfuse_client(verbose=verbose), verbose=verbose)
    log("[watcher] Stopped.")
