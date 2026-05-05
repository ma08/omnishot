"""Tests for Apple FM prompt eval helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omnishot.prompt_eval import evaluate_slug_case, load_eval_cases


class PromptEvalTests(unittest.TestCase):
    def test_evaluate_slug_case_passes_include_and_exclude(self) -> None:
        result = evaluate_slug_case(
            "500-miles-email-problem",
            must_include_any=["email", "mail"],
            must_exclude=["browser", "menu"],
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["format_valid"])
        self.assertTrue(result["includes_ok"])
        self.assertTrue(result["excludes_ok"])
        self.assertEqual(result["matched_include"], "email")

    def test_evaluate_slug_case_fails_include_requirement(self) -> None:
        result = evaluate_slug_case(
            "browser-menu-tab-message",
            must_include_any=["email", "miles"],
            must_exclude=[],
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["includes_ok"])
        self.assertIsNone(result["matched_include"])

    def test_evaluate_slug_case_fails_exclude_requirement(self) -> None:
        result = evaluate_slug_case(
            "browser-menu-tab-message",
            must_include_any=[],
            must_exclude=["menu", "tab"],
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["excludes_ok"])
        self.assertEqual(result["excluded_hits"], ["menu", "tab"])

    def test_load_eval_cases_validates_schema(self) -> None:
        payload = {
            "cases": [
                {
                    "id": "sample",
                    "ocr_text": "hello world",
                    "must_include_any": ["hello"],
                    "must_exclude": ["browser"],
                    "critical": True,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = Path(temp_dir) / "fixture.json"
            fixture_path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_eval_cases(fixture_path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "sample")

    def test_load_eval_cases_rejects_invalid_case(self) -> None:
        payload = {
            "cases": [
                {
                    "id": "",
                    "ocr_text": "hello world",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = Path(temp_dir) / "fixture.json"
            fixture_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_eval_cases(fixture_path)


if __name__ == "__main__":
    unittest.main()
