#!/usr/bin/env bash
# bash scripts/run_experiments.sh [e1|e2|e3|e4|all] [quick|default|large] [--set key=json ...]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$ROOT/.venv/bin/python" ]; then VPY="$ROOT/.venv/bin/python"; else VPY="$ROOT/.venv/Scripts/python"; fi
EXP=${1:-all}; PROFILE=${2:-quick}; shift $(( $# > 2 ? 2 : $# ))
cd "$ROOT/backend"
PYTHONUNBUFFERED=1 exec "$VPY" -m app.experiments.runner "$EXP" --profile "$PROFILE" "$@"
