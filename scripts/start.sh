#!/usr/bin/env bash
# Start HANF on Linux/macOS.  bash scripts/start.sh [--full|--emulated] [port]
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=${1:-}
PORT=${2:-8000}
if [ -x .venv/bin/python ]; then VPY=.venv/bin/python; else VPY=.venv/Scripts/python; fi
if [ "$MODE" = "--emulated" ]; then
  export HANF_REDIS_MODE=emulated HANF_MONGO_ENABLED=false HANF_CASSANDRA_ENABLED=false
  echo "EMULATED mode: Redis is simulated in-process (fakeredis). Results are NOT live measurements."
else
  if [ "$MODE" = "--full" ]; then docker compose --profile full up -d; else docker compose up -d; fi
  for i in $(seq 1 30); do
    n=$(docker ps --filter "name=hanf-redis" --filter "health=healthy" -q | wc -l)
    [ "$n" -ge 5 ] && break; sleep 1
  done
  echo "Redis shards healthy: $n/5"
fi
export PYTHONUNBUFFERED=1
echo "Dashboard: http://127.0.0.1:$PORT/   API docs: http://127.0.0.1:$PORT/docs"
exec "$VPY" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT"
