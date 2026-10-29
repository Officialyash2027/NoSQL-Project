"""E2 - adaptive sharding: remapping cost, load-skew rebalancing and live Redis migration.

Part 1 (algorithmic): keys remapped when one node is added, modulo vs consistent hashing with
16/64/128/256 virtual nodes; initial imbalance (max/mean key count). Repeated over several ring
seeds (different node names -> different token positions) to show variability.
Part 2 (algorithmic): adaptive vnode rebalancing under Zipf-skewed request load, several rounds,
also counting how many keys each adjustment moves (the cost of rebalancing).
Part 3 (live Redis): load keys into 4 real shards, add a 5th through the ShardManager, migrate,
verify a sample of values byte-for-byte, reconcile the total and check that every key now sits on
its ring owner.
"""
from __future__ import annotations

import time

import numpy as np

from ..sharding.hashring import (ConsistentHashRing, ModuloHasher, adaptive_vnode_update,
                                 coefficient_of_variation, hash_many, imbalance)
from ..sharding.manager import ShardManager
from . import plotstyle
from .common import ExperimentContext, flush_experiment_db


def remap_test(cfg: dict) -> list[dict]:
    keys = [f"key:{i}" for i in range(cfg["remap_keys"])]
    hashes = hash_many(keys)
    rows = []
    for before, after in cfg["node_counts"]:
        for seed in range(cfg["ring_seeds"]):
            nodes_b = [f"node-{seed}-{i}" for i in range(before)]
            nodes_a = nodes_b + [f"node-{seed}-{before}"]
            schemes = [("modulo", None)] + [("consistent", v) for v in cfg["vnode_options"]]
            for scheme, v in schemes:
                if scheme == "modulo":
                    hb, ha = ModuloHasher(nodes_b), ModuloHasher(nodes_a)
                else:
                    hb, ha = ConsistentHashRing(nodes_b, v), ConsistentHashRing(nodes_a, v)
                ob, oa = hb.map_hashes(hashes), ha.map_hashes(hashes)
                # node index order is identical for the shared prefix of node names
                moved = float((ob != oa).mean() * 100)
                counts = np.bincount(ob, minlength=before)
                rows.append({"scheme": scheme, "vnodes": v, "nodes_before": before, "nodes_after": after,
                             "ring_seed": seed, "remapped_pct": round(moved, 3),
                             "ideal_pct": round(100 / after, 3),
                             "imbalance_before": round(imbalance(counts), 4)})
    return rows


def summarize_remap(rows: list[dict]) -> list[dict]:
    out = []
    keys = sorted({(r["scheme"], r["vnodes"] or 0, r["nodes_before"], r["nodes_after"]) for r in rows},
                  key=lambda k: (k[2], k[0] != "modulo", k[1]))
    for scheme, v, b, a in keys:
        rs = [r for r in rows if r["scheme"] == scheme and (r["vnodes"] or 0) == v and r["nodes_before"] == b]
        m = np.array([r["remapped_pct"] for r in rs])
        imb = np.array([r["imbalance_before"] for r in rs])
        out.append({"scheme": scheme if scheme == "modulo" else f"consistent v={v}", "nodes": f"{b}->{a}",
                    "remapped_pct_mean": round(float(m.mean()), 2), "remapped_pct_sd": round(float(m.std(ddof=1)) if len(m) > 1 else 0.0, 2),
                    "ideal_pct": round(100 / a, 2),
                    "imbalance_mean": round(float(imb.mean()), 3), "seeds": len(rs)})
    return out


def rebalance_test(cfg: dict, exponent: float) -> list[dict]:
    rng = np.random.default_rng(cfg["seed"])
    n_keys = cfg["rebalance_keys"]
    keys = [f"item:{i}" for i in range(n_keys)]
    hashes = hash_many(keys)
    ranks = np.arange(1, n_keys + 1, dtype=float)
    p = 1.0 / ranks**exponent
    p /= p.sum()
    perm = rng.permutation(n_keys)
    req = perm[rng.choice(n_keys, size=cfg["rebalance_requests"], p=p)]
    per_key = np.bincount(req, minlength=n_keys)
    nodes = [f"node-{i}" for i in range(cfg["rebalance_nodes"])]
    vn = {n: cfg["rebalance_vnodes"] for n in nodes}
    ring = ConsistentHashRing(vn)
    owners = ring.map_hashes(hashes)
    rows = []
    hottest_share = float(per_key.max() / per_key.sum())
    for rnd in range(cfg["rebalance_rounds"]):
        load = np.bincount(owners, weights=per_key, minlength=len(nodes))
        row = {"zipf_exponent": exponent, "round": rnd, "max_over_mean": round(imbalance(load), 4),
               "cv": round(coefficient_of_variation(load), 4),
               "hottest_key_share_pct": round(100 * hottest_share, 3),
               "vnodes": {n: vn[n] for n in nodes}}
        if rnd < cfg["rebalance_rounds"] - 1:
            vn = adaptive_vnode_update(vn, {n: float(load[i]) for i, n in enumerate(nodes)})
            ring = ConsistentHashRing(vn)
            new_owners = ring.map_hashes(hashes)
            row["keys_moved_by_next_adjustment_pct"] = round(float((new_owners != owners).mean() * 100), 3)
            owners = new_owners
        rows.append(row)
    return rows


def live_migration(ctx: ExperimentContext, cfg: dict) -> dict:
    shards = ctx.experiment_shards()
    for s in shards:
        s.client.ping()
    flush_experiment_db(shards)
    try:
        mgr = ShardManager(shards, 4, ctx.settings.vnodes, telemetry=None, topology_path=None)
        rng = np.random.default_rng(cfg["seed"])
        n = cfg["live_keys"]
        keys = [f"e2:{i}" for i in range(n)]
        values = [rng.bytes(cfg["live_value_bytes"]) for _ in range(n)]
        t0 = time.perf_counter()
        pipes = {}
        for k, v in zip(keys, values):
            sh = mgr.shard_for(k)
            p = pipes.setdefault(sh.id, sh.client.pipeline(transaction=False))
            p.set(k, v)
            if len(p) >= 2000:
                p.execute()
        for p in pipes.values():
            p.execute()
        load_s = time.perf_counter() - t0
        sample = {k: v for i, (k, v) in enumerate(zip(keys, values)) if i % cfg["verify_every"] == 0}
        ctx.progress(f"E2: loaded {n} keys in {load_s:.2f}s; adding shard-5 and migrating")
        report = mgr.add_shard("shard-5")

        def get(k):
            return mgr.shard_for(k).client.get(k)

        t1 = time.perf_counter()
        check = mgr.verify_sample(sample, get)
        verify_s = time.perf_counter() - t1
        misplaced = 0
        for sid in mgr.active:
            client = mgr.shards[sid].client
            batch = [raw.decode() for raw in client.scan_iter(count=2000)]
            misplaced += sum(o != sid for o in mgr.ring.map_keys(batch))
        return {"keys_loaded": n, "load_seconds": round(load_s, 3), "migration": report.to_dict(),
                "sample_verified": check, "verify_seconds": round(verify_s, 3),
                "keys_not_on_ring_owner": misplaced,
                "total_reconciled": report.total_before == report.total_after == n}
    finally:
        flush_experiment_db(shards)


def run(ctx: ExperimentContext, cfg: dict) -> dict:
    writer = ctx.store.new_run("e2", cfg, ctx.env(ctx.experiment_shards()))
    results: dict = {}
    ctx.progress("E2: remapping test")
    remap_rows = remap_test(cfg)
    writer.write_csv("e2_remap_runs.csv", remap_rows)
    results["remap"] = summarize_remap(remap_rows)
    writer.write_csv("e2_remap_summary.csv", results["remap"])

    ctx.progress("E2: adaptive rebalancing test")
    reb = []
    for e in cfg["zipf_exponents"]:
        reb.extend(rebalance_test(cfg, e))
    results["rebalance"] = reb
    writer.write_csv("e2_rebalance.csv", reb)

    try:
        results["live_migration"] = live_migration(ctx, cfg)
        lm = results["live_migration"]
        writer.write_csv("e2_live_migration.csv", [
            {"shard": s, "keys_before": lm["migration"]["keys_before"].get(s, 0),
             "keys_after": lm["migration"]["keys_after"].get(s, 0)} for s in lm["migration"]["keys_before"]])
    except Exception as exc:  # noqa: BLE001
        writer.record_failure("live_migration", f"{type(exc).__name__}: {exc}")
        results["live_migration"] = {"error": str(exc)}
    plot(writer, results, cfg)
    return writer.finish(results)


def plot(writer, results: dict, cfg: dict) -> None:
    rs = results["remap"]
    labels = [r["scheme"].replace("consistent ", "CH ") for r in rs if r["nodes"] == rs[0]["nodes"]]
    pairs = sorted({r["nodes"] for r in rs}, key=lambda x: int(x.split("->")[0]))
    series = {p: [r["remapped_pct_mean"] for r in rs if r["nodes"] == p] for p in pairs}
    errs = {p: [r["remapped_pct_sd"] for r in rs if r["nodes"] == p] for p in pairs}
    fig, axes = plotstyle.subplots(1, 2, figsize=(8.6, 3.2))
    ax = axes[0]
    plotstyle.grouped_bars(ax, labels, {f"{p} nodes": v for p, v in series.items()},
                           {f"{p} nodes": v for p, v in errs.items()})
    for i, p in enumerate(pairs):
        ideal = 100 / int(p.split("->")[1])
        ax.axhline(ideal, color=plotstyle.SERIES[i], lw=1, ls=(0, (1, 2)))
    ax.set_ylabel("Keys remapped (%)")
    ax.set_title("(a) Data movement when one node is added")
    ax.legend(loc="upper right")
    ax.tick_params(axis="x", labelsize=7)

    ax = axes[1]
    for i, e in enumerate(cfg["zipf_exponents"]):
        pts = [r for r in results["rebalance"] if r["zipf_exponent"] == e]
        ax.plot([r["round"] for r in pts], [r["max_over_mean"] for r in pts], marker="o",
                color=plotstyle.SERIES[i], label=f"Zipf {e}")
    ax.axhline(1.0, color=plotstyle.MUTED, lw=1)
    ax.set_xlabel("Rebalancing round")
    ax.set_ylabel("Hottest node load / mean load")
    ax.set_title("(b) Adaptive rebalancing, 8 nodes")
    ax.legend()
    fig.tight_layout()
    writer.add_graph(fig, "e2_sharding.png")

    lm = results.get("live_migration", {})
    if "migration" in lm:
        m = lm["migration"]
        shards = list(m["keys_before"])
        fig, ax = plotstyle.subplots(figsize=(5.6, 2.9))
        plotstyle.grouped_bars(ax, [s.replace("shard-", "Shard ") for s in shards],
                               {"Before adding shard 5": [m["keys_before"][s] for s in shards],
                                "After migration": [m["keys_after"][s] for s in shards]},
                               label_values=True, fmt="{:,.0f}")
        ax.set_ylabel("Keys")
        ax.set_title("E2 - Live Redis migration, 4 -> 5 shards")
        ax.legend(loc="upper left", bbox_to_anchor=(0, -0.12), ncol=2)
        writer.add_graph(fig, "e2_live_migration.png")
