"""Live-migration logic of the ShardManager, on isolated fakeredis shards."""
import pytest
import redis

from app.sharding.manager import ShardManager


def load(mgr: ShardManager, n=3000, prefix="k"):
    data = {f"{prefix}:{i}": f"value-{i}".encode() for i in range(n)}
    for k, v in data.items():
        mgr.shard_for(k).client.set(k, v)
    return data


def all_on_owner(mgr: ShardManager) -> bool:
    for sid in mgr.active:
        for raw in mgr.shards[sid].client.scan_iter():
            if mgr.ring.get_node(raw.decode()) != sid:
                return False
    return True


def test_add_shard_moves_only_changed_keys_and_preserves_values(manager):
    data = load(manager)
    report = manager.add_shard()
    assert report.status == "completed"
    assert report.failed == 0
    assert report.total_before == report.total_after == len(data)
    assert report.moved == report.verified == report.keys_after["shard-5"] > 0
    assert 10 < report.remapped_pct < 35  # ideal 20 %
    for k, v in data.items():
        assert manager.shard_for(k).client.get(k) == v
    assert all_on_owner(manager)
    assert manager.previous_ring is None


def test_migration_rerun_is_idempotent(manager):
    data = load(manager, 1000)
    manager.add_shard()
    total = sum(manager.distribution().values())
    # force a second pass over the same rings: nothing should move or be lost
    manager.previous_ring = manager.ring.copy()
    r = manager.retry_migration()
    assert r.to_move == 0 and r.failed == 0
    assert sum(manager.distribution().values()) == total == len(data)


def test_remove_and_reset_return_keys(manager):
    data = load(manager, 1500)
    manager.add_shard("shard-5")
    report = manager.reset_to_initial()
    assert report.status == "completed"
    assert manager.active == ["shard-1", "shard-2", "shard-3", "shard-4"]
    assert manager.count_keys("shard-5") == 0
    for k, v in data.items():
        assert manager.shard_for(k).client.get(k) == v


def test_cannot_add_active_or_unknown_or_remove_last(manager):
    with pytest.raises(ValueError):
        manager.add_shard("shard-1")
    with pytest.raises(KeyError):
        manager.add_shard("shard-99")
    single = ShardManager(list(manager.shards.values()), 1, 16)
    with pytest.raises(ValueError):
        single.remove_shard("shard-1")


class _FailingPipeline:
    def __init__(self):
        self.calls = 0

    def __getattr__(self, name):
        def record(*a, **k):
            self.calls += 1
        return record

    def execute(self):
        raise redis.ConnectionError("simulated network failure")


class _FlakyClient:
    """Delegates to a real client but every pipeline fails while ``broken`` is True."""

    def __init__(self, real):
        self.real, self.broken = real, True

    def pipeline(self, *a, **k):
        return _FailingPipeline() if self.broken else self.real.pipeline(*a, **k)

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_failed_destination_keeps_source_and_retry_completes(manager):
    data = load(manager, 2000)
    flaky = _FlakyClient(manager.shards["shard-5"].client)
    manager.shards["shard-5"].client = flaky
    report = manager.add_shard("shard-5")
    assert report.status == "partial"
    assert report.failed > 0 and report.moved == 0
    assert report.total_after == report.total_before == len(data)  # nothing lost
    assert manager.migrating  # previous ring kept for read fallback
    moved_key = next(k for k in data if manager.ring.get_node(k) == "shard-5")
    fb = manager.fallback_shard_for(moved_key)
    assert fb is not None and fb.client.get(moved_key) == data[moved_key]

    flaky.broken = False
    retry = manager.retry_migration()
    assert retry.status == "completed" and retry.failed == 0
    assert not manager.migrating
    assert all_on_owner(manager)
    assert sum(manager.distribution().values()) == len(data)


def test_newer_write_at_destination_is_not_overwritten(manager):
    data = load(manager, 800)
    flaky = _FlakyClient(manager.shards["shard-5"].client)
    manager.shards["shard-5"].client = flaky
    manager.add_shard("shard-5")             # fails -> partial; ring already switched
    key = next(k for k in data if manager.ring.get_node(k) == "shard-5")
    flaky.real.set(key, b"newer-value")      # a client write after the switch
    flaky.broken = False
    manager.retry_migration()
    assert manager.shard_for(key).client.get(key) == b"newer-value"
    assert manager.last_report.kept_newer_destination >= 1


def test_rebalance_by_load_requires_samples_and_shifts_vnodes(manager, telemetry):
    load(manager, 1000)
    with pytest.raises(ValueError):
        manager.rebalance_by_load(min_requests=10)
    load_map = {"shard-1": 900, "shard-2": 100, "shard-3": 100, "shard-4": 100}
    out = manager.rebalance_by_load(load=load_map)
    assert out["vnodes_after"]["shard-1"] < out["vnodes_before"]["shard-1"]
    assert out["migration"]["failed"] == 0
    assert out["migration"]["count_reconciled"]


def test_topology_persists(fake_shards, tmp_path):
    path = tmp_path / "topo.json"
    m = ShardManager(fake_shards, 4, 32, topology_path=path)
    m.add_shard()
    again = ShardManager(fake_shards, 4, 32, topology_path=path)
    assert again.active == m.active and again.epoch == m.epoch
