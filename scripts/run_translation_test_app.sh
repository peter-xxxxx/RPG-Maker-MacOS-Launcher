#!/usr/bin/env bash
# Launch the launcher from source for testing the translation feature.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
    echo "Test environment not found. Running setup first..."
    ./scripts/setup_translation_test_env.sh
fi

echo "Starting RPG Maker Launcher test app..."
exec .venv/bin/python main.py
