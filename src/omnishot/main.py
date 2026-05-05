"""CLI entry point for Omnishot."""

import argparse
import os
import sys
from pathlib import Path

from .paste import VALID_PASTE_MODES, normalize_paste_mode, resolve_runtime_config
from .tracing import flush_langfuse, get_langfuse_client
from .watcher import process_screenshot, start_watching

DEFAULT_WATCH_DIR = Path.home() / "Pictures" / "Screenshots"
ENV_BUCKET = "OMNISHOT_BUCKET"
DEFAULT_PREFIX = "screenshots"
DEFAULT_EXPIRY = 86400
DEFAULT_REFRESH_THRESHOLD_SECONDS = 300
DEFAULT_HISTORY_LIMIT = 20


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="omnishot",
        description=(
            "Watch for screenshots, semantic-rename locally, and copy agent-ready "
            "payloads, image data, or S3 links."
        ),
    )

    subparsers = parser.add_subparsers(dest="command")

    # Common options
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--bucket",
        "-b",
        default=os.environ.get(ENV_BUCKET),
        help=f"S3 bucket (required unless {ENV_BUCKET} is set)",
    )
    common.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"S3 key prefix (default: {DEFAULT_PREFIX})")
    common.add_argument("--expiry", "-e", type=int, default=DEFAULT_EXPIRY, help=f"Presigned URL expiry in seconds (default: {DEFAULT_EXPIRY})")
    common.add_argument("--no-describe", action="store_true", help="Skip Apple FM, use OCR only")
    common.add_argument("--no-ocr", action="store_true", help="Skip all image analysis")
    common.add_argument("--no-rename", action="store_true", help="Don't rename the local file")
    common.add_argument("--no-notify", action="store_true", help="Skip notification")
    common.add_argument("--verbose", "-v", action="store_true", help="Debug output")
    common.add_argument(
        "--default-paste-mode",
        type=normalize_paste_mode,
        choices=VALID_PASTE_MODES,
        default=None,
        help=(
            "Clipboard payload after processing. Defaults to saved config or "
            "OMNISHOT_DEFAULT_PASTE_MODE, then path-ref."
        ),
    )
    common.add_argument(
        "--ssh-host-hint",
        default=None,
        help="Optional SSH host alias to use as the machine token in path-ref screenshot payloads",
    )

    # Watch subcommand
    watch_parser = subparsers.add_parser("watch", parents=[common], help="Watch directory for new screenshots")
    watch_parser.add_argument("--watch-dir", "-w", type=Path, default=DEFAULT_WATCH_DIR, help=f"Directory to watch (default: {DEFAULT_WATCH_DIR})")

    # Upload subcommand
    upload_parser = subparsers.add_parser("upload", parents=[common], help="Upload a single file")
    upload_parser.add_argument("file", type=Path, help="File to upload")

    # Menubar subcommand
    menubar_parser = subparsers.add_parser("menubar", parents=[common], help="Run menu-bar widget")
    menubar_parser.add_argument("--watch-dir", "-w", type=Path, default=DEFAULT_WATCH_DIR, help=f"Directory to watch (default: {DEFAULT_WATCH_DIR})")
    menubar_parser.add_argument(
        "--refresh-threshold-seconds",
        type=int,
        default=DEFAULT_REFRESH_THRESHOLD_SECONDS,
        help=(
            "Regenerate a presigned URL before copy when remaining lifetime is "
            f"<= threshold (default: {DEFAULT_REFRESH_THRESHOLD_SECONDS})"
        ),
    )
    menubar_parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_HISTORY_LIMIT,
        help=f"Max history entries shown in menu (default: {DEFAULT_HISTORY_LIMIT})",
    )
    menubar_parser.add_argument(
        "--public-base-url",
        default=None,
        help="Optional base URL for generated public links (for example CDN domain)",
    )

    args = parser.parse_args(argv)

    # Default to watch if no subcommand given
    if args.command is None:
        args.command = "watch"
        # Re-parse with watch defaults
        args = watch_parser.parse_args(argv or [])
        args.command = "watch"

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.bucket:
        print(f"Error: --bucket is required or set {ENV_BUCKET}.", file=sys.stderr)
        return 2

    runtime_config = resolve_runtime_config(
        default_paste_mode=args.default_paste_mode,
        ssh_host_hint=args.ssh_host_hint,
    )

    if args.command == "watch":
        start_watching(
            watch_dir=args.watch_dir,
            bucket=args.bucket,
            prefix=args.prefix,
            expiry=args.expiry,
            no_describe=args.no_describe,
            no_ocr=args.no_ocr,
            no_rename=args.no_rename,
            no_notify=args.no_notify,
            verbose=args.verbose,
            default_paste_mode=runtime_config.default_paste_mode,
            ssh_host_hint=runtime_config.ssh_host_hint,
        )
        return 0

    elif args.command == "upload":
        file_path = args.file.expanduser().resolve()
        if not file_path.exists():
            print(f"Error: file not found: {file_path}", file=sys.stderr)
            return 1

        try:
            result = process_screenshot(
                file_path,
                bucket=args.bucket,
                prefix=args.prefix,
                expiry=args.expiry,
                no_describe=args.no_describe,
                no_ocr=args.no_ocr,
                no_rename=args.no_rename,
                no_notify=args.no_notify,
                verbose=args.verbose,
                default_paste_mode=runtime_config.default_paste_mode,
                ssh_host_hint=runtime_config.ssh_host_hint,
            )
            return 0 if result.presigned_url else 1
        finally:
            # One-shot mode is short-lived; flush once on process exit.
            flush_langfuse(get_langfuse_client(verbose=args.verbose), verbose=args.verbose)

    elif args.command == "menubar":
        from .menubar import run_menubar

        run_menubar(
            watch_dir=args.watch_dir,
            bucket=args.bucket,
            prefix=args.prefix,
            expiry=args.expiry,
            no_describe=args.no_describe,
            no_ocr=args.no_ocr,
            no_rename=args.no_rename,
            no_notify=args.no_notify,
            verbose=args.verbose,
            refresh_threshold_seconds=args.refresh_threshold_seconds,
            history_limit=args.history_limit,
            public_base_url=args.public_base_url,
            default_paste_mode=runtime_config.default_paste_mode,
            ssh_host_hint=runtime_config.ssh_host_hint,
        )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
