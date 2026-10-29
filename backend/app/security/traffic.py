"""Reproducible synthetic request traffic for the security experiments and the live demo.

The same generator serves two purposes:
* **offline (E3)** - each synthetic request carries the outcome it *would* have on the seeded
  dataset (hit/miss, login success, payload size), so thousands of windows can be built instantly;
* **live demo** - the identical requests are executed through the real HANF pipeline against Redis,
  and the monitor uses the real outcomes and real byte counts.

Seeded dataset (see :func:`dataset_records`): ``user:0..N`` and ``product:0..M`` string values whose
size is a deterministic function of the key (100-400 bytes), plus ``cred:user<i>`` records for
synthetic login users (password ``pass-<i>``). Keys from ``product:100000`` upward never exist.

Request timestamps are *simulated client timestamps*: the generator draws inter-arrival times from
the window's request rate so the request-rate feature is realistic without waiting in real time.
Every generated request carries its ground-truth label (normal / injection / exfiltration /
bruteforce); that label is never shown as a detection - it is only used to score the detector.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterator

import numpy as np

from .features import RequestEvent, has_injection

ATTACK_KINDS = ("injection", "exfiltration", "bruteforce")
KINDS = ("normal",) + ATTACK_KINDS

BENIGN_LOOKALIKES = [
    "Ships from our Union Square store",
    "Free drop-off at the nearest locker",
    "Please select your size before checkout",
    "Union Jack themed mug -- limited edition",
    "Drop shipping available for this item",
]
INJECTION_PAYLOADS = [
    "user:1' OR '1'='1",
    "user:admin'--",
    'product:{"$ne": null}',
    'user:{"$gt": ""}',
    "product:1 UNION SELECT password FROM users",
    "user:1; DROP TABLE accounts",
    'user:{"$where": "sleep(1000)"}',
    "product:' or 1=1 --",
]


@dataclass
class DatasetSpec:
    n_users: int = 1000
    n_products: int = 5000
    n_login_users: int = 50
    missing_base: int = 100_000

    def value_size(self, key: str) -> int:
        return 100 + int.from_bytes(hashlib.md5(key.encode()).digest()[:4], "big") % 301

    def value_for(self, key: str) -> str:
        size = self.value_size(key)
        base = hashlib.sha256(key.encode()).hexdigest()
        return (key + "|" + base * (size // 64 + 2))[:size]

    def keys(self) -> list[str]:
        return [f"user:{i}" for i in range(self.n_users)] + [f"product:{i}" for i in range(self.n_products)]


def dataset_records(spec: DatasetSpec) -> Iterator[tuple[str, str]]:
    for k in spec.keys():
        yield k, spec.value_for(k)


def login_users(spec: DatasetSpec) -> list[tuple[str, str]]:
    return [(f"user{i}", f"pass-{i}") for i in range(spec.n_login_users)]


@dataclass
class SyntheticRequest:
    client_id: str
    ts: float
    operation: str
    key: str
    value: Any = None
    limit: int = 50
    label: str = "normal"
    expected_failed: bool = False
    expected_bytes: int = 0
    meta: dict = field(default_factory=dict)

    def to_api(self) -> dict:
        return {"operation": self.operation, "key": self.key, "value": self.value,
                "limit": self.limit, "client_id": self.client_id,
                "client_ts": self.ts, "synthetic_label": self.label}

    def offline_event(self) -> RequestEvent:
        return RequestEvent(self.client_id, self.ts, self.operation, self.key, self.expected_bytes,
                            self.expected_failed, has_injection(self.key, self.value), self.label)


class TrafficGenerator:
    def __init__(self, spec: DatasetSpec | None = None, seed: int = 42, window: int = 100):
        self.spec = spec or DatasetSpec()
        self.rng = np.random.default_rng(seed)
        self.window = window
        keys = self.spec.keys()
        perm = np.random.default_rng(seed + 1).permutation(len(keys))
        self._pool = [keys[i] for i in perm]
        ranks = np.arange(1, len(keys) + 1)
        p = 1.0 / ranks**0.8
        self._cdf = np.cumsum(p / p.sum())
        alphabet = np.array(list("abcdefghijklmnopqrstuvwxyz0123456789 "))
        buf_rng = np.random.default_rng(seed + 2)
        self._textbuf = "".join(alphabet[buf_rng.integers(0, len(alphabet), size=65_536)])
        self._counter = 0

    # ---------------------------------------------------------------- primitives
    def _hot_key(self) -> str:
        i = int(np.searchsorted(self._cdf, self.rng.random()))
        return self._pool[min(i, len(self._pool) - 1)]

    def _text(self, n: int) -> str:
        start = int(self.rng.integers(0, len(self._textbuf) - n))
        return self._textbuf[start:start + n]

    def _normal_rate(self) -> float:
        return float(np.clip(self.rng.lognormal(np.log(40), 0.35), 8, 120))

    def _read(self, cid, miss_p=0.03) -> SyntheticRequest:
        if self.rng.random() < miss_p:
            key = f"product:{self.spec.missing_base + int(self.rng.integers(0, 10**6))}"
            return SyntheticRequest(cid, 0, "read", key, expected_failed=True, expected_bytes=0)
        key = self._hot_key()
        return SyntheticRequest(cid, 0, "read", key, expected_bytes=self.spec.value_size(key))

    def _write(self, cid, op="write", text: str | None = None) -> SyntheticRequest:
        key = self._hot_key()
        size = int(self.rng.integers(100, 401))
        value = text if text is not None else self._text(size)
        return SyntheticRequest(cid, 0, op, key, value, expected_bytes=len(value.encode()))

    def _login(self, cid, wrong: bool, users: list[int] | None = None) -> SyntheticRequest:
        i = int(self.rng.choice(users)) if users else int(self.rng.integers(0, self.spec.n_login_users))
        pw = f"pass-{i}" if not wrong else "guess-" + self._text(6).replace(" ", "x")
        return SyntheticRequest(cid, 0, "login", f"user{i}", pw, expected_failed=wrong,
                                expected_bytes=len(pw))

    def _scan(self, cid, limit: int) -> SyntheticRequest:
        pattern = str(self.rng.choice(["product:*", "user:*", "product:1*", "user:2*"]))
        avg_len = 12 if pattern.startswith("product") else 8
        return SyntheticRequest(cid, 0, "scan", pattern, limit=limit, expected_bytes=limit * avg_len)

    def _normal_event(self, cid) -> SyntheticRequest:
        r = self.rng.random()
        if r < 0.70:
            return self._read(cid)
        if r < 0.87:
            return self._write(cid, "write")
        if r < 0.97:
            return self._write(cid, "update")
        return self._login(cid, wrong=self.rng.random() < 0.05)

    # ---------------------------------------------------------------- windows
    def make_window(self, kind: str = "normal", subtle: bool = False,
                    client_id: str | None = None, start_ts: float = 0.0,
                    intensity: float | None = None) -> list[SyntheticRequest]:
        """Build one window of ``self.window`` requests.

        ``intensity`` (0-1) overrides the attack strength for the evasion sweep: the share of
        injection requests, the share of scan requests, or the share of failed logins. With an
        intensity set, the rest of the window (rate, other requests) looks like normal traffic.
        """
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if intensity is not None and kind != "normal":
            return self._intensity_window(kind, float(intensity), client_id, start_ts)
        self._counter += 1
        cid = client_id or f"{kind}-{self._counter}"
        n = self.window
        rng = self.rng
        reqs: list[SyntheticRequest] = []
        rate = self._normal_rate()

        if kind == "normal":
            reqs = [self._normal_event(cid) for _ in range(n)]
            if rng.random() < 0.10:  # an occasional small admin listing
                for i in rng.choice(n, size=int(rng.integers(1, 3)), replace=False):
                    reqs[i] = self._scan(cid, limit=20)
            if rng.random() < 0.05:  # benign text that looks like an injection keyword
                for i in rng.choice(n, size=int(rng.integers(1, 5)), replace=False):
                    reqs[i] = self._write(cid, "write", str(rng.choice(BENIGN_LOOKALIKES)))
        elif kind == "injection":
            share = rng.uniform(0.06, 0.17) if subtle else rng.uniform(0.30, 0.80)
            reqs = [self._normal_event(cid) for _ in range(n)]
            for i in rng.choice(n, size=max(1, int(round(share * n))), replace=False):
                key = str(rng.choice(INJECTION_PAYLOADS))
                reqs[i] = SyntheticRequest(cid, 0, "read", key, expected_failed=True, expected_bytes=0)
        elif kind == "exfiltration":
            share = rng.uniform(0.12, 0.35) if subtle else rng.uniform(0.55, 0.95)
            limit_lo, limit_hi = (50, 120) if subtle else (100, 200)
            if not subtle:
                rate = float(rng.uniform(60, 200))
            reqs = []
            for _ in range(n):
                if rng.random() < share:
                    reqs.append(self._scan(cid, int(rng.integers(limit_lo, limit_hi + 1))))
                else:
                    reqs.append(self._read(cid, miss_p=0.0))
        elif kind == "bruteforce":
            if subtle:
                login_share, wrong_p = rng.uniform(0.5, 0.8), rng.uniform(0.45, 0.6)
                rate = float(rng.uniform(70, 140))
                users = list(rng.choice(self.spec.n_login_users, size=int(rng.integers(2, 7)), replace=False))
            else:
                login_share, wrong_p = rng.uniform(0.85, 1.0), rng.uniform(0.9, 0.99)
                rate = float(rng.uniform(160, 400))
                users = list(rng.choice(self.spec.n_login_users, size=int(rng.integers(1, 4)), replace=False))
            reqs = [self._login(cid, rng.random() < wrong_p, users) if rng.random() < login_share
                    else self._normal_event(cid) for _ in range(n)]

        gaps = rng.exponential(1.0 / rate, size=n)
        ts = start_ts + np.cumsum(gaps)
        for r, t in zip(reqs, ts):
            r.ts, r.label, r.client_id = float(t), kind, cid
            r.meta = {"subtle": bool(subtle), "window_rate": round(rate, 2)}
        return reqs

    def _intensity_window(self, kind: str, share: float, client_id: str | None,
                          start_ts: float) -> list[SyntheticRequest]:
        self._counter += 1
        cid = client_id or f"{kind}-i{self._counter}"
        n, rng = self.window, self.rng
        reqs = [self._normal_event(cid) for _ in range(n)]
        k = max(1, int(round(share * n)))
        for i in rng.choice(n, size=k, replace=False):
            if kind == "injection":
                reqs[i] = SyntheticRequest(cid, 0, "read", str(rng.choice(INJECTION_PAYLOADS)),
                                           expected_failed=True)
            elif kind == "exfiltration":
                reqs[i] = self._scan(cid, int(rng.integers(50, 121)))
            else:  # bruteforce: failed logins against a few accounts
                reqs[i] = self._login(cid, wrong=True, users=[int(rng.integers(0, self.spec.n_login_users))])
        rate = self._normal_rate()
        ts = start_ts + np.cumsum(rng.exponential(1.0 / rate, size=n))
        for r, t in zip(reqs, ts):
            r.ts, r.label, r.client_id = float(t), kind, cid
            r.meta = {"intensity": share, "window_rate": round(rate, 2)}
        return reqs

    def dataset(self, n_normal: int, n_per_attack: int, subtle_frac: float = 0.3
                ) -> tuple[list[list[SyntheticRequest]], list[str], list[bool]]:
        """Windows plus labels and subtle flags, shuffled deterministically."""
        windows, labels, subtle = [], [], []
        for _ in range(n_normal):
            windows.append(self.make_window("normal"))
            labels.append("normal")
            subtle.append(False)
        for kind in ATTACK_KINDS:
            n_sub = int(round(subtle_frac * n_per_attack))
            for j in range(n_per_attack):
                s = j < n_sub
                windows.append(self.make_window(kind, subtle=s))
                labels.append(kind)
                subtle.append(s)
        order = self.rng.permutation(len(windows))
        return [windows[i] for i in order], [labels[i] for i in order], [subtle[i] for i in order]
