"""Tiny stdlib HTTP client shared by the helper scripts (no third-party packages needed)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("HANF_URL", "http://127.0.0.1:8000")


class ApiError(RuntimeError):
    pass


def call(path: str, body: dict | None = None, method: str | None = None, timeout: float = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ctype = r.headers.get("content-type", "")
            return json.loads(raw) if "json" in ctype else raw
    except urllib.error.HTTPError as e:
        raise ApiError(f"{method or 'GET'} {path} -> {e.code}: {e.read().decode()[:300]}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"cannot reach {BASE}{path}: {e.reason}. Is the server running (scripts/start)?") from None
