"""Command-line entry point for the experiments.

    python -m app.experiments.runner e1 --profile quick
    python -m app.experiments.runner all --profile default
(run from the ``backend`` directory, or use ``scripts/run_experiments.ps1``/``.sh``)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from ..config import get_settings
from . import e1_throughput, e2_sharding, e3_security, e4_consistency
from .common import ExperimentContext, load_profile

MODULES = {"e1": e1_throughput, "e2": e2_sharding, "e3": e3_security, "e4": e4_consistency}


def run_experiment(name: str, profile: str = "quick", overrides: dict | None = None,
                   progress=None) -> dict:
    settings = get_settings()
    cfg = load_profile(settings, profile, name, overrides)
    ctx = ExperimentContext(settings, progress or (lambda m: None))
    return MODULES[name].run(ctx, cfg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run HANF experiments E1-E4")
    ap.add_argument("experiment", choices=list(MODULES) + ["all"])
    ap.add_argument("--profile", default="quick", choices=["quick", "default", "large"])
    ap.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                    help="override a config value, e.g. --set repetitions=1")
    args = ap.parse_args(argv)
    overrides = {}
    for item in args.set:
        k, v = item.split("=", 1)
        try:
            overrides[k] = json.loads(v)
        except ValueError:
            overrides[k] = v
    names = list(MODULES) if args.experiment == "all" else [args.experiment]
    exit_code = 0
    for n in names:
        t0 = time.time()
        print(f"=== {n.upper()} ({args.profile}) ===", flush=True)
        doc = run_experiment(n, args.profile, overrides,
                             lambda m: print(f"  [{time.time() - t0:7.1f}s] {m}", flush=True))
        print(f"  status: {doc['status']}  run: {doc['run_id']}  ({doc['duration_s']} s)")
        print(f"  output: results/{n}/{doc['run_id']}/  graphs: {', '.join(doc['graphs'])}")
        if doc["failures"]:
            exit_code = 1
            for f in doc["failures"]:
                print(f"  FAILURE in {f['step']}: {f['error']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
