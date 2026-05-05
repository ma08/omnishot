# Repository Guidelines

## Project Structure & Module Organization
- Core Python package: `src/omnishot/`
  - `main.py`: CLI entrypoint (`watch`, `upload`, `menubar`)
  - `watcher.py`: file watching + processing pipeline
  - `enrich.py`: OCR + semantic naming
  - `upload.py`: S3 upload, presigned/public link helpers
  - `menubar.py`: macOS menu-bar runtime
  - `history.py`: local SQLite history
- Swift helper: `swift/` (`DescribeImage` binary for Apple Foundation Models flow).
- Tests: `tests/` (currently `unittest`-based).
- Documentation: `README.md` (user-facing) and `docs/tech-stack.md` (implementation stack + citations).
- Service and utility scripts: `scripts/`.
- Keep local task logs/artifacts outside the public tree. `context/` is ignored
  and should not be committed.

## Build, Test, and Development Commands
- `uv sync`: install/update Python dependencies from `pyproject.toml`/`uv.lock`.
- `OMNISHOT_BUCKET=your-bucket uv run omnishot menubar`: run the menu-bar app (recommended local mode).
- `uv run omnishot watch --bucket your-bucket --verbose`: run headless watcher with debug logs.
- `uv run omnishot upload path/to/image.png --bucket your-bucket`: one-shot upload.
- `./scripts/build-swift.sh`: build Swift helper (`swift/.build/release/DescribeImage`).
- `uv run python -m unittest discover -s tests -v`: run unit tests.
- `./scripts/install-service.sh` / `./scripts/uninstall-service.sh`: manage launchd service.

## Coding Style & Naming Conventions
- Python 3.10+, 4-space indentation, PEP 8-compatible formatting.
- Prefer type hints (`str | None`, `Path`) and small, focused functions.
- Use `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants, `PascalCase` for classes.
- Prefer `pathlib.Path` over raw string paths.
- Keep CLI/user logs concise and actionable.

## Testing Guidelines
- Framework: `unittest`.
- Place tests in `tests/test_*.py`; group by module/feature.
- Use deterministic unit tests; avoid real AWS/network calls in tests.
- Validate edge cases around filename generation, URL expiry, and history DB behavior.

## Commit & Pull Request Guidelines
- Use Conventional-style prefixes seen in history: `feat:`, `fix:`, `chore:` (optional scope, e.g. `fix(upload): ...`).
- Keep commits focused and logically atomic.
- PRs should include:
  - concise summary + motivation,
  - linked issue (e.g. `Closes #4`),
  - test evidence (command + result),
  - screenshots/log snippets for menu-bar or service behavior changes.

## Security & Configuration Tips
- Never commit credentials (`.env`, AWS keys, Langfuse secrets).
- Do not commit `context/`, local task artifacts, screenshots with private
  content, logs with presigned URLs, or exported observability traces.
- Verify S3 ACL/public-access settings before using public-link features.

## Documentation Maintenance
- Keep `README.md` short and scan-friendly; move detailed setup, architecture, and troubleshooting into `docs/`.
- Keep `docs/usage.md`, `docs/agent-instructions.md`, `docs/architecture.md`, and `docs/tech-stack.md` aligned with runtime behavior in `watcher.py`, `enrich.py`, `menubar.py`, `notify.py`, `upload.py`, and `tracing.py`.
- Keep `docs/tech-stack.md` current whenever stack/API usage changes (Foundation Models, Vision OCR, menu-bar GUI, clipboard formats, notifications, tracing, or S3 flows).
- When output contracts or prompting strategy changes (for example `output_contract_version`, structured fields, fallback logic), update the relevant docs in the same change.
