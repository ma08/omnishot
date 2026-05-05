"""Image description (Apple FM + OCR fallback) and smart filename generation."""

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import time_ns
from typing import Any

from .notify import log
from .tracing import emit_timed_span, get_langfuse_client, start_generation, start_span, update_observation

# Path to compiled Swift helper (relative to project root)
_SWIFT_BINARY = Path(__file__).resolve().parent.parent.parent / "swift" / ".build" / "release" / "DescribeImage"
_SLUG_POLICY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,7}$")
_SLUG_CANDIDATE_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+){2,7}")


def _is_slug_policy_match(value: str) -> bool:
    return bool(_SLUG_POLICY_PATTERN.fullmatch(value))


def _slugify_text(value: str, max_words: int = 8) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        return ""
    words = slug.split("-")
    return "-".join(words[:max_words])


def normalize_apple_fm_description_with_context(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize Apple FM output into a stable slug-like description."""
    candidates: list[tuple[str, str]] = []
    for key in ("structured_slug", "raw_output_unparsed", "raw_output", "description"):
        value = data.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                candidates.append((key, stripped.lower()))

    for source, lowered in candidates:
        if _is_slug_policy_match(lowered):
            return {
                "description": lowered,
                "format_valid": True,
                "normalization_source": f"{source}:exact_match",
            }

    for source, lowered in candidates:
        matches = _SLUG_CANDIDATE_PATTERN.findall(lowered)
        if matches:
            best = max(matches, key=len)
            return {
                "description": best,
                "format_valid": True,
                "normalization_source": f"{source}:regex_extract",
            }

    for source, lowered in candidates:
        slugified = _slugify_text(lowered)
        if slugified:
            return {
                "description": slugified,
                "format_valid": _is_slug_policy_match(slugified),
                "normalization_source": f"{source}:slugify",
            }

    return {
        "description": None,
        "format_valid": False,
        "normalization_source": "none",
    }


def build_apple_fm_observability_spans_with_context(
    data: dict[str, Any],
    *,
    image_path: Path,
    description: str,
    format_valid: bool | None,
    normalization_source: str | None,
) -> dict[str, Any]:
    """Build structured/fallback span payloads for Apple FM trace observability."""
    timing = data.get("timing") if isinstance(data.get("timing"), dict) else None
    structured_duration_ms = data.get("structured_generation_duration_ms")
    if not isinstance(structured_duration_ms, (int, float)) and isinstance(timing, dict):
        structured_duration_ms = timing.get("model_duration_ms")

    chain_of_thought = data.get("structured_chain_of_thought") or data.get("chain_of_thought")
    structured_span = {
        "name": "apple-foundation-model-structured-generation",
        "duration_ms": structured_duration_ms,
        "input": {
            "image_path": str(image_path),
            "prompt": data.get("prompt"),
            "instructions": data.get("instructions"),
            "ocr_text": data.get("ocr_text"),
            "content_keywords": data.get("content_keywords"),
        },
        "output": {
            "chain_of_thought": chain_of_thought,
            "structured_generation_attempted": data.get("structured_generation_attempted"),
            "structured_generation_succeeded": data.get("structured_generation_succeeded"),
            "structured_slug": data.get("structured_slug"),
            "structured_chain_of_thought": chain_of_thought,
            "structured_output_json": data.get("structured_output_json"),
            "raw_output_unparsed": data.get("raw_output_unparsed"),
        },
        "metadata": {
            "source": "swift.DescribeImage",
            "response_mode": data.get("response_mode"),
            "output_contract_version": data.get("output_contract_version"),
            "prompt_version": data.get("prompt_version"),
            "structured_generation_strategy": data.get("structured_generation_strategy"),
            "structured_generation_error": data.get("structured_generation_error"),
            "structured_generation_error_type": data.get("structured_generation_error_type"),
            "structured_generation_duration_ms": data.get("structured_generation_duration_ms"),
            "timing": timing,
        },
    }

    fallback_span: dict[str, Any] | None = None
    if data.get("response_mode") == "legacy_text_fallback":
        fallback_span = {
            "name": "apple-foundation-model-legacy-fallback",
            "duration_ms": data.get("fallback_generation_duration_ms"),
            "input": {
                "image_path": str(image_path),
                "prompt": data.get("fallback_prompt") or data.get("prompt"),
                "instructions": data.get("instructions"),
                "content_keywords": data.get("content_keywords"),
            },
            "output": {
                "chain_of_thought": data.get("chain_of_thought"),
                "raw_output_unparsed": data.get("raw_output_unparsed"),
                "raw_output": data.get("raw_output"),
                "parsed_description": description or None,
                "format_valid": format_valid,
                "normalization_source": normalization_source,
            },
            "metadata": {
                "source": "swift.DescribeImage",
                "prompt_version": data.get("prompt_version"),
                "fallback_generation_duration_ms": data.get("fallback_generation_duration_ms"),
                "structured_generation_error": data.get("structured_generation_error"),
                "structured_generation_error_type": data.get("structured_generation_error_type"),
                "timing": timing,
            },
        }

    return {
        "structured_span": structured_span,
        "fallback_span": fallback_span,
    }


def describe_with_apple_fm(image_path: Path, verbose: bool = False) -> dict[str, Any] | None:
    """Describe image using Vision OCR + Apple Foundation Models via Swift helper.

    Returns structured metadata (description, prompt, raw model output, etc.).
    Returns None only when Apple FM was not attempted (missing helper binary).
    """
    if not _SWIFT_BINARY.exists():
        log("[enrich] Swift helper not found, skipping Apple FM", verbose, is_debug=True)
        return None

    langfuse = get_langfuse_client(verbose=verbose)
    cmd = [str(_SWIFT_BINARY), str(image_path)]
    null_model_output = {
        "chain_of_thought": None,
        "raw_output": None,
        "raw_output_unparsed": None,
        "parsed_description": None,
        "keywords": None,
        "format_valid": None,
        "normalization_source": None,
        "structured_generation_attempted": None,
        "structured_generation_succeeded": None,
        "structured_generation_strategy": None,
        "structured_generation_error": None,
        "structured_generation_error_type": None,
        "structured_generation_duration_ms": None,
        "fallback_generation_duration_ms": None,
        "prompt_version": None,
        "content_keywords": None,
    }
    with start_generation(
        langfuse,
        name="apple-foundation-model-generation",
        model="apple-foundation-models",
        input={"image_path": str(image_path)},
        metadata={"source": "swift.DescribeImage"},
        verbose=verbose,
    ) as generation:
        result: subprocess.CompletedProcess[str] | None = None
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log(f"[enrich] Apple FM error: {e}", verbose, is_debug=True)
            error_payload = {
                "description": None,
                "error": "apple-fm-exception",
                "exception": str(e),
            }
            update_observation(
                generation,
                verbose=verbose,
                output=null_model_output,
                metadata={
                    "source": "swift.DescribeImage",
                    "error": "apple-fm-exception",
                    "exception": str(e),
                    "exception_type": type(e).__name__,
                },
            )
            return error_payload

        if result.returncode != 0:
            stderr = result.stderr.strip()
            log(f"[enrich] Swift helper failed: {stderr}", verbose, is_debug=True)
            error_payload = {
                "description": None,
                "error": "swift-helper-failed",
                "stderr": stderr,
                "exit_code": result.returncode,
            }
            update_observation(
                generation,
                verbose=verbose,
                output=null_model_output,
                metadata={
                    "source": "swift.DescribeImage",
                    "error": "swift-helper-failed",
                    "stderr": stderr,
                    "exit_code": result.returncode,
                },
            )
            return error_payload

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            log(f"[enrich] Apple FM error: {e}", verbose, is_debug=True)
            error_payload = {
                "description": None,
                "error": "apple-fm-exception",
                "exception": str(e),
            }
            stderr = result.stderr.strip() if result.stderr else None
            stdout = result.stdout if result.stdout else None
            update_observation(
                generation,
                verbose=verbose,
                output=null_model_output,
                metadata={
                    "source": "swift.DescribeImage",
                    "error": "apple-fm-exception",
                    "exception": str(e),
                    "exception_type": type(e).__name__,
                    "stderr": stderr,
                    "stdout": stdout,
                },
            )
            return error_payload

        normalized = normalize_apple_fm_description_with_context(data)
        normalized_description = normalized.get("description")
        description = normalized_description if isinstance(normalized_description, str) else ""
        format_valid = normalized.get("format_valid")
        normalization_source = normalized.get("normalization_source")
        ocr_details = data.get("ocr")
        timing_details = data.get("timing")
        model_name = str(data.get("model") or "apple-foundation-models")
        chain_of_thought = data.get("structured_chain_of_thought") or data.get("chain_of_thought")
        if not isinstance(chain_of_thought, str) or not chain_of_thought.strip():
            chain_of_thought = None
        observability_spans = build_apple_fm_observability_spans_with_context(
            data,
            image_path=image_path,
            description=description,
            format_valid=format_valid,
            normalization_source=normalization_source,
        )

        ocr_duration_ms = None
        if isinstance(ocr_details, dict) and isinstance(ocr_details.get("duration_ms"), (int, float)):
            ocr_duration_ms = ocr_details.get("duration_ms")
        elif isinstance(timing_details, dict):
            ocr_duration_ms = timing_details.get("ocr_duration_ms")

        structured_span = observability_spans.get("structured_span")
        fallback_span = observability_spans.get("fallback_span")

        def _coerce_positive_ms(value: Any) -> int | None:
            if not isinstance(value, (int, float)):
                return None
            as_int = int(value)
            return as_int if as_int > 0 else None

        ocr_duration_ms_int = _coerce_positive_ms(ocr_duration_ms)
        structured_duration_ms_int = _coerce_positive_ms(
            structured_span.get("duration_ms") if isinstance(structured_span, dict) else None
        )
        fallback_duration_ms_int = _coerce_positive_ms(
            fallback_span.get("duration_ms") if isinstance(fallback_span, dict) else None
        )

        # Backdate child spans as a contiguous sequence so timeline order matches runtime:
        # OCR -> structured generation -> optional legacy fallback.
        timeline_cursor_end_ns = time_ns()
        fallback_window: tuple[int, int] | None = None
        structured_window: tuple[int, int] | None = None
        ocr_window: tuple[int, int] | None = None

        if fallback_duration_ms_int is not None:
            fallback_duration_ns = fallback_duration_ms_int * 1_000_000
            fallback_start_ns = max(0, timeline_cursor_end_ns - fallback_duration_ns)
            fallback_window = (fallback_start_ns, timeline_cursor_end_ns)
            timeline_cursor_end_ns = fallback_start_ns

        if structured_duration_ms_int is not None:
            structured_duration_ns = structured_duration_ms_int * 1_000_000
            structured_start_ns = max(0, timeline_cursor_end_ns - structured_duration_ns)
            structured_window = (structured_start_ns, timeline_cursor_end_ns)
            timeline_cursor_end_ns = structured_start_ns

        if ocr_duration_ms_int is not None:
            ocr_duration_ns = ocr_duration_ms_int * 1_000_000
            ocr_start_ns = max(0, timeline_cursor_end_ns - ocr_duration_ns)
            ocr_window = (ocr_start_ns, timeline_cursor_end_ns)

        emit_timed_span(
            langfuse,
            name="apple-foundation-model-ocr",
            duration_ms=ocr_duration_ms,
            start_timestamp_ns=ocr_window[0] if ocr_window is not None else None,
            end_timestamp_ns=ocr_window[1] if ocr_window is not None else None,
            input={
                "image_path": str(image_path),
                "engine": (ocr_details or {}).get("engine") if isinstance(ocr_details, dict) else None,
            },
            output={
                "ocr_text": data.get("ocr_text"),
                "observation_count": (ocr_details or {}).get("observation_count")
                if isinstance(ocr_details, dict) else None,
                "full_text_chars": (ocr_details or {}).get("full_text_chars")
                if isinstance(ocr_details, dict) else None,
                "prompt_text_chars": (ocr_details or {}).get("prompt_text_chars")
                if isinstance(ocr_details, dict) else None,
                "was_truncated": (ocr_details or {}).get("was_truncated")
                if isinstance(ocr_details, dict) else None,
            },
            metadata={
                "ocr": ocr_details,
                "timing": timing_details,
                "source": "swift.DescribeImage",
            },
            verbose=verbose,
        )

        model_input = {
            "image_path": str(image_path),
            "instructions": data.get("instructions"),
            "prompt": data.get("prompt"),
            "fallback_prompt": data.get("fallback_prompt"),
            "ocr_text": data.get("ocr_text"),
            "content_keywords": data.get("content_keywords"),
            "ocr": ocr_details,
        }
        model_output = {
            "chain_of_thought": chain_of_thought,
            "raw_output": data.get("raw_output"),
            "raw_output_unparsed": data.get("raw_output_unparsed"),
            "parsed_description": description or None,
            "keywords": data.get("keywords"),
            "format_valid": format_valid,
            "normalization_source": normalization_source,
            "structured_generation_attempted": data.get("structured_generation_attempted"),
            "structured_generation_succeeded": data.get("structured_generation_succeeded"),
            "structured_generation_strategy": data.get("structured_generation_strategy"),
            "structured_generation_error": data.get("structured_generation_error"),
            "structured_generation_error_type": data.get("structured_generation_error_type"),
            "structured_generation_duration_ms": data.get("structured_generation_duration_ms"),
            "fallback_generation_duration_ms": data.get("fallback_generation_duration_ms"),
            "prompt_version": data.get("prompt_version"),
            "content_keywords": data.get("content_keywords"),
        }
        result_payload: dict[str, Any] = {
            "chain_of_thought": chain_of_thought,
            "description": description or None,
            "raw_output": data.get("raw_output"),
            "raw_output_unparsed": data.get("raw_output_unparsed"),
            "prompt": data.get("prompt"),
            "fallback_prompt": data.get("fallback_prompt"),
            "instructions": data.get("instructions"),
            "ocr_text": data.get("ocr_text"),
            "keywords": data.get("keywords"),
            "model": data.get("model"),
            "ocr": ocr_details,
            "timing": timing_details,
            "structured_slug": data.get("structured_slug"),
            "structured_chain_of_thought": data.get("structured_chain_of_thought"),
            "structured_output_json": data.get("structured_output_json"),
            "response_mode": data.get("response_mode"),
            "output_contract_version": data.get("output_contract_version"),
            "prompt_version": data.get("prompt_version"),
            "structured_generation_attempted": data.get("structured_generation_attempted"),
            "structured_generation_succeeded": data.get("structured_generation_succeeded"),
            "structured_generation_strategy": data.get("structured_generation_strategy"),
            "structured_generation_error": data.get("structured_generation_error"),
            "structured_generation_error_type": data.get("structured_generation_error_type"),
            "structured_generation_duration_ms": data.get("structured_generation_duration_ms"),
            "fallback_generation_duration_ms": data.get("fallback_generation_duration_ms"),
            "format_valid": format_valid,
            "normalization_source": normalization_source,
            "content_keywords": data.get("content_keywords"),
        }

        if isinstance(structured_span, dict):
            emit_timed_span(
                langfuse,
                name=str(structured_span.get("name") or "apple-foundation-model-structured-generation"),
                duration_ms=structured_span.get("duration_ms"),
                start_timestamp_ns=structured_window[0] if structured_window is not None else None,
                end_timestamp_ns=structured_window[1] if structured_window is not None else None,
                input=structured_span.get("input"),
                output=structured_span.get("output"),
                metadata=structured_span.get("metadata"),
                verbose=verbose,
            )

        if isinstance(fallback_span, dict):
            emit_timed_span(
                langfuse,
                name=str(fallback_span.get("name") or "apple-foundation-model-legacy-fallback"),
                duration_ms=fallback_span.get("duration_ms"),
                start_timestamp_ns=fallback_window[0] if fallback_window is not None else None,
                end_timestamp_ns=fallback_window[1] if fallback_window is not None else None,
                input=fallback_span.get("input"),
                output=fallback_span.get("output"),
                metadata=fallback_span.get("metadata"),
                verbose=verbose,
            )

        update_observation(
            generation,
            verbose=verbose,
            model=model_name,
            input=model_input,
            output=model_output,
            metadata={
                "source": "swift.DescribeImage",
                "model_reported_by_swift": data.get("model"),
                "timing": timing_details,
                "response_mode": data.get("response_mode"),
                "output_contract_version": data.get("output_contract_version"),
                "prompt_version": data.get("prompt_version"),
                "format_valid": format_valid,
                "normalization_source": normalization_source,
                "structured_generation_attempted": data.get("structured_generation_attempted"),
                "structured_generation_succeeded": data.get("structured_generation_succeeded"),
                "structured_generation_strategy": data.get("structured_generation_strategy"),
                "structured_generation_error": data.get("structured_generation_error"),
                "structured_generation_error_type": data.get("structured_generation_error_type"),
                "structured_generation_duration_ms": data.get("structured_generation_duration_ms"),
                "fallback_generation_duration_ms": data.get("fallback_generation_duration_ms"),
            },
        )

        log(f"[enrich] Apple FM description: {description!r}", verbose, is_debug=True)
        return result_payload


def ocr_image(image_path: Path, verbose: bool = False) -> str | None:
    """Extract text from image using macOS Vision framework via PyObjC.

    Returns extracted text or None on failure.
    """
    result = ocr_image_with_context(image_path, verbose=verbose)
    text = result.get("text")
    return text if isinstance(text, str) and text else None


def ocr_image_with_context(image_path: Path, verbose: bool = False) -> dict[str, Any]:
    """OCR image and return structured context for tracing/debugging."""
    context: dict[str, Any] = {
        "engine": "pyobjc-vision",
        "text": None,
        "text_chars": 0,
        "observation_count": 0,
        "error": None,
    }

    try:
        import Quartz
        import Vision

        image_url = Quartz.NSURL.fileURLWithPath_(str(image_path))
        image_source = Quartz.CGImageSourceCreateWithURL(image_url, None)
        if not image_source:
            context["error"] = "cg-image-source-create-failed"
            return context

        cg_image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)
        if not cg_image:
            context["error"] = "cg-image-create-failed"
            return context

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
        success = handler.performRequests_error_([request], None)

        if not success[0]:
            context["error"] = "vision-request-failed"
            return context

        results = request.results()
        if not results:
            context["error"] = "vision-no-results"
            return context
        context["observation_count"] = len(results)

        texts = []
        for observation in results:
            candidate = observation.topCandidates_(1)
            if candidate:
                texts.append(candidate[0].string())

        combined = " ".join(texts)
        log(f"[enrich] OCR text ({len(combined)} chars): {combined[:100]}...", verbose, is_debug=True)
        if not combined.strip():
            context["error"] = "vision-empty-text"
            return context
        context["text"] = combined
        context["text_chars"] = len(combined)
        return context

    except Exception as e:
        log(f"[enrich] OCR failed: {e}", verbose, is_debug=True)
        context["error"] = str(e)
        return context


def sanitize_slug(text: str, max_len: int = 50) -> str:
    """Convert text to a filesystem-safe slug: lowercase, alphanumeric + hyphens."""
    # Lowercase and replace non-alphanum with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    # Collapse multiple hyphens
    slug = re.sub(r"-{2,}", "-", slug)
    # Truncate at max_len, but don't cut mid-word
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug


def build_filename(
    description: str | None,
    timestamp: datetime | None = None,
) -> str:
    """Build a smart filename from description and timestamp.

    Format: 2026-02-23_21h30m45s_PST_description-slug.png
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).astimezone()

    date = timestamp.strftime("%Y-%m-%d")
    time = timestamp.strftime("%Hh%Mm%Ss")
    tz = timestamp.strftime("%Z") or timestamp.strftime("%z")
    desc_slug = sanitize_slug(description, max_len=60) if description else ""

    if desc_slug:
        return f"{date}_{time}_{tz}_{desc_slug}.png"
    return f"{date}_{time}_{tz}.png"


def s3_key_for_file(filename: str, prefix: str = "screenshots") -> str:
    """Build S3 key with date-based folder structure.

    Example: screenshots/2026/02/23/filename.png
    """
    # Extract date from filename (starts with YYYY-MM-DD)
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", filename)
    if match:
        year, month, day = match.groups()
    else:
        now = datetime.now()
        year, month, day = now.strftime("%Y"), now.strftime("%m"), now.strftime("%d")

    return f"{prefix}/{year}/{month}/{day}/{filename}"


# Common UI chrome words to filter from OCR text
_UI_CHROME_WORDS = frozenset({
    "file", "edit", "view", "window", "help", "menu", "tools", "format",
    "selection", "terminal", "go", "run", "debug", "source", "refactor",
    "navigate", "code", "tab", "new", "open", "close", "save", "quit",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "am", "pm", "the", "and", "for", "with", "from", "that", "this",
})


def _extract_ocr_keywords(ocr_text: str, max_words: int = 8) -> str | None:
    """Extract meaningful keywords from OCR text, filtering UI chrome."""
    result = extract_ocr_keywords_with_context(ocr_text, max_words=max_words)
    keywords = result.get("keywords")
    return keywords if isinstance(keywords, str) and keywords else None


def extract_ocr_keywords_with_context(ocr_text: str, max_words: int = 8) -> dict[str, Any]:
    """Extract OCR keywords and return detailed filtering context."""
    words = []
    skipped = {
        "empty_or_non_alnum": 0,
        "too_short": 0,
        "ui_chrome": 0,
    }

    tokens = ocr_text.split()
    for word in tokens:
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", word)
        if not cleaned:
            skipped["empty_or_non_alnum"] += 1
            continue
        if len(cleaned) < 3:
            skipped["too_short"] += 1
            continue
        if cleaned.lower() in _UI_CHROME_WORDS:
            skipped["ui_chrome"] += 1
            continue
        words.append(cleaned)
        if len(words) >= max_words:
            break

    keywords = " ".join(words) if words else None
    return {
        "keywords": keywords,
        "selected_words": words,
        "token_count": len(tokens),
        "selected_count": len(words),
        "max_words": max_words,
        "skipped": skipped,
    }


def enrich_screenshot(
    image_path: Path,
    no_describe: bool = False,
    no_ocr: bool = False,
    verbose: bool = False,
) -> str | None:
    """Get a description for the screenshot. Returns description text or None."""
    description, _context = enrich_screenshot_with_context(
        image_path,
        no_describe=no_describe,
        no_ocr=no_ocr,
        verbose=verbose,
    )
    return description


def enrich_screenshot_with_context(
    image_path: Path,
    no_describe: bool = False,
    no_ocr: bool = False,
    verbose: bool = False,
) -> tuple[str | None, dict[str, Any]]:
    """Get screenshot description and structured context for tracing/debugging."""
    context: dict[str, Any] = {
        "strategy": "none",
        "apple_fm": None,
        "ocr": None,
        "flags": {
            "no_describe": no_describe,
            "no_ocr": no_ocr,
        },
    }
    langfuse = get_langfuse_client(verbose=verbose)

    # Try Apple FM first (does OCR + LLM summarization in one shot)
    if not no_describe and not no_ocr:
        apple_fm_result = describe_with_apple_fm(image_path, verbose)
        context["apple_fm"] = apple_fm_result
        if apple_fm_result:
            description = apple_fm_result.get("description")
            if isinstance(description, str) and description:
                context["strategy"] = "apple_fm"
                return description, context

    # Fallback to PyObjC OCR with keyword extraction
    if not no_ocr:
        ocr_result: dict[str, Any] = {}
        with start_span(
            langfuse,
            name="ocr-fallback-vision",
            input={"image_path": str(image_path), "engine": "pyobjc-vision"},
            verbose=verbose,
        ) as ocr_span:
            ocr_result = ocr_image_with_context(image_path, verbose)
            ocr_text = ocr_result.get("text")
            if not isinstance(ocr_text, str):
                ocr_text = None

            update_observation(
                ocr_span,
                verbose=verbose,
                output={
                    "has_text": bool(ocr_text),
                    "text_chars": ocr_result.get("text_chars"),
                    "observation_count": ocr_result.get("observation_count"),
                },
                metadata={
                    "error": ocr_result.get("error"),
                    "text_preview": (ocr_text[:300] if ocr_text else None),
                },
            )

        keywords_result: dict[str, Any] | None = None
        keywords: str | None = None
        if ocr_text:
            with start_span(
                langfuse,
                name="ocr-keyword-extraction",
                input={"max_words": 8},
                verbose=verbose,
            ) as keywords_span:
                keywords_result = extract_ocr_keywords_with_context(ocr_text)
                maybe_keywords = keywords_result.get("keywords")
                keywords = maybe_keywords if isinstance(maybe_keywords, str) else None
                update_observation(
                    keywords_span,
                    verbose=verbose,
                    output={
                        "keywords": keywords,
                        "selected_count": keywords_result.get("selected_count"),
                    },
                    metadata={
                        "selected_words": keywords_result.get("selected_words"),
                        "skipped": keywords_result.get("skipped"),
                        "token_count": keywords_result.get("token_count"),
                    },
                )

        context["ocr"] = {
            "engine": ocr_result.get("engine"),
            "error": ocr_result.get("error"),
            "text": ocr_text,
            "text_chars": ocr_result.get("text_chars"),
            "observation_count": ocr_result.get("observation_count"),
            "keywords": keywords,
            "keywords_debug": keywords_result,
        }
        if ocr_text:
            context["strategy"] = "ocr_keywords" if keywords else "ocr_no_keywords"
            return keywords, context
    else:
        context["ocr"] = {
            "engine": "pyobjc-vision",
            "error": "ocr-disabled",
        }

    return None, context
