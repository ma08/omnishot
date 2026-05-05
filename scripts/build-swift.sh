#!/usr/bin/env bash
# Build the Swift DescribeImage helper
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SWIFT_DIR="$SCRIPT_DIR/../swift"

echo "Building DescribeImage Swift helper..."
cd "$SWIFT_DIR"
swift build -c release 2>&1

BINARY="$SWIFT_DIR/.build/release/DescribeImage"
if [ -f "$BINARY" ]; then
    echo "Built successfully: $BINARY"
else
    echo "Build failed: binary not found at $BINARY" >&2
    exit 1
fi
