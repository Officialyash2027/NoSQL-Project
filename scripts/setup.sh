#!/usr/bin/env bash
# One-time setup on Linux/macOS (or Git Bash on Windows). Usage: bash scripts/setup.sh [--dev]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3.11}
command -v "$PY" >/dev/null 2>&1 || PY=python3
[ -d .venv ] || "$PY" -m venv .venv
if [ -x .venv/bin/python ]; then VPY=.venv/bin/python; else VPY=.venv/Scripts/python; fi
"$VPY" -m pip install --upgrade pip
if [ "${1:-}" = "--dev" ]; then "$VPY" -m pip install -r requirements-dev.txt; else "$VPY" -m pip install -r requirements.txt; fi
[ -f .env ] || cp .env.example .env
mkdir -p data/models data/runtime results graphs
echo "Setup complete. Next: bash scripts/start.sh"
