"""Helpers for evaluating Apple FM slug outputs against fixture expectations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SLUG_POLICY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,7}$")


def _normalize_tokens(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        lowered = value.strip().lower()
        if lowered:
            normalized.append(lowered)
    return normalized


def evaluate_slug_case(
    slug: str,
    *,
    must_include_any: list[str] | None = None,
    must_exclude: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate one slug against include/exclude token expectations."""
    lowered = slug.strip().lower()
    tokens = [token for token in lowered.split("-") if token]

    include_targets = _normalize_tokens(must_include_any or [])
    exclude_targets = _normalize_tokens(must_exclude or [])

    matched_include = next((token for token in include_targets if token in tokens), None)
    excluded_hits = [token for token in exclude_targets if token in tokens]

    format_valid = bool(_SLUG_POLICY_PATTERN.fullmatch(lowered))
    includes_ok = True if not include_targets else matched_include is not None
    excludes_ok = not excluded_hits
    passed = format_valid and includes_ok and excludes_ok

    return {
        "passed": passed,
        "format_valid": format_valid,
        "includes_ok": includes_ok,
        "matched_include": matched_include,
        "excludes_ok": excludes_ok,
        "excluded_hits": excluded_hits,
        "tokens": tokens,
    }


def validate_eval_case(case: dict[str, Any], *, index: int) -> None:
    """Validate fixture case schema with clear error context."""
    if not isinstance(case.get("id"), str) or not case["id"].strip():
        raise ValueError(f"Case {index}: 'id' must be a non-empty string")
    if not isinstance(case.get("ocr_text"), str) or not case["ocr_text"].strip():
        raise ValueError(f"Case {case['id']}: 'ocr_text' must be a non-empty string")

    for key in ("must_include_any", "must_exclude"):
        value = case.get(key, [])
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Case {case['id']}: '{key}' must be a list of strings")

    critical = case.get("critical", False)
    if not isinstance(critical, bool):
        raise ValueError(f"Case {case['id']}: 'critical' must be a boolean")


def load_eval_cases(path: Path) -> list[dict[str, Any]]:
    """Load and validate eval fixture cases."""
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Fixture must define a non-empty 'cases' list")

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Case {index}: expected object")
        validate_eval_case(case, index=index)

    return cases
