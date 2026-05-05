# Architecture

```mermaid
flowchart TD
    A["User triggers macOS screenshot shortcut"] --> B["macOS writes PNG into watch directory"]
    B --> C["watchdog event handler receives file event"]
    C --> D["Batch window groups multi-monitor captures"]

    D --> E["Per-file pipeline starts"]
    E --> F["Enrich step"]
    F --> F1["Swift helper: Vision OCR + Foundation Models structured generation"]
    F --> F2["Python fallback: PyObjC Vision OCR + keyword extraction"]

    F1 --> G["Build semantic filename"]
    F2 --> G
    G --> H["Rename local file"]
    H --> I["Upload object to S3"]
    I --> J["Generate presigned URL"]
    J --> K["Copy configured default payload to clipboard"]
    J --> L["Post macOS notification"]
    J --> M["Write record to SQLite history"]

    N["Menu-bar app"] --> O["Load recent history"]
    O --> P["Actions: S3 URL, path reference, image, public link, reveal"]
    O --> Q["Global shortcuts temporarily write payload, post Cmd+V, restore clipboard"]
```

## Naming Pipeline

The primary path uses Apple Vision OCR and Apple Foundation Models through the
Swift helper in `swift/Sources/DescribeImage.swift`.

The fallback path uses Vision through PyObjC and deterministic keyword
extraction in `src/omnishot/enrich.py`.

## Routing Pipeline

The file is always kept locally. S3 and public links are optional transports.
The default post-capture payload is `path-ref`, which produces:

```text
screenshot-info:
  machine: <ssh-alias>
  path: <absolute-local-path>
```

Remote agents can copy that file over SSH into durable task artifacts. Local apps
can use the direct image paste path instead.

## Data Stores

- History: `~/Library/Application Support/omnishot/history.db`
- Menubar config: `~/Library/Application Support/omnishot/config.json`

The app-support path is intentionally scoped to Omnishot so local history and
menu-bar settings stay separate from earlier experiments.
