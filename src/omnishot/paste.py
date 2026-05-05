"""Paste-mode config and clipboard payload helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .history import resolve_machine_name

APP_SUPPORT_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "omnishot"
)
DEFAULT_CONFIG_PATH = APP_SUPPORT_DIR / "config.json"

PASTE_MODE_PATH_REF = "path-ref"
PASTE_MODE_LOCAL_AGENT = PASTE_MODE_PATH_REF
PASTE_MODE_S3_URL = "s3-url"
VALID_PASTE_MODES = (PASTE_MODE_PATH_REF, PASTE_MODE_S3_URL)
DEFAULT_PASTE_MODE = PASTE_MODE_PATH_REF

ENV_DEFAULT_PASTE_MODE = "OMNISHOT_DEFAULT_PASTE_MODE"
ENV_SSH_HOST_HINT = "OMNISHOT_SSH_HOST_HINT"
ENV_MACHINE_SSH_ALIAS = "BOT_MACHINE_SSH_ALIAS"


@dataclass(frozen=True, slots=True)
class AppConfig:
    default_paste_mode: str = DEFAULT_PASTE_MODE
    ssh_host_hint: str | None = None


@dataclass(frozen=True, slots=True)
class MachineMetadata:
    machine: str


def normalize_paste_mode(value: str | None, *, fallback: str = DEFAULT_PASTE_MODE) -> str:
    """Return a supported paste mode, falling back for empty values."""
    if value is None or value == "":
        return fallback
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {
        "path-ref",
        "pathref",
        "path",
        "file-ref",
        "fileref",
        "machine-path",
        "machinepath",
        "local",
        "agent",
        "local-path",
        "local-agent",
        "local-agent-payload",
    }:
        return PASTE_MODE_PATH_REF
    if normalized in {"url", "s3", "s3_url", "s3-url", "presigned", "presigned-url"}:
        return PASTE_MODE_S3_URL
    if normalized in VALID_PASTE_MODES:
        return normalized
    raise ValueError(
        f"unsupported paste mode: {value!r}; expected one of {', '.join(VALID_PASTE_MODES)}"
    )


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_app_config(config_path: Path | None = None) -> AppConfig:
    """Load persisted local app config, tolerating missing or malformed files."""
    path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    if not path.exists():
        return AppConfig()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()

    if not isinstance(data, dict):
        return AppConfig()

    default_mode = DEFAULT_PASTE_MODE
    try:
        default_mode = normalize_paste_mode(_clean_optional_text(data.get("default_paste_mode")))
    except ValueError:
        default_mode = DEFAULT_PASTE_MODE

    return AppConfig(
        default_paste_mode=default_mode,
        ssh_host_hint=_clean_optional_text(data.get("ssh_host_hint")),
    )


def save_app_config(config: AppConfig, config_path: Path | None = None) -> None:
    """Persist local app config under Application Support."""
    path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "default_paste_mode": normalize_paste_mode(config.default_paste_mode),
        "ssh_host_hint": config.ssh_host_hint,
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_runtime_config(
    *,
    default_paste_mode: str | None = None,
    ssh_host_hint: str | None = None,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AppConfig:
    """Resolve runtime config with CLI values first, then env, then persisted config."""
    environ = env if env is not None else os.environ
    persisted = load_app_config(config_path)

    mode_source = (
        default_paste_mode
        or _clean_optional_text(environ.get(ENV_DEFAULT_PASTE_MODE))
        or persisted.default_paste_mode
    )
    hint_source = (
        _clean_optional_text(ssh_host_hint)
        or _clean_optional_text(environ.get(ENV_SSH_HOST_HINT))
        or persisted.ssh_host_hint
    )

    return AppConfig(
        default_paste_mode=normalize_paste_mode(mode_source),
        ssh_host_hint=hint_source,
    )


def resolve_machine_metadata(*, ssh_host_hint: str | None = None) -> MachineMetadata:
    """Resolve the compact machine token for path-ref payloads."""
    machine = (
        _clean_optional_text(ssh_host_hint)
        or _clean_optional_text(os.getenv(ENV_MACHINE_SSH_ALIAS))
        or resolve_machine_name()
    )
    return MachineMetadata(
        machine=machine,
    )


def build_path_ref_payload(
    *,
    local_path: Path,
    smart_name: str | None = None,
    ssh_host_hint: str | None = None,
    machine_metadata: MachineMetadata | None = None,
) -> str:
    """Build compact text that lets local or remote coding agents retrieve a screenshot."""
    metadata = machine_metadata or resolve_machine_metadata(ssh_host_hint=ssh_host_hint)
    path = local_path.expanduser().resolve(strict=False)
    lines = [
        "screenshot-info:",
        f"  machine: {metadata.machine}",
        f"  path: {path}",
    ]
    return "\n".join(lines)


def build_local_agent_payload(
    *,
    local_path: Path,
    smart_name: str | None = None,
    ssh_host_hint: str | None = None,
    machine_metadata: MachineMetadata | None = None,
) -> str:
    """Backward-compatible alias for the path-ref payload builder."""
    return build_path_ref_payload(
        local_path=local_path,
        smart_name=smart_name,
        ssh_host_hint=ssh_host_hint,
        machine_metadata=machine_metadata,
    )
