#!/usr/bin/env bash
# Set up a local Python 3.11 venv for testing the launcher from source.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v mise >/dev/null 2>&1; then
    echo "mise is required: https://mise.jdx.dev/getting-started.html" >&2
    exit 1
fi

echo "Installing the Python version pinned in mise.toml..."
mise install

if [[ ! -x .venv/bin/python ]]; then
    echo "Creating .venv with the mise-managed Python..."
    mise exec -- python -m venv .venv
fi

echo "Installing launcher dependencies into .venv..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install PySide6-Essentials requests chardet evbunpack

echo
echo "Test environment is ready."
echo "Launch it with: ./scripts/run_translation_test_app.sh"
