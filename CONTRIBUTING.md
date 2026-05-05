# Contributing

Thanks for contributing to Omnishot.

## Local Setup

1. Clone and install dependencies:

```bash
git clone https://github.com/ma08/omnishot.git
cd omnishot
uv sync
```

2. Optional: build the Swift helper for Apple Foundation Models naming:

```bash
./scripts/build-swift.sh
```

## Development Workflow

Run local sanity checks before opening a PR:

```bash
./scripts/check.sh
```

This runs:
- `uv run ruff check src tests`
- `uv run python -m unittest discover -s tests -v`
- `uv run python -m compileall src tests`

## Testing Guidelines

- Keep tests deterministic and `unittest`-based.
- Avoid network/AWS calls in unit tests.
- Prefer focused tests for pure logic and edge cases.

## Coding Guidelines

- Python 3.10+
- `pathlib.Path` over raw string paths where practical
- Small, focused functions with clear types
- Keep user-facing logs concise and actionable

## Security and Secrets

- Do not commit credentials (`.env`, AWS keys, Langfuse secrets).
- Scrub sensitive links/tokens from logs and task artifacts before sharing.

## Pull Requests

Please include:
- concise summary + motivation,
- linked issue (for example `Closes #4`),
- test evidence (commands and results),
- screenshots/log snippets for menubar or service behavior changes when relevant.
