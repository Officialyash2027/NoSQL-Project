"""Background jobs for long-running experiments so the API and UI never block.

Jobs run one at a time (a single worker thread) so concurrent benchmarks do not distort each
other's timings. Job state lives in memory; finished results are persisted by the experiment store.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


class JobManager:
    def __init__(self, max_jobs: int = 100):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hanf-job")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._max = max_jobs

    def submit(self, kind: str, params: dict, fn: Callable[[Callable[[str], None]], Any]) -> dict:
        job_id = uuid.uuid4().hex[:10]
        job = {"id": job_id, "kind": kind, "params": params, "status": "queued",
               "created_at": time.time(), "started_at": None, "finished_at": None,
               "progress": [], "result": None, "error": None}
        with self._lock:
            self._jobs[job_id] = job
            if len(self._jobs) > self._max:
                for old in sorted(self._jobs.values(), key=lambda j: j["created_at"])[: len(self._jobs) - self._max]:
                    if old["status"] in ("done", "failed"):
                        self._jobs.pop(old["id"], None)

        def progress(msg: str) -> None:
            job["progress"].append({"t": round(time.time() - (job["started_at"] or time.time()), 1), "msg": msg})
            del job["progress"][:-200]

        def run():
            job["status"], job["started_at"] = "running", time.time()
            try:
                job["result"] = fn(progress)
                job["status"] = "done"
            except Exception as exc:  # noqa: BLE001
                job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"
                job["traceback"] = traceback.format_exc()[-2000:]
            finally:
                job["finished_at"] = time.time()

        self._pool.submit(run)
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return {k: v for k, v in job.items() if k != "traceback"} | {"progress": list(job["progress"])}

    def list(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
            return [{k: v for k, v in j.items() if k not in ("result", "traceback", "progress")}
                    | {"last_progress": j["progress"][-1]["msg"] if j["progress"] else None} for j in jobs]

    def busy(self) -> bool:
        with self._lock:
            return any(j["status"] in ("queued", "running") for j in self._jobs.values())

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
