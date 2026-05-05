# Usage

Omnishot is installed and run with the `omnishot` CLI.

## Requirements

Required:

- macOS
- Python 3.10+
- `uv`
- AWS credentials configured through `~/.aws/credentials` or environment vars
- S3 bucket with upload permissions, passed as `--bucket` or
  `OMNISHOT_BUCKET`

Optional:

- Apple Intelligence + Swift helper build for best filename quality
- `terminal-notifier` for richer notifications
- Langfuse credentials for tracing
- Input Monitoring + Accessibility permissions for global paste shortcuts

## Screenshot Folder

```bash
defaults write com.apple.screencapture location ~/Pictures/Screenshots
killall SystemUIServer
```

## S3 Bucket

```bash
aws s3 mb s3://your-bucket-name --region us-east-1
```

Minimum IAM/S3 capability needed for normal flow:

- `s3:PutObject`
- `s3:GetObject`

If you want `Generate Public Link` from the menubar, ACL updates must also be
allowed with `s3:PutObjectAcl`, and bucket settings must permit ACL usage.

## Install And Run

```bash
git clone https://github.com/ma08/omnishot.git
cd omnishot
uv sync

# Optional, recommended for semantic naming on supported macOS versions
./scripts/build-swift.sh

export OMNISHOT_BUCKET=your-bucket-name
uv run omnishot menubar
```

Headless watcher:

```bash
uv run omnishot watch --bucket your-bucket-name --verbose
```

One-shot upload:

```bash
uv run omnishot upload path/to/image.png --bucket your-bucket-name
```

## Menu-Bar Mode

```bash
uv run omnishot menubar \
  --refresh-threshold-seconds 300 \
  --history-limit 30 \
  --default-paste-mode path-ref \
  --ssh-host-hint work-mac
```

The default paste mode is persisted by the menubar app in:

```text
~/Library/Application Support/omnishot/config.json
```

Environment fallbacks:

- `OMNISHOT_DEFAULT_PASTE_MODE=path-ref|s3-url`
- `OMNISHOT_SSH_HOST_HINT=<ssh-alias>`
- `BOT_MACHINE_SSH_ALIAS=<ssh-alias>`

The launchd wrapper sources `~/pro/botfiles/.botenv` before starting the app, so
botfiles-provided environment is available to the service.

## Paste Modes And Shortcuts

Normal `Cmd+V` pastes whatever the watcher last copied to the clipboard. By
default that is a compact path-reference payload:

```text
screenshot-info:
  machine: work-mac
  path: /Users/alex/Pictures/Screenshots/example.png
```

`machine` should be an SSH alias other agent machines can use directly.
Configure it with `--ssh-host-hint`, `OMNISHOT_SSH_HOST_HINT`, or the
shared botfiles `BOT_MACHINE_SSH_ALIAS`.

| Shortcut | Payload |
|----------|---------|
| `Cmd+V` | configured default clipboard payload after capture |
| `Cmd+Option+V` | latest path reference |
| `Cmd+Shift+Option+V` | latest image directly into the focused app |
| `Cmd+Control+Option+V` | latest S3 URL |
| `Cmd+Control+Shift+Option+V` | generate and paste the latest public link |

The explicit shortcuts temporarily write the selected payload, synthesize
`Cmd+V`, then restore the previous clipboard when it has not changed.

`local-agent` is still accepted as a migration alias for `path-ref` in config,
env, and CLI usage.

On first launch, macOS may prompt for Input Monitoring and Accessibility so the
app can listen for the V-variant shortcuts and post the synthetic paste event.
If prompts are missing or incomplete, use the menubar `Request Shortcut
Permissions` action and restart/redeploy after granting access.

## Menubar Actions

- `Copy S3 URL` refreshes the URL when it is close to expiry.
- `Copy Path Reference` copies the compact `screenshot-info` payload.
- `Generate Public Link and Copy Public Link` makes a public URL when configured.
- `Copy Image` pastes image content into chat/docs apps and copied-file payloads
  into Finder or editor explorers.
- The latest screenshot section also includes `Generate Latest Public Link and
  Copy` so you do not need to open a history submenu for the common case.
- Latest top-level actions show their matching shortcut in brackets in the
  menubar for quick reference.
- Recent entries can be opened or revealed locally.

Tip: in Cursor/VS Code, focus Explorer and select the destination folder before
pressing `Cmd+V`.

## Run At Login

```bash
./scripts/install-service.sh
```

Uninstall:

```bash
./scripts/uninstall-service.sh
```

Redeploy after local changes:

```bash
./scripts/redeploy-local-service.sh
```

## CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `--bucket`, `-b` | S3 bucket name | pass your own bucket |
| `--prefix` | S3 key prefix | `screenshots` |
| `--expiry`, `-e` | Presigned URL expiry seconds | `86400` |
| `--watch-dir`, `-w` | Directory to watch | `~/Pictures/Screenshots` |
| `--refresh-threshold-seconds` | Regenerate URL when remaining lifetime is below threshold | `300` |
| `--history-limit` | Max recent entries shown in menu | `20` |
| `--public-base-url` | Optional base URL for public links | none |
| `--no-describe` | Skip Apple FM and use OCR keywords only | off |
| `--no-ocr` | Skip all image analysis | off |
| `--no-rename` | Keep original local filename | off |
| `--no-notify` | Skip desktop notifications | off |
| `--verbose`, `-v` | Debug logs | off |
| `--default-paste-mode` | `path-ref` or `s3-url` | saved config/env, then `path-ref` |
| `--ssh-host-hint` | SSH alias used as the path-ref `machine` value | saved config/env |

## App Data

History DB:

```text
~/Library/Application Support/omnishot/history.db
```
