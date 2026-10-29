"""Load the deterministic demo dataset. Usage: python scripts/seed.py [--reset]"""
import json
import sys

from hanf_client import ApiError, call

try:
    r = call("/api/seed", {"reset": "--reset" in sys.argv})
except ApiError as e:
    print(e)
    sys.exit(1)
print(json.dumps(r, indent=2))
