#!/usr/bin/env bash
# Run omnishot in watch mode
set -euo pipefail

cd "$(dirname "$0")"
exec uv run omnishot watch "$@"
