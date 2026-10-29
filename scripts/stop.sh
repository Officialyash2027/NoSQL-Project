#!/usr/bin/env bash
# Stop containers (Ctrl+C stops the API server).  bash scripts/stop.sh [--remove]
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--remove" ]; then docker compose --profile full down; else docker compose --profile full stop; fi
