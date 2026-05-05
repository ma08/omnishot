#!/bin/bash
set -euo pipefail

LABEL="${OMNISHOT_LAUNCHD_LABEL:-com.ma08.omnishot}"
LOG_BASENAME="${OMNISHOT_LAUNCHD_LOG_BASENAME:-omnishot}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
GUI_DOMAIN="gui/$(id -u)"

echo "==> Uninstalling $LABEL"

# Unload agent if loaded
if launchctl print "$GUI_DOMAIN/$LABEL" &>/dev/null; then
  echo "==> Unloading agent..."
  launchctl bootout "$GUI_DOMAIN/$LABEL" 2>/dev/null || true
fi

# Remove plist
if [[ -L "$PLIST" || -f "$PLIST" ]]; then
  rm "$PLIST"
  echo "==> Removed $PLIST"
else
  echo "==> No plist found at $PLIST"
fi

echo ""
echo "==> $LABEL uninstalled."
echo "    Log files preserved at ~/Library/Logs/$LOG_BASENAME.{log,err}"
