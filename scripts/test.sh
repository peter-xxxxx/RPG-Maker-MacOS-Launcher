#!/usr/bin/env bash
# Run the complete translation test suite (Python backend + runtime plugin).
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]]; then
    PYTHON=.venv/bin/python
else
    PYTHON="${PYTHON:-python3}"
fi

if ! command -v node >/dev/null 2>&1; then
    echo "Node.js 18+ is required to run the runtime plugin tests." >&2
    exit 1
fi

echo "Running Python translation tests..."
"$PYTHON" -m unittest discover -s tests -v

echo
echo "Running runtime plugin tests..."
node tests/test_translate_plugin.js
node tests/test_translate_plugin_scenarios.js

echo
echo "All translation tests passed."
