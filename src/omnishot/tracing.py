"""Langfuse tracing helpers with graceful fallback when not configured."""

import contextlib
import os
from pathlib import Path
from time import time_ns
from typing import Any, Iterator

from .notify import log

_ENV_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
_STATIC_ENV_FILE_CANDIDATES = (
    Path(__file__).resolve().parent.parent.parent / ".env",
    Path.home() / "pro" / "omnishot" / ".env",
)

_LANGFUSE_CLIENT: Any | None = None
_LANGFUSE_INIT_ATTEMPTED = False


def _load_env_file(path: Path, verbose: bool = False) -> bool:
    """Load KEY=VALUE lines from an env file without overriding existing vars."""
    try:
        if not path.exists():
            return False
    except OSError as e:
        log(f"[trace] Unable to stat env file '{path}': {e}", verbose, is_debug=True)
        return False

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        log(f"[trace] Unable to read env file '{path}': {e}", verbose, is_debug=True)
        return False

    loaded_any = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        os.environ[key] = value
        loaded_any = True

    return loaded_any


def _ensure_langfuse_env(verbose: bool = False) -> None:
    """Load Langfuse env vars from likely .env locations if missing."""
    if all(os.getenv(key) for key in _ENV_KEYS):
        return

    candidates: list[Path] = []
    try:
        candidates.append(Path.cwd() / ".env")
    except OSError as e:
        log(f"[trace] Unable to resolve current working directory: {e}", verbose, is_debug=True)
    candidates.extend(_STATIC_ENV_FILE_CANDIDATES)

    seen: set[Path] = set()
    loaded_from: list[Path] = []
    for candidate in candidates:
        expanded = candidate.expanduser()
        resolved = expanded.resolve() if expanded.exists() else expanded
        if resolved in seen:
            continue
        seen.add(resolved)
        if _load_env_file(resolved, verbose=verbose):
            loaded_from.append(resolved)

    if loaded_from:
        joined = ", ".join(str(path) for path in loaded_from)
        log(f"[trace] Loaded env vars from: {joined}", verbose, is_debug=True)


def get_langfuse_client(verbose: bool = False) -> Any | None:
    """Return initialized Langfuse client, or None if unavailable."""
    global _LANGFUSE_CLIENT, _LANGFUSE_INIT_ATTEMPTED

    if _LANGFUSE_INIT_ATTEMPTED:
        return _LANGFUSE_CLIENT

    _LANGFUSE_INIT_ATTEMPTED = True
    _ensure_langfuse_env(verbose=verbose)

    missing = [key for key in _ENV_KEYS if not os.getenv(key)]
    if missing:
        log(
            f"[trace] Langfuse disabled; missing env vars: {', '.join(missing)}",
            verbose,
            is_debug=True,
        )
        return None

    try:
        # Import after env is loaded so client picks up credentials correctly.
        from langfuse import get_client  # type: ignore
    except Exception as e:
        log(f"[trace] Langfuse import failed: {e}", verbose, is_debug=True)
        return None

    try:
        _LANGFUSE_CLIENT = get_client()
        log("[trace] Langfuse tracing enabled", verbose, is_debug=True)
        return _LANGFUSE_CLIENT
    except Exception as e:
        log(f"[trace] Langfuse init failed: {e}", verbose, is_debug=True)
        _LANGFUSE_CLIENT = None
        return None


@contextlib.contextmanager
def start_span(
    langfuse: Any | None,
    *,
    name: str,
    input: Any | None = None,
    output: Any | None = None,
    metadata: Any | None = None,
    verbose: bool = False,
) -> Iterator[Any | None]:
    """Start a nested span if Langfuse is configured."""
    if langfuse is None:
        yield None
        return

    kwargs: dict[str, Any] = {"name": name}
    if input is not None:
        kwargs["input"] = input
    if output is not None:
        kwargs["output"] = output
    if metadata is not None:
        kwargs["metadata"] = metadata

    try:
        span_ctx = langfuse.start_as_current_span(**kwargs)
    except Exception as e:
        log(f"[trace] Failed to start span '{name}': {e}", verbose, is_debug=True)
        yield None
        return

    body_error: BaseException | None = None
    entered = False
    try:
        with span_ctx as span:
            entered = True
            try:
                yield span
            except BaseException as e:
                body_error = e
                raise
    except Exception as e:
        if body_error is not None:
            # Keep caller errors primary if tracer teardown also fails.
            if e is body_error:
                raise
            raise body_error from e
        if not entered:
            log(f"[trace] Failed to enter span '{name}': {e}", verbose, is_debug=True)
            yield None
            return
        log(f"[trace] Failed to close span '{name}': {e}", verbose, is_debug=True)


@contextlib.contextmanager
def start_generation(
    langfuse: Any | None,
    *,
    name: str,
    model: str,
    input: Any | None = None,
    metadata: Any | None = None,
    verbose: bool = False,
) -> Iterator[Any | None]:
    """Start a nested generation if Langfuse is configured."""
    if langfuse is None:
        yield None
        return

    kwargs: dict[str, Any] = {
        "name": name,
        "model": model,
    }
    if input is not None:
        kwargs["input"] = input
    if metadata is not None:
        kwargs["metadata"] = metadata

    try:
        generation_ctx = langfuse.start_as_current_generation(**kwargs)
    except Exception as e:
        log(f"[trace] Failed to start generation '{name}': {e}", verbose, is_debug=True)
        yield None
        return

    body_error: BaseException | None = None
    entered = False
    try:
        with generation_ctx as generation:
            entered = True
            try:
                yield generation
            except BaseException as e:
                body_error = e
                raise
    except Exception as e:
        if body_error is not None:
            # Keep caller errors primary if tracer teardown also fails.
            if e is body_error:
                raise
            raise body_error from e
        if not entered:
            log(f"[trace] Failed to enter generation '{name}': {e}", verbose, is_debug=True)
            yield None
            return
        log(f"[trace] Failed to close generation '{name}': {e}", verbose, is_debug=True)


def update_observation(observation: Any | None, *, verbose: bool = False, **kwargs: Any) -> None:
    """Update span/generation safely without breaking the pipeline."""
    if observation is None or not kwargs:
        return
    try:
        observation.update(**kwargs)
    except Exception as e:
        log(f"[trace] Observation update failed: {e}", verbose, is_debug=True)


def emit_timed_span(
    langfuse: Any | None,
    *,
    name: str,
    duration_ms: int | float | None,
    start_timestamp_ns: int | None = None,
    end_timestamp_ns: int | None = None,
    input: Any | None = None,
    output: Any | None = None,
    metadata: Any | None = None,
    verbose: bool = False,
) -> None:
    """Emit a span with an explicit duration when available.

    Uses Langfuse internals to backdate the span start time for accurate latency in UI.
    Falls back to a regular instantaneous span when timed emission fails.
    """
    if langfuse is None:
        return

    try:
        duration_ms_int = int(duration_ms) if duration_ms is not None else 0
    except (TypeError, ValueError):
        duration_ms_int = 0

    if start_timestamp_ns is None and end_timestamp_ns is None and duration_ms_int <= 0:
        with start_span(
            langfuse,
            name=name,
            input=input,
            output=output,
            metadata=metadata,
            verbose=verbose,
        ):
            pass
        return

    try:
        tracer = getattr(langfuse, "_otel_tracer", None)
        create_observation = getattr(langfuse, "_create_observation_from_otel_span", None)
        if tracer is None or not callable(create_observation):
            raise RuntimeError("timed-span-api-unavailable")

        resolved_end_ns = end_timestamp_ns if isinstance(end_timestamp_ns, int) else time_ns()
        if resolved_end_ns < 0:
            resolved_end_ns = time_ns()

        resolved_start_ns: int
        if isinstance(start_timestamp_ns, int):
            resolved_start_ns = max(0, start_timestamp_ns)
        elif duration_ms_int > 0:
            duration_ns = duration_ms_int * 1_000_000
            resolved_start_ns = max(0, resolved_end_ns - duration_ns)
        else:
            resolved_start_ns = resolved_end_ns

        if resolved_end_ns < resolved_start_ns:
            resolved_end_ns = resolved_start_ns

        otel_span = tracer.start_span(name=name, start_time=resolved_start_ns)
        span = create_observation(
            otel_span=otel_span,
            as_type="span",
            input=input,
            output=output,
            metadata=metadata,
            version=None,
            level=None,
            status_message=None,
        )
        span.end(end_time=resolved_end_ns)
    except Exception as e:
        log(
            f"[trace] Timed span emission failed for '{name}': {e}; falling back to regular span",
            verbose,
            is_debug=True,
        )
        with start_span(
            langfuse,
            name=name,
            input=input,
            output=output,
            metadata=metadata,
            verbose=verbose,
        ):
            pass


def flush_langfuse(langfuse: Any | None, verbose: bool = False) -> None:
    """Flush Langfuse events in short-lived commands."""
    if langfuse is None:
        return
    try:
        langfuse.flush()
    except Exception as e:
        log(f"[trace] Flush failed: {e}", verbose, is_debug=True)


def get_trace_id(observation: Any | None) -> str | None:
    """Extract trace id from a Langfuse observation object if available."""
    trace_id = getattr(observation, "trace_id", None)
    return trace_id if isinstance(trace_id, str) and trace_id else None


def get_trace_url(langfuse: Any | None, trace_id: str | None) -> str | None:
    """Get a Langfuse trace URL when SDK exposes helper and trace id exists."""
    if langfuse is None or not trace_id:
        return None

    getter = getattr(langfuse, "get_trace_url", None)
    if not callable(getter):
        return None

    try:
        url = getter(trace_id)
    except Exception:
        return None
    return url if isinstance(url, str) and url else None
