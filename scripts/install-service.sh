#!/bin/bash
set -euo pipefail

LABEL="${OMNISHOT_LAUNCHD_LABEL:-com.ma08.omnishot}"
LOG_BASENAME="${OMNISHOT_LAUNCHD_LOG_BASENAME:-omnishot}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WRAPPER="$PROJECT_ROOT/scripts/launchd-wrapper.sh"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST="$LAUNCH_AGENTS/$LABEL.plist"
STDOUT_LOG="$HOME/Library/Logs/$LOG_BASENAME.log"
STDERR_LOG="$HOME/Library/Logs/$LOG_BASENAME.err"
GUI_DOMAIN="gui/$(id -u)"

echo "==> Installing $LABEL (menu-bar mode)"

# Validate prerequisites
if ! command -v uv &>/dev/null; then
  echo "ERROR: uv not found. Install it first: https://docs.astral.sh/uv/"
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install Python 3 first."
  exit 1
fi

if [[ ! -f "$WRAPPER" ]]; then
  echo "ERROR: Wrapper script not found at $WRAPPER"
  exit 1
fi

# Ensure uv venv exists
if [[ ! -d "$PROJECT_ROOT/.venv" ]]; then
  echo "==> Running uv sync..."
  (cd "$PROJECT_ROOT" && uv sync)
fi

# Create directories if missing
mkdir -p "$HOME/Library/Logs"
mkdir -p "$HOME/Pictures/Screenshots"
mkdir -p "$LAUNCH_AGENTS"

# Unload existing agent if loaded (ignore errors if not loaded)
if launchctl print "$GUI_DOMAIN/$LABEL" &>/dev/null; then
  echo "==> Unloading existing agent..."
  launchctl bootout "$GUI_DOMAIN/$LABEL" 2>/dev/null || true
fi

# Generate plist in ~/Library/LaunchAgents/ so repo paths are not tracked.
if [[ -L "$PLIST" || -f "$PLIST" ]]; then
  rm "$PLIST"
fi
python3 - "$PLIST" "$LABEL" "$WRAPPER" "$STDOUT_LOG" "$STDERR_LOG" <<'PY'
import plistlib
import sys

plist_path, label, wrapper, stdout_log, stderr_log = sys.argv[1:]
plist = {
    "Label": label,
    "ProgramArguments": [wrapper],
    "RunAtLoad": True,
    "KeepAlive": True,
    "StandardOutPath": stdout_log,
    "StandardErrorPath": stderr_log,
    "EnvironmentVariables": {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    },
}
with open(plist_path, "wb") as f:
    plistlib.dump(plist, f)
PY
echo "==> Generated plist at $PLIST"

# Load agent
echo "==> Loading agent..."
launchctl bootstrap "$GUI_DOMAIN" "$PLIST"

echo ""
echo "==> $LABEL installed and running (menu-bar mode)!"
echo ""
echo "Management commands:"
echo "  Status:   launchctl print $GUI_DOMAIN/$LABEL"
echo "  Logs:     tail -f $STDOUT_LOG"
echo "  Errors:   tail -f $STDERR_LOG"
echo "  Stop:     launchctl kill SIGTERM $GUI_DOMAIN/$LABEL"
echo "  Disable:  launchctl disable $GUI_DOMAIN/$LABEL"
echo "  Enable:   launchctl enable $GUI_DOMAIN/$LABEL"
echo "  Uninstall: $PROJECT_ROOT/scripts/uninstall-service.sh"
