"""Local upload history persistence for omnishot."""

from __future__ import annotations

import os
import socket
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_HISTORY_DB_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "omnishot"
    / "history.db"
)


def _read_botfiles_system_name() -> str | None:
    botfiles_root = Path(os.getenv("BOTFILES_ROOT") or Path.home() / "pro" / "botfiles")
    machine_rc = botfiles_root / "secrets" / "local" / "machine.rc"
    try:
        lines = machine_rc.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        if not stripped.startswith("SYSTEM_NAME="):
            continue
        value = stripped.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value or None
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _from_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def resolve_machine_name() -> str:
    configured = os.getenv("SYSTEM_NAME")
    if configured:
        return configured
    botfiles_system_name = _read_botfiles_system_name()
    if botfiles_system_name:
        return botfiles_system_name
    hostname = socket.gethostname()
    return hostname or "unknown"


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    created_at_utc: datetime
    machine_name: str
    local_path: str
    smart_name: str
    bucket: str
    s3_key: str
    last_presigned_url: str | None
    last_presigned_generated_at_utc: datetime | None
    last_presigned_expiry_seconds: int | None
    is_public: bool
    public_url: str | None
    last_accessed_at_utc: datetime | None


def presigned_url_remaining_seconds(entry: HistoryEntry, *, now: datetime | None = None) -> int | None:
    """Return remaining seconds for the stored presigned URL."""
    if entry.last_presigned_generated_at_utc is None:
        return None
    if entry.last_presigned_expiry_seconds is None:
        return None
    current = now.astimezone(timezone.utc) if now else _utc_now()
    expiry_at = entry.last_presigned_generated_at_utc + timedelta(seconds=entry.last_presigned_expiry_seconds)
    return int((expiry_at - current).total_seconds())


def should_regenerate_presigned_url(
    entry: HistoryEntry,
    *,
    refresh_threshold_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Regenerate when URL is missing or expiry is below threshold."""
    if not entry.last_presigned_url:
        return True
    remaining = presigned_url_remaining_seconds(entry, now=now)
    if remaining is None:
        return True
    return remaining <= refresh_threshold_seconds


class HistoryStore:
    """SQLite-backed storage for uploaded screenshot metadata."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = (db_path or DEFAULT_HISTORY_DB_PATH).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS screenshot_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at_utc TEXT NOT NULL,
                        machine_name TEXT NOT NULL,
                        local_path TEXT NOT NULL,
                        smart_name TEXT NOT NULL,
                        bucket TEXT NOT NULL,
                        s3_key TEXT NOT NULL,
                        last_presigned_url TEXT,
                        last_presigned_generated_at_utc TEXT,
                        last_presigned_expiry_seconds INTEGER,
                        is_public INTEGER NOT NULL DEFAULT 0,
                        public_url TEXT,
                        last_accessed_at_utc TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_screenshot_history_created_at
                    ON screenshot_history(created_at_utc DESC, id DESC)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_screenshot_history_bucket_key
                    ON screenshot_history(bucket, s3_key)
                    """
                )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> HistoryEntry:
        return HistoryEntry(
            id=int(row["id"]),
            created_at_utc=_from_utc_iso(row["created_at_utc"]) or _utc_now(),
            machine_name=str(row["machine_name"]),
            local_path=str(row["local_path"]),
            smart_name=str(row["smart_name"]),
            bucket=str(row["bucket"]),
            s3_key=str(row["s3_key"]),
            last_presigned_url=row["last_presigned_url"],
            last_presigned_generated_at_utc=_from_utc_iso(row["last_presigned_generated_at_utc"]),
            last_presigned_expiry_seconds=row["last_presigned_expiry_seconds"],
            is_public=bool(row["is_public"]),
            public_url=row["public_url"],
            last_accessed_at_utc=_from_utc_iso(row["last_accessed_at_utc"]),
        )

    def record_upload(
        self,
        *,
        local_path: Path,
        smart_name: str,
        bucket: str,
        s3_key: str,
        presigned_url: str | None,
        presigned_expiry_seconds: int | None,
        machine_name: str | None = None,
    ) -> int:
        now = _utc_now()
        now_iso = _to_utc_iso(now)
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO screenshot_history (
                        created_at_utc,
                        machine_name,
                        local_path,
                        smart_name,
                        bucket,
                        s3_key,
                        last_presigned_url,
                        last_presigned_generated_at_utc,
                        last_presigned_expiry_seconds,
                        last_accessed_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_iso,
                        machine_name or resolve_machine_name(),
                        str(local_path),
                        smart_name,
                        bucket,
                        s3_key,
                        presigned_url,
                        now_iso if presigned_url else None,
                        presigned_expiry_seconds,
                        now_iso if presigned_url else None,
                    ),
                )
                return int(cur.lastrowid)

    def get_entry(self, entry_id: int) -> HistoryEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM screenshot_history WHERE id = ?",
                (entry_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def list_recent(self, *, limit: int = 20) -> list[HistoryEntry]:
        bounded_limit = max(1, min(limit, 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM screenshot_history
                ORDER BY created_at_utc DESC, id DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def update_presigned_link(
        self,
        *,
        entry_id: int,
        presigned_url: str,
        expiry_seconds: int,
        generated_at_utc: datetime | None = None,
    ) -> None:
        generated = generated_at_utc.astimezone(timezone.utc) if generated_at_utc else _utc_now()
        generated_iso = _to_utc_iso(generated)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE screenshot_history
                    SET
                        last_presigned_url = ?,
                        last_presigned_generated_at_utc = ?,
                        last_presigned_expiry_seconds = ?,
                        last_accessed_at_utc = ?
                    WHERE id = ?
                    """,
                    (presigned_url, generated_iso, expiry_seconds, generated_iso, entry_id),
                )

    def mark_public(self, *, entry_id: int, public_url: str) -> None:
        now_iso = _to_utc_iso(_utc_now())
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE screenshot_history
                    SET
                        is_public = 1,
                        public_url = ?,
                        last_accessed_at_utc = ?
                    WHERE id = ?
                    """,
                    (public_url, now_iso, entry_id),
                )
