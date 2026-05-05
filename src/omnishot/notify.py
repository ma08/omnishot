"""Clipboard, notification, and logging utilities."""

import shutil
import subprocess
import sys


def copy_to_clipboard(url: str) -> bool:
    """Copy URL to clipboard via pbcopy. Returns True on success."""
    try:
        subprocess.run(
            ["pbcopy"],
            input=url.encode(),
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def send_notification(title: str, message: str, url: str | None = None) -> bool:
    """Send a macOS notification. Tries terminal-notifier first, falls back to osascript."""
    if shutil.which("terminal-notifier"):
        cmd = [
            "terminal-notifier",
            "-title", title,
            "-message", message,
            "-sound", "default",
        ]
        if url:
            cmd += ["-open", url]
        try:
            subprocess.run(cmd, check=True, timeout=10)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    # Fallback: osascript
    script = f'display notification "{message}" with title "{title}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def log(message: str, verbose: bool = False, is_debug: bool = False) -> None:
    """Print to stdout. Debug messages only shown if verbose=True."""
    if is_debug and not verbose:
        return
    print(message, file=sys.stderr if is_debug else sys.stdout, flush=True)
