# Troubleshooting

## Swift Helper Not Found Or Apple FM Not Used

- Run `./scripts/build-swift.sh`.
- If unavailable, the pipeline still works via OCR fallback.

## Apple FM Returned Extra Prose

The helper uses structured guided generation first and normalizes outputs before
rename.

If trace metadata shows `unsupportedGuide`, a guide pattern was not supported at
runtime. The current contract avoids regex guides and uses
description-guided structured output.

If the model still drifts, the parser fallback extracts and slugifies a stable
value. Useful Langfuse fields include:

- `raw_output_unparsed`
- `response_mode`
- `output_contract_version`
- `prompt_version`
- `content_keywords`
- `structured_generation_attempted`
- `structured_generation_succeeded`
- `structured_generation_strategy`
- `structured_generation_error`
- `structured_generation_error_type`
- `format_valid`
- `normalization_source`

Dedicated spans:

- `apple-foundation-model-structured-generation`
- `apple-foundation-model-legacy-fallback`

## Upload Fails

- Validate AWS identity: `aws sts get-caller-identity`.
- Check bucket permissions and region.
- Confirm the bucket name passed via `--bucket`.

## Public Link Generation Fails With ACL Error

If the message mentions `BlockPublicAcls`, your bucket blocks ACL-based public
objects. Either disable that block setting or use a bucket policy/public serving
strategy instead of object ACLs.

## URLs Do Not Appear On Clipboard

- Ensure `pbcopy` is available.
- Run with `--verbose` and inspect logs for clipboard errors.

## Global Paste Shortcuts Do Not Fire

- Open the menubar and confirm it shows `Shortcuts: V variants (ready)`.
- If it shows `permissions required`, use `Request Shortcut Permissions`.
- Allow both Input Monitoring and Accessibility in System Settings.
- Restart the menubar service after granting permissions:

```bash
./scripts/redeploy-local-service.sh
```

Try again in Notes or TextEdit to rule out an app-specific shortcut conflict.

## Copy Image Works In Finder But Not Cursor Explorer

- Restart/redeploy the service with the latest code.
- In Cursor, click the target folder in Explorer, then press `Cmd+V`.
- If needed, verify you are on a recent Cursor build. Explorer external-file
  paste behavior is editor-version dependent.

## Prompt/Eval Tests

Use local deterministic tests when tuning prompt/parse behavior:

```bash
./scripts/check.sh
```

Run the prompt-quality fixture eval:

```bash
./scripts/build-swift.sh
uv run python scripts/eval_apple_fm_prompt.py \
  --fixture tests/fixtures/apple_fm_prompt_eval_cases.json \
  --min-pass-rate 0.85
```

Evaluate prompt behavior directly from OCR text fixtures:

```bash
swift/.build/release/DescribeImage --ocr-text-file path/to/ocr.txt
```

Focus cases:

- `tests/test_enrich.py`
- `tests/test_prompt_eval.py`
- `tests/fixtures/apple_fm_prompt_eval_cases.json`
