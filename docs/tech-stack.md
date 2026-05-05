# Omnishot Tech Stack (Exact, Cited)

This document is the implementation-level source of truth for technology choices in this repo.
`README.md` contains the user-facing summary; this file contains deeper technical detail.

## 1) AI Naming Pipeline

### 1.1 Foundation Models usage

- The project uses Apple Foundation Models via Swift `LanguageModelSession` [1][2].
- Structured output is requested using `@Generable` schema types with `@Guide` constraints [3].
- The helper currently requests two structured fields:
  - `chain_of_thought`
  - `slug`
- Code location:
  - `swift/Sources/DescribeImage.swift`

### 1.2 Modality (what is actually sent to the model)

- Current flow is **OCR text -> model text generation**.
- The helper does Vision OCR first, truncates OCR text, and sends that text as prompt input to `LanguageModelSession`.
- The current implementation does **not** pass image pixels directly into Foundation Models generation.
- Code location:
  - `swift/Sources/DescribeImage.swift` (`RecognizeTextRequest` + prompt assembly + `session.respond(...)`)

### 1.3 Model size/variant visibility

- Runtime output reports model as `apple-foundation-models`.
- This app path does not expose a concrete runtime parameter count or model variant identifier.
- Apple has publicly described Foundation Models as integrated with an on-device 3B-parameter model, but that is framework-level context and not a per-call model-size value emitted by this app [15].

## 2) OCR Stack

### 2.1 Primary OCR (Swift helper path)

- OCR is performed using Apple Vision text recognition APIs (`RecognizeTextRequest` / `VNRecognizeTextRequest`) [4].
- This OCR output feeds the Foundation Models prompt.
- Code location:
  - `swift/Sources/DescribeImage.swift`

### 2.2 Fallback OCR (Python path)

- If Foundation Models path is disabled/unavailable/fails, Python fallback OCR uses Vision via PyObjC (`pyobjc-framework-Vision`) [4][8].
- Then a deterministic keyword extraction pass generates filename text.
- Code location:
  - `src/omnishot/enrich.py`

## 3) Runtime App UX Stack

### 3.1 Menu bar GUI

- GUI is AppKit-based status bar/menu runtime via PyObjC:
  - `NSStatusBar`
  - `NSStatusItem`
  - `NSMenu`
- Code location:
  - `src/omnishot/menubar.py`
- References: [5][6][8]

### 3.2 Clipboard behavior

- URL copy path: `pbcopy` subprocess.
- Image/file copy path: AppKit `NSPasteboard` payloads, including Finder-compatible file list and `code/file-list` for editor file explorers.
- Default post-capture clipboard payload is configurable through saved app config, CLI flags, and env:
  - `path-ref`: compact machine SSH alias + absolute-path payload
  - `s3-url`: presigned URL payload
- Code location:
  - `src/omnishot/paste.py`
  - `src/omnishot/notify.py`
  - `src/omnishot/watcher.py`
  - `src/omnishot/menubar.py`
- Reference: [7]

### 3.3 Global paste shortcuts

- Uses a Quartz session event tap for global V-variant shortcut detection.
- Shortcut payloads:
  - `Cmd+Option+V`: latest path reference
  - `Cmd+Shift+Option+V`: latest image payload
  - `Cmd+Control+Option+V`: latest S3 URL
  - `Cmd+Control+Shift+Option+V`: latest public link
- The menubar app snapshots the pasteboard, writes the requested payload, posts a synthetic `Cmd+V`, then restores the prior pasteboard when it has not changed.
- Requires macOS Input Monitoring and Accessibility permissions.
- Code location:
  - `src/omnishot/menubar.py`
- References: [16][17][18]

### 3.4 Notifications

- Primary path: `terminal-notifier` CLI.
- Fallback path: AppleScript `display notification` via `osascript`.
- Code location:
  - `src/omnishot/notify.py`
- References: [13][14]

## 4) Filesystem, Upload, and Persistence

### 4.1 File watching

- Uses Python `watchdog` (`Observer`, filesystem event handlers) for screenshot detection and batching.
- Code location:
  - `src/omnishot/watcher.py`
- Reference: [9]

### 4.2 S3 upload and share links

- Upload: Boto3 S3 `upload_file`.
- Share links: Boto3 `generate_presigned_url`.
- Public-link path supports optional ACL flow when enabled.
- Code location:
  - `src/omnishot/upload.py`
  - `src/omnishot/menubar.py`
- References: [10][11]

### 4.3 Local history

- Uses `sqlite3` for local upload history and link refresh bookkeeping.
- Machine identity prefers `SYSTEM_NAME`, then `~/pro/botfiles/secrets/local/machine.rc`, then hostname.
- Code location:
  - `src/omnishot/history.py`

### 4.4 Local app config

- Uses JSON under `~/Library/Application Support/omnishot/config.json`.
- Persists default paste mode and optional SSH host hint for the path-ref `machine` token.
- Code location:
  - `src/omnishot/paste.py`

## 5) Observability

- Uses Langfuse Python SDK observations for span/generation tracing, with OpenTelemetry-backed timing/span data.
- Key traced stages include:
  - screenshot pipeline
  - enrich
  - OCR
  - Foundation Models generation
  - structured-generation/fallback spans
- Code location:
  - `src/omnishot/tracing.py`
  - `src/omnishot/watcher.py`
  - `src/omnishot/enrich.py`
- Reference: [12]

## 6) Known Unknowns / Explicit Non-Claims

- The app does not currently expose an authoritative per-call model size/version identifier beyond `apple-foundation-models`.
- The app does not currently execute a direct multimodal image-to-text Foundation Models call path; OCR mediates image understanding first.
- macOS may identify the Python/uv runtime in permission prompts for global shortcuts; users must grant the requested Input Monitoring and Accessibility permissions to the process macOS shows.
- If model-selection metadata becomes available in future SDK/runtime versions, this file should be updated.

## References

- [1] Apple Foundation Models overview: https://developer.apple.com/documentation/foundationmodels
- [2] Apple `LanguageModelSession`: https://developer.apple.com/documentation/foundationmodels/languagemodelsession
- [3] Apple `Generable`: https://developer.apple.com/documentation/foundationmodels/generable
- [4] Apple Vision text recognition (`VNRecognizeTextRequest`): https://developer.apple.com/documentation/vision/vnrecognizetextrequest
- [5] Apple AppKit `NSStatusBar`: https://developer.apple.com/documentation/appkit/nsstatusbar
- [6] Apple AppKit `NSStatusItem`: https://developer.apple.com/documentation/appkit/nsstatusitem
- [7] Apple AppKit `NSPasteboard`: https://developer.apple.com/documentation/appkit/nspasteboard
- [8] PyObjC docs: https://pyobjc.readthedocs.io/en/latest/
- [9] watchdog docs: https://python-watchdog.readthedocs.io/en/stable/
- [10] Boto3 S3 `upload_file`: https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/upload_file.html
- [11] Boto3 S3 `generate_presigned_url`: https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/generate_presigned_url.html
- [12] Langfuse Python API docs: https://python.reference.langfuse.com/langfuse
- [13] `terminal-notifier`: https://github.com/julienXX/terminal-notifier
- [14] AppleScript notifications (`display notification`): https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/DisplayNotifications.html
- [15] Apple Newsroom (Foundation Models framework, 3B on-device model note): https://www.apple.com/newsroom/2025/09/apples-foundation-models-framework-unlocks-new-intelligent-app-experiences/
- [16] Apple Core Graphics `CGEventTapCreate`: https://developer.apple.com/documentation/coregraphics/cgevent/tapcreate%28tap%3Aplace%3Aoptions%3Aeventsofinterest%3Acallback%3Auserinfo%3A%29?language=objc
- [17] Apple Core Graphics `CGRequestListenEventAccess`: https://developer.apple.com/documentation/coregraphics/cgrequestlisteneventaccess%28%29
- [18] Apple Core Graphics `CGPreflightPostEventAccess`: https://developer.apple.com/documentation/coregraphics/cgpreflightposteventaccess%28%29
