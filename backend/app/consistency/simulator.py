"""Quorum-replication latency/staleness model and the Adaptive Consistency Controller.

Model (Review II, Section 6.4)
------------------------------
* N replicas, one per region. Writes are coordinated from the writer's region, reads from the
  reader's region. ``writer_rtt[i]`` / ``reader_rtt[i]`` are mean round-trip times (ms) to replica i.
* Each sampled RTT = base x congestion (remote links only) x LogNormal(0, sigma).
* A write is applied at replica i after one-way delay rtt_w[i]/2 and is acknowledged when W replicas
  have responded:  write_latency = W-th smallest rtt_w.
* A read is issued Exp(read_delay_mean) ms after the acknowledgement, contacts the R replicas closest
  to the reader (by base RTT) and returns when the slowest of them responds:
  read_latency = max over contacted rtt_r.
* The read reaches replica j at t_read + rtt_r[j]/2. It is **stale** if no contacted replica has
  applied the write by then. With W + R > N the read set always overlaps the acknowledged write
  set, so the stale probability is 0 by construction.

Controller
----------
For each of the seven (W, R) profiles it predicts mean operation latency and stale-read probability
by Monte-Carlo sampling at the current congestion estimate, then picks the lowest-latency profile
whose predicted staleness is <= the SLO; if none qualifies it falls back to ALL/ALL.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

LEVELS = ("ONE", "QUORUM", "ALL")
PROFILES: list[tuple[str, str]] = [("ONE", "ONE"), ("ONE", "QUORUM"), ("QUORUM", "ONE"),
                                   ("QUORUM", "QUORUM"), ("ONE", "ALL"), ("ALL", "ONE"), ("ALL", "ALL")]
DEFAULT_PHASES = [1, 1, 1, 3, 3, 6, 6, 3, 1, 1, 1, 6, 1, 1, 3, 1]


def level_count(level: str, n: int) -> int:
    level = level.upper()
    if level == "ONE":
        return 1
    if level == "QUORUM":
        return n // 2 + 1
    if level == "ALL":
        return n
    raise ValueError(f"unknown consistency level {level!r}")


def profile_name(p: tuple[str, str]) -> str:
    return f"{p[0]}/{p[1]}"


@dataclass
class NetworkModel:
    regions: list[str] = field(default_factory=lambda: ["A", "B", "C"])
    writer_rtt_ms: list[float] = field(default_factory=lambda: [2.0, 30.0, 110.0])
    reader_rtt_ms: list[float] = field(default_factory=lambda: [30.0, 2.0, 80.0])
    jitter_sigma: float = 0.25
    read_delay_mean_ms: float = 15.0
    local_threshold_ms: float = 5.0   # links at or below this base RTT are local (no congestion)

    def __post_init__(self):
        n = len(self.regions)
        if len(self.writer_rtt_ms) != n or len(self.reader_rtt_ms) != n:
            raise ValueError("writer_rtt_ms and reader_rtt_ms need one entry per region")
        if n < 1:
            raise ValueError("at least one replica is required")

    @property
    def n(self) -> int:
        return len(self.regions)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SampleResult:
    write_ms: np.ndarray
    read_ms: np.ndarray
    stale: np.ndarray
    remote_ratio: float   # observed remote RTT / base RTT (median) - the congestion signal

    def summary(self) -> dict:
        ops = np.concatenate([self.write_ms, self.read_ms])
        return {"write_ms": float(self.write_ms.mean()), "read_ms": float(self.read_ms.mean()),
                "mean_ms": float(ops.mean()), "p95_ms": float(np.percentile(ops, 95)),
                "stale_pct": float(self.stale.mean() * 100)}


def _rtt(base: np.ndarray, congestion: float, sigma: float, n: int, local_thr: float,
         rng: np.random.Generator) -> np.ndarray:
    mult = np.where(base <= local_thr, 1.0, congestion)
    return base * mult * rng.lognormal(0.0, sigma, size=(n, base.size))


def sample(model: NetworkModel, w_level: str, r_level: str, congestion: float, n: int,
           rng: np.random.Generator) -> SampleResult:
    """Simulate ``n`` write-then-read pairs under one (W, R) profile."""
    N = model.n
    W, R = level_count(w_level, N), level_count(r_level, N)
    wb = np.asarray(model.writer_rtt_ms, dtype=float)
    rb = np.asarray(model.reader_rtt_ms, dtype=float)
    rtt_w = _rtt(wb, congestion, model.jitter_sigma, n, model.local_threshold_ms, rng)
    rtt_r = _rtt(rb, congestion, model.jitter_sigma, n, model.local_threshold_ms, rng)
    applied_at = rtt_w / 2.0
    ack = np.sort(rtt_w, axis=1)[:, W - 1]
    t_read = ack + rng.exponential(model.read_delay_mean_ms, size=n)
    contacted = np.argsort(rb, kind="stable")[:R]
    read_lat = rtt_r[:, contacted].max(axis=1)
    arrive = t_read[:, None] + rtt_r[:, contacted] / 2.0
    fresh = (applied_at[:, contacted] <= arrive).any(axis=1)
    remote = wb > model.local_threshold_ms
    ratio = float(np.median(rtt_w[:, remote] / wb[remote])) if remote.any() else 1.0
    return SampleResult(ack, read_lat, ~fresh, ratio)


@dataclass
class ProfilePrediction:
    profile: str
    write_ms: float
    read_ms: float
    mean_ms: float
    p95_ms: float
    stale_prob: float
    eligible: bool


class AdaptiveConsistencyController:
    def __init__(self, model: NetworkModel | None = None, slo: float = 0.01, samples: int = 4000,
                 seed: int = 7, profiles: list[tuple[str, str]] | None = None, telemetry=None):
        self.model = model or NetworkModel()
        self.slo = slo
        self.samples = samples
        self.profiles = profiles or PROFILES
        self.rng = np.random.default_rng(seed)
        self.telemetry = telemetry
        self.congestion_estimate = 1.0
        self.current: tuple[str, str] = ("QUORUM", "QUORUM")
        self.last_predictions: list[ProfilePrediction] = []
        self.fallback_used = False
        self.updated_at: float | None = None
        self._lock = threading.Lock()

    def predict(self, congestion: float) -> list[ProfilePrediction]:
        out = []
        for p in self.profiles:
            s = sample(self.model, p[0], p[1], congestion, self.samples, self.rng).summary()
            stale = s["stale_pct"] / 100
            out.append(ProfilePrediction(profile_name(p), s["write_ms"], s["read_ms"], s["mean_ms"],
                                         s["p95_ms"], stale, stale <= self.slo))
        return out

    def select(self, congestion: float) -> tuple[tuple[str, str], list[ProfilePrediction], bool]:
        preds = self.predict(congestion)
        eligible = [pr for pr in preds if pr.eligible]
        if eligible:
            best = min(eligible, key=lambda pr: pr.mean_ms)
            chosen = tuple(best.profile.split("/"))
            return chosen, preds, False  # type: ignore[return-value]
        return ("ALL", "ALL"), preds, True

    def update(self, congestion_estimate: float, source: str = "manual") -> dict:
        """Re-select the profile for a new congestion estimate and publish it on the bus."""
        if congestion_estimate <= 0:
            raise ValueError("congestion estimate must be positive")
        with self._lock:
            chosen, preds, fb = self.select(congestion_estimate)
            self.congestion_estimate = congestion_estimate
            self.current, self.last_predictions, self.fallback_used = chosen, preds, fb
            self.updated_at = time.time()
        state = self.state()
        if self.telemetry:
            self.telemetry.publish("consistency_profile", "consistency", profile=state["profile"],
                                   write_level=chosen[0], read_level=chosen[1],
                                   congestion_estimate=congestion_estimate, slo=self.slo,
                                   fallback=fb, trigger=source)
        return state

    def level_for(self, operation: str) -> str:
        w, r = self.current
        return r if operation in ("read", "query", "scan", "login") else w

    def state(self) -> dict[str, Any]:
        return {"profile": profile_name(self.current), "write_level": self.current[0],
                "read_level": self.current[1], "congestion_estimate": self.congestion_estimate,
                "slo": self.slo, "fallback_used": self.fallback_used, "updated_at": self.updated_at,
                "predictions": [asdict(p) for p in self.last_predictions],
                "model": self.model.to_dict(), "samples": self.samples}


# --------------------------------------------------------------------------- timeline experiment
def run_timeline(model: NetworkModel, phases: list[float], ops_per_phase: int = 4000,
                 slo: float = 0.01, mc_samples: int = 4000, seed: int = 2024,
                 include_oracle: bool = True) -> dict[str, Any]:
    """Run the phase timeline under the static strategies and the adaptive controller.

    Each phase has ``ops_per_phase`` operations: half writes, half reads (each read follows a write).
    The adaptive controller selects at the start of each phase using the congestion it *observed*
    in the previous phase (one-phase lag). The optional oracle variant sees the true congestion of
    the current phase and is reported only as an upper bound on what better estimation could give.
    """
    pairs = max(1, ops_per_phase // 2)
    strategies: dict[str, Any] = {f"static {profile_name(p)}": p
                                  for p in [("ONE", "ONE"), ("QUORUM", "QUORUM"), ("ALL", "ALL")]}
    strategies["HANF adaptive"] = "adaptive"
    if include_oracle:
        strategies["adaptive (oracle)"] = "oracle"

    results: dict[str, Any] = {}
    phase_rows: list[dict] = []
    for si, (name, strat) in enumerate(strategies.items()):
        rng = np.random.default_rng(seed + 1000 * si)  # independent stream per strategy
        ctrl = AdaptiveConsistencyController(model, slo, mc_samples, seed + 7 + si)
        estimate = 1.0
        w_all, r_all, s_all = [], [], []
        per_phase = []
        for i, c in enumerate(phases):
            fb = False
            pred = None
            est_used = float(c) if strat == "oracle" else estimate
            if strat in ("adaptive", "oracle"):
                prof, preds, fb = ctrl.select(est_used)
                pred = next(p for p in preds if p.profile == profile_name(prof))
            else:
                prof = strat
            res = sample(model, prof[0], prof[1], float(c), pairs, rng)
            estimate = res.remote_ratio  # what the controller observes for the next phase
            w_all.append(res.write_ms); r_all.append(res.read_ms); s_all.append(res.stale)
            summ = res.summary()
            row = {"strategy": name, "phase": i, "congestion": c, "profile": profile_name(prof),
                   "observed_mean_ms": round(summ["mean_ms"], 3),
                   "observed_stale_pct": round(summ["stale_pct"], 4),
                   "observed_congestion_estimate": round(res.remote_ratio, 3), "fallback": fb}
            if pred is not None:
                row |= {"predicted_mean_ms": round(pred.mean_ms, 3),
                        "predicted_stale_pct": round(pred.stale_prob * 100, 4),
                        "estimate_used": round(est_used, 3)}
            per_phase.append(row)
            phase_rows.append(row)
        W, Rr, S = np.concatenate(w_all), np.concatenate(r_all), np.concatenate(s_all)
        ops = np.concatenate([W, Rr])
        results[name] = {
            "write_ms": round(float(W.mean()), 3), "read_ms": round(float(Rr.mean()), 3),
            "mean_ms": round(float(ops.mean()), 3), "p95_ms": round(float(np.percentile(ops, 95)), 3),
            "stale_pct": round(float(S.mean() * 100), 4),
            "phases_over_slo": int(sum(r["observed_stale_pct"] > slo * 100 for r in per_phase)),
            "profile_counts": {p: sum(r["profile"] == p for r in per_phase)
                               for p in sorted({r["profile"] for r in per_phase})},
        }
    return {"strategies": results, "phases": phase_rows,
            "config": {"model": model.to_dict(), "phases": phases, "ops_per_phase": ops_per_phase,
                       "slo": slo, "mc_samples": mc_samples, "seed": seed}}
