"""Experiment result store and run metadata.

Each run is written to ``results/<experiment>/<run_id>/`` containing
* ``config.json``   - the parameters used,
* ``metadata.json`` - environment (hardware, OS, Python/library/engine versions, git commit, seeds),
* ``results.json``  - machine-readable results (including any failures),
* ``*.csv``         - tables used for graphs and the report,
* ``*.png``         - graphs generated from those same tables.
The latest graph for each experiment is also copied to ``graphs/``.
"""
from __future__ import annotations

import csv
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

EXPERIMENTS = {
    "e1": "Throughput and latency (YCSB-style workloads A/B/C)",
    "e2": "Adaptive sharding (remapping, rebalancing, live migration)",
    "e3": "ML security monitor (Isolation Forest vs static rules)",
    "e4": "Adaptive consistency (simulation)",
    "smoke": "Integrated end-to-end smoke test",
}

EVIDENCE = {
    "e1": "live Redis measurement",
    "e2": "algorithmic simulation + live Redis migration",
    "e3": "synthetic-data evaluation",
    "e4": "simulation (no live multi-region cluster)",
    "smoke": "live pipeline check",
}


def _git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:  # noqa: BLE001
            pass
    return platform.processor() or platform.machine()


def _memory_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2**30, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except Exception:  # noqa: BLE001
        return None


def os_name() -> str:
    """platform.release() reports '10' on Windows 11; builds >= 22000 are Windows 11."""
    system, release, version = platform.system(), platform.release(), platform.version()
    if system == "Windows":
        try:
            if int(version.split(".")[2]) >= 22000:
                release = "11"
        except (IndexError, ValueError):
            pass
    return f"{system} {release} ({version})"


def environment(root: Path, engines: dict[str, Any] | None = None) -> dict[str, Any]:
    import fastapi
    import matplotlib
    import numpy
    import pandas
    import redis
    import sklearn

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "os": os_name(),
        "cpu": _cpu_name(),
        "logical_cpus": os.cpu_count(),
        "memory_gb": _memory_gb(),
        "python": sys.version.split()[0],
        "libraries": {"numpy": numpy.__version__, "pandas": pandas.__version__,
                      "scikit-learn": sklearn.__version__, "redis-py": redis.__version__,
                      "matplotlib": matplotlib.__version__, "fastapi": fastapi.__version__},
        "engines": engines or {},
        "git_commit": _git_commit(root),
    }


class ExperimentStore:
    def __init__(self, results_dir: Path, graphs_dir: Path):
        self.results_dir = results_dir
        self.graphs_dir = graphs_dir

    def new_run(self, experiment: str, config: dict, env: dict) -> "RunWriter":
        if experiment not in EXPERIMENTS:
            raise ValueError(f"unknown experiment {experiment}")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = f"{experiment}-{stamp}-{uuid.uuid4().hex[:6]}"
        d = self.results_dir / experiment / run_id
        d.mkdir(parents=True, exist_ok=True)
        return RunWriter(self, experiment, run_id, d, config, env)

    def list_runs(self, experiment: str | None = None) -> list[dict]:
        rows = []
        exps = [experiment] if experiment else list(EXPERIMENTS)
        for e in exps:
            base = self.results_dir / e
            if not base.exists():
                continue
            for d in base.iterdir():
                f = d / "results.json"
                if not f.exists():
                    continue
                try:
                    r = json.loads(f.read_text())
                except ValueError:
                    continue
                rows.append({"run_id": d.name, "experiment": e, "title": EXPERIMENTS[e],
                             "status": r.get("status"), "evidence": r.get("evidence"),
                             "started_at": r.get("started_at"), "duration_s": r.get("duration_s"),
                             "profile": r.get("config", {}).get("profile"),
                             "files": sorted(p.name for p in d.iterdir() if p.is_file())})
        return sorted(rows, key=lambda r: r["started_at"] or 0, reverse=True)

    def run_dir(self, run_id: str) -> Path:
        exp = run_id.split("-", 1)[0]
        d = (self.results_dir / exp / run_id).resolve()
        if self.results_dir.resolve() not in d.parents or not d.exists():
            raise FileNotFoundError(run_id)
        return d

    def get_run(self, run_id: str) -> dict:
        d = self.run_dir(run_id)
        data = json.loads((d / "results.json").read_text())
        data["files"] = sorted(p.name for p in d.iterdir() if p.is_file())
        return data

    def zip_run(self, run_id: str) -> bytes:
        d = self.run_dir(run_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(d.iterdir()):
                if p.is_file():
                    z.write(p, arcname=f"{run_id}/{p.name}")
        return buf.getvalue()

    def latest(self, experiment: str, status: str = "completed") -> dict | None:
        for r in self.list_runs(experiment):
            if r["status"] == status:
                return self.get_run(r["run_id"])
        return None


class RunWriter:
    def __init__(self, store: ExperimentStore, experiment: str, run_id: str, path: Path,
                 config: dict, env: dict):
        self.store, self.experiment, self.run_id, self.path = store, experiment, run_id, path
        self.config, self.env = config, env
        self.started = time.time()
        self.failures: list[dict] = []
        self.graphs: list[str] = []
        (path / "config.json").write_text(json.dumps(config, indent=2, default=str))
        (path / "metadata.json").write_text(json.dumps(env, indent=2, default=str))

    def write_csv(self, name: str, rows: Iterable[dict]) -> Path:
        rows = list(rows)
        p = self.path / name
        if not rows:
            p.write_text("")
            return p
        fields = list(dict.fromkeys(k for r in rows for k in r))
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
        return p

    def add_graph(self, fig, name: str) -> Path:
        p = self.path / name
        fig.savefig(p, dpi=160, bbox_inches="tight", facecolor=fig.get_facecolor())
        self.store.graphs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, self.store.graphs_dir / name)
        self.graphs.append(name)
        import matplotlib.pyplot as plt

        plt.close(fig)
        return p

    def record_failure(self, step: str, error: str) -> None:
        self.failures.append({"step": step, "error": error, "at": time.time()})

    def finish(self, results: dict, status: str | None = None) -> dict:
        status = status or ("completed" if not self.failures else "completed_with_failures")
        doc = {"run_id": self.run_id, "experiment": self.experiment,
               "title": EXPERIMENTS[self.experiment], "evidence": EVIDENCE[self.experiment],
               "status": status, "started_at": self.started,
               "duration_s": round(time.time() - self.started, 2), "config": self.config,
               "environment": self.env, "failures": self.failures, "graphs": self.graphs,
               "results": results}
        (self.path / "results.json").write_text(json.dumps(doc, indent=2, default=str))
        return doc
