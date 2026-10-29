"""Return the running system to a clean demo state:
shards back to the initial four, dataset re-seeded (FLUSHDB of the HANF DB), security history and
telemetry counters cleared, consistency controller back to 1x congestion.
Usage: python scripts/reset_demo.py"""
import sys

from hanf_client import ApiError, call

try:
    r = call("/api/shards/rebalance", {"action": "reset"})
    print("shards:", r.get("status"), "- active now:", call("/api/shards")["topology"]["active"])
    s = call("/api/seed", {"reset": True})
    print("seeded:", s["redis"]["records"], "Redis records;", "MongoDB", s["mongodb"], "; Cassandra", s["cassandra"])
    call("/api/security/clear", method="POST")
    call("/api/telemetry/reset", method="POST")
    c = call("/api/consistency/network", {"congestion": 1})
    print("consistency profile:", c["profile"])
    print("Demo state reset.")
except ApiError as e:
    print(e)
    sys.exit(1)
