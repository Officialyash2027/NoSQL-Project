"""Print service health. Exit code 0 = Redis path healthy, 1 = degraded/unreachable.
Usage: .venv\\Scripts\\python scripts\\healthcheck.py"""
import sys

from hanf_client import ApiError, call

try:
    h = call("/health")
    adapters = call("/api/adapters?deep=true")["adapters"]
except ApiError as e:
    print(f"UNREACHABLE: {e}")
    sys.exit(1)

print(f"overall: {h['status']}   redis mode: {h['redis_mode']}   dataset seeded: {h['seeded']}")
for a in adapters:
    rt = "verified round trip" if a["verified_round_trip"] else "no round trip"
    print(f"  {a['engine']:<10} {a['status']:<12} {a.get('version') or '-':<10} {rt:<20} {a['detail']}")
shards = call("/api/shards")["health"]
for s in shards:
    print(f"  {s['id']} :{s['port']}  {'up' if s['up'] else 'DOWN'}  {'active' if s['active'] else 'spare '}  keys={s['keys']}")
sys.exit(0 if h["status"] == "ok" else 1)
