#!/usr/bin/env bash
# Convenience wrapper for double-clicking from Finder.
set -euo pipefail

cd "$(dirname "$0")/.."
exec ./scripts/run_translation_test_app.sh
