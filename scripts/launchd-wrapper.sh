#!/bin/zsh
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
[ -f "$HOME/pro/botfiles/.botenv" ] && . "$HOME/pro/botfiles/.botenv"
cd "$(dirname "$0")/.."
exec uv run omnishot menubar
