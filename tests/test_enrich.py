"""Tests for filename and OCR keyword enrichment helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from omnishot.enrich import (
    build_apple_fm_observability_spans_with_context,
    build_filename,
    extract_ocr_keywords_with_context,
    normalize_apple_fm_description_with_context,
    s3_key_for_file,
    sanitize_slug,
)


class EnrichHelpersTests(unittest.TestCase):
    def test_sanitize_slug_basic(self) -> None:
        self.assertEqual(sanitize_slug("Hello,   World!!!"), "hello-world")

    def test_sanitize_slug_truncates_without_trailing_hyphen(self) -> None:
        text = "alpha beta gamma delta epsilon zeta eta theta iota"
        slug = sanitize_slug(text, max_len=24)
        self.assertLessEqual(len(slug), 24)
        self.assertFalse(slug.endswith("-"))

    def test_build_filename_with_description(self) -> None:
        pst = timezone(timedelta(hours=-8), name="PST")
        ts = datetime(2026, 2, 23, 21, 43, 40, tzinfo=pst)
        self.assertEqual(
            build_filename("Cursor Settings Heavy Memory", timestamp=ts),
            "2026-02-23_21h43m40s_PST_cursor-settings-heavy-memory.png",
        )

    def test_build_filename_without_description(self) -> None:
        pst = timezone(timedelta(hours=-8), name="PST")
        ts = datetime(2026, 2, 23, 21, 43, 40, tzinfo=pst)
        self.assertEqual(
            build_filename(None, timestamp=ts),
            "2026-02-23_21h43m40s_PST.png",
        )

    def test_s3_key_uses_filename_date_prefix(self) -> None:
        key = s3_key_for_file("2026-02-23_21h43m40s_PST_example.png", prefix="shots")
        self.assertEqual(key, "shots/2026/02/23/2026-02-23_21h43m40s_PST_example.png")

    def test_s3_key_fallback_date_shape(self) -> None:
        key = s3_key_for_file("random-name.png", prefix="shots")
        parts = key.split("/")
        self.assertEqual(len(parts), 5)
        self.assertEqual(parts[0], "shots")
        self.assertTrue(parts[1].isdigit() and len(parts[1]) == 4)
        self.assertTrue(parts[2].isdigit() and len(parts[2]) == 2)
        self.assertTrue(parts[3].isdigit() and len(parts[3]) == 2)
        self.assertEqual(parts[4], "random-name.png")

    def test_extract_keywords_filters_ui_chrome(self) -> None:
        text = (
            "File Edit View Debug Selection implement robust screenshot pipeline "
            "for remote coding agents with upload retries"
        )
        result = extract_ocr_keywords_with_context(text, max_words=6)
        self.assertEqual(result["keywords"], "implement robust screenshot pipeline remote coding")
        self.assertEqual(result["selected_count"], 6)
        self.assertGreaterEqual(result["skipped"]["ui_chrome"], 4)

    def test_normalize_apple_fm_prefers_structured_slug(self) -> None:
        normalized = normalize_apple_fm_description_with_context(
            {
                "structured_slug": "500-miles-email-problem",
                "description": "ignored",
                "raw_output": "ignored",
            }
        )
        self.assertEqual(normalized["description"], "500-miles-email-problem")
        self.assertEqual(normalized["normalization_source"], "structured_slug:exact_match")
        self.assertTrue(normalized["format_valid"])

    def test_normalize_apple_fm_extracts_slug_from_prose(self) -> None:
        normalized = normalize_apple_fm_description_with_context(
            {
                "description": "Here's a short filename description for the screenshot:\n\n500-miles-email-problem",
            }
        )
        self.assertEqual(normalized["description"], "500-miles-email-problem")
        self.assertEqual(normalized["normalization_source"], "description:regex_extract")
        self.assertTrue(normalized["format_valid"])

    def test_normalize_apple_fm_prefers_raw_unparsed_for_parsing(self) -> None:
        normalized = normalize_apple_fm_description_with_context(
            {
                "raw_output_unparsed": "{\"slug\":\"500-miles-email-problem\"}",
                "description": "legacy screenshot naming",
            }
        )
        self.assertEqual(normalized["description"], "500-miles-email-problem")
        self.assertEqual(normalized["normalization_source"], "raw_output_unparsed:regex_extract")
        self.assertTrue(normalized["format_valid"])

    def test_normalize_apple_fm_uses_description_when_raw_unparsed_has_no_slug(self) -> None:
        normalized = normalize_apple_fm_description_with_context(
            {
                "raw_output_unparsed": "N/A",
                "description": "500-miles-email-problem",
            }
        )
        self.assertEqual(normalized["description"], "500-miles-email-problem")
        self.assertEqual(normalized["normalization_source"], "description:exact_match")
        self.assertTrue(normalized["format_valid"])

    def test_normalize_apple_fm_slugifies_free_form_text(self) -> None:
        normalized = normalize_apple_fm_description_with_context(
            {
                "description": "Engineering notes series orchestration framework",
            }
        )
        self.assertEqual(normalized["description"], "engineering-notes-series-orchestration-framework")
        self.assertEqual(normalized["normalization_source"], "description:slugify")
        self.assertTrue(normalized["format_valid"])

    def test_normalize_apple_fm_marks_short_slug_as_invalid(self) -> None:
        normalized = normalize_apple_fm_description_with_context(
            {
                "description": "Engineering notes",
            }
        )
        self.assertEqual(normalized["description"], "engineering-notes")
        self.assertEqual(normalized["normalization_source"], "description:slugify")
        self.assertFalse(normalized["format_valid"])

    def test_normalize_apple_fm_handles_missing_candidates(self) -> None:
        normalized = normalize_apple_fm_description_with_context({})
        self.assertIsNone(normalized["description"])
        self.assertEqual(normalized["normalization_source"], "none")
        self.assertFalse(normalized["format_valid"])

    def test_build_observability_spans_structured_success(self) -> None:
        spans = build_apple_fm_observability_spans_with_context(
            {
                "prompt": "Prompt text",
                "instructions": "Instructions",
                "ocr_text": "OCR",
                "content_keywords": ["500", "miles", "email"],
                "response_mode": "structured_generable",
                "output_contract_version": "v3-structured-slug-no-pattern-guide",
                "prompt_version": "content_focus_v1",
                "structured_generation_attempted": True,
                "structured_generation_succeeded": True,
                "structured_generation_strategy": "generable_description_guide",
                "structured_slug": "500-miles-problem-email-limits",
                "structured_output_json": "{\"slug\":\"500-miles-problem-email-limits\"}",
                "raw_output_unparsed": "{\"slug\":\"500-miles-problem-email-limits\"}",
                "timing": {"model_duration_ms": 1000, "ocr_duration_ms": 500},
                "structured_generation_duration_ms": 700,
            },
            image_path=Path("/tmp/image.png"),
            description="500-miles-problem-email-limits",
            format_valid=True,
            normalization_source="structured_slug:exact_match",
        )

        structured_span = spans["structured_span"]
        self.assertEqual(structured_span["name"], "apple-foundation-model-structured-generation")
        self.assertEqual(structured_span["input"]["image_path"], "/tmp/image.png")
        self.assertEqual(structured_span["input"]["content_keywords"], ["500", "miles", "email"])
        self.assertEqual(next(iter(structured_span["output"])), "chain_of_thought")
        self.assertTrue(structured_span["output"]["structured_generation_succeeded"])
        self.assertEqual(structured_span["metadata"]["prompt_version"], "content_focus_v1")
        self.assertEqual(
            structured_span["metadata"]["structured_generation_strategy"],
            "generable_description_guide",
        )
        self.assertIsNone(spans["fallback_span"])

    def test_build_observability_spans_legacy_fallback(self) -> None:
        spans = build_apple_fm_observability_spans_with_context(
            {
                "prompt": "Prompt text",
                "instructions": "Instructions",
                "content_keywords": ["warning", "session", "ended"],
                "response_mode": "legacy_text_fallback",
                "output_contract_version": "v3-structured-slug-no-pattern-guide",
                "prompt_version": "content_focus_v1",
                "structured_generation_attempted": True,
                "structured_generation_succeeded": False,
                "structured_generation_strategy": "legacy_text_fallback",
                "structured_generation_error": "unsupportedGuide(...)",
                "structured_generation_error_type": "GenerationError",
                "raw_output_unparsed": "500-miles-problem-with-email",
                "raw_output": "500-miles-problem-with-email",
                "fallback_generation_duration_ms": 900,
                "timing": {"model_duration_ms": 1700, "ocr_duration_ms": 900},
            },
            image_path=Path("/tmp/image.png"),
            description="500-miles-problem-with-email",
            format_valid=True,
            normalization_source="raw_output_unparsed:exact_match",
        )

        self.assertIsNotNone(spans["fallback_span"])
        fallback_span = spans["fallback_span"]
        self.assertIsInstance(fallback_span, dict)
        self.assertEqual(fallback_span["name"], "apple-foundation-model-legacy-fallback")
        self.assertEqual(next(iter(fallback_span["output"])), "chain_of_thought")
        self.assertEqual(
            fallback_span["input"]["content_keywords"],
            ["warning", "session", "ended"],
        )
        self.assertEqual(
            fallback_span["output"]["parsed_description"],
            "500-miles-problem-with-email",
        )
        self.assertEqual(fallback_span["metadata"]["prompt_version"], "content_focus_v1")
        self.assertEqual(
            fallback_span["metadata"]["structured_generation_error_type"],
            "GenerationError",
        )


if __name__ == "__main__":
    unittest.main()
