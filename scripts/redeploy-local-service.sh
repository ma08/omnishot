#!/bin/bash
set -euo pipefail

LABEL="${OMNISHOT_LAUNCHD_LABEL:-com.ma08.omnishot}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUI_DOMAIN="gui/$(id -u)"

echo "==> Redeploying $LABEL"
echo "==> Project: $PROJECT_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install uv first: https://docs.astral.sh/uv/"
  exit 1
fi

if ! command -v swift >/dev/null 2>&1; then
  echo "ERROR: swift not found. Install Xcode/Swift toolchain first."
  exit 1
fi

cd "$PROJECT_ROOT"

echo "==> Checking out main"
git checkout main

echo "==> Pulling latest main"
git pull --ff-only origin main

echo "==> Syncing Python dependencies"
uv sync

echo "==> Building Swift helper"
swift build -c release --package-path swift

if launchctl print "$GUI_DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "==> Restarting launchd service"
  launchctl kickstart -k "$GUI_DOMAIN/$LABEL"
  echo "==> Redeploy complete"
else
  echo "WARNING: launchd service '$LABEL' is not installed."
  echo "Run ./scripts/install-service.sh first, then rerun this script."
fi
