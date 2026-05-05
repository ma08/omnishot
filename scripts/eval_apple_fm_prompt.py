#!/usr/bin/env python3
"""Evaluate Apple FM slug prompt quality using OCR text fixtures.

Inputs:
- Fixture file with OCR text + include/exclude expectations.
- Swift helper binary path (`DescribeImage`).

Outputs:
- Console summary with per-case pass/fail.
- Optional JSON report file for CI/history tracking.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from omnishot.prompt_eval import evaluate_slug_case, load_eval_cases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/apple_fm_prompt_eval_cases.json"),
        help="Path to fixture JSON file",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path("swift/.build/release/DescribeImage"),
        help="Path to compiled DescribeImage binary",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.85,
        help="Minimum pass rate required for success (0.0-1.0)",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("output/prompt-eval"),
        help="Directory for generated OCR input files and optional report",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional JSON report output path",
    )
    return parser.parse_args()


def run_case(binary: Path, case: dict[str, Any], artifacts_dir: Path) -> dict[str, Any]:
    case_id = case["id"]
    ocr_path = artifacts_dir / f"{case_id}.ocr.txt"
    ocr_path.write_text(case["ocr_text"], encoding="utf-8")

    cmd = [str(binary), "--ocr-text-file", str(ocr_path)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "id": case_id,
            "passed": False,
            "error": f"command-failed: {exc}",
            "slug": None,
            "format_valid": False,
            "includes_ok": False,
            "excludes_ok": False,
            "matched_include": None,
            "excluded_hits": [],
        }

    if result.returncode != 0:
        return {
            "id": case_id,
            "passed": False,
            "error": f"non-zero-exit ({result.returncode}): {result.stderr.strip()}",
            "slug": None,
            "format_valid": False,
            "includes_ok": False,
            "excludes_ok": False,
            "matched_include": None,
            "excluded_hits": [],
        }

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "id": case_id,
            "passed": False,
            "error": f"invalid-json: {exc}",
            "slug": None,
            "format_valid": False,
            "includes_ok": False,
            "excludes_ok": False,
            "matched_include": None,
            "excluded_hits": [],
        }

    slug = payload.get("description")
    if not isinstance(slug, str):
        return {
            "id": case_id,
            "passed": False,
            "error": "missing-description",
            "slug": None,
            "format_valid": False,
            "includes_ok": False,
            "excludes_ok": False,
            "matched_include": None,
            "excluded_hits": [],
        }

    eval_result = evaluate_slug_case(
        slug,
        must_include_any=case.get("must_include_any"),
        must_exclude=case.get("must_exclude"),
    )

    return {
        "id": case_id,
        "critical": bool(case.get("critical", False)),
        "passed": bool(eval_result["passed"]),
        "slug": slug,
        "format_valid": bool(eval_result["format_valid"]),
        "includes_ok": bool(eval_result["includes_ok"]),
        "excludes_ok": bool(eval_result["excludes_ok"]),
        "matched_include": eval_result.get("matched_include"),
        "excluded_hits": eval_result.get("excluded_hits", []),
        "error": None,
    }


def main() -> int:
    args = parse_args()

    if not (0.0 <= args.min_pass_rate <= 1.0):
        print("error: --min-pass-rate must be between 0.0 and 1.0", file=sys.stderr)
        return 2

    if not args.binary.exists():
        print(f"error: binary not found: {args.binary}", file=sys.stderr)
        return 2

    try:
        cases = load_eval_cases(args.fixture)
    except Exception as exc:
        print(f"error: failed to load fixtures: {exc}", file=sys.stderr)
        return 2

    args.artifacts_dir.mkdir(parents=True, exist_ok=True)

    results = [run_case(args.binary, case, args.artifacts_dir) for case in cases]

    total = len(results)
    passed = sum(1 for item in results if item["passed"])
    pass_rate = (passed / total) if total else 0.0
    critical_failed = [item["id"] for item in results if item.get("critical") and not item["passed"]]

    print("Apple FM prompt eval results")
    print(f"- Fixture: {args.fixture}")
    print(f"- Binary: {args.binary}")
    print(f"- Passed: {passed}/{total} ({pass_rate:.1%})")
    if critical_failed:
        print(f"- Critical failures: {', '.join(critical_failed)}")
    else:
        print("- Critical failures: none")
    print("")
    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        detail = item["slug"] if item["slug"] else item["error"]
        print(f"[{status}] {item['id']}: {detail}")

    report = {
        "fixture": str(args.fixture),
        "binary": str(args.binary),
        "min_pass_rate": args.min_pass_rate,
        "summary": {
            "total": total,
            "passed": passed,
            "pass_rate": pass_rate,
            "critical_failed": critical_failed,
        },
        "results": results,
    }
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return 0 if pass_rate >= args.min_pass_rate and not critical_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
