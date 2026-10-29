import numpy as np
import pytest

from app.sharding.hashring import (ConsistentHashRing, ModuloHasher, adaptive_vnode_update,
                                   coefficient_of_variation, hash64, hash_many, imbalance)

KEYS = [f"key:{i}" for i in range(20000)]


def test_hash_is_deterministic_and_64bit():
    assert hash64("abc") == hash64("abc")
    assert 0 <= hash64("abc") < 2**64
    assert hash64("abc") != hash64("abd")


def test_mapping_is_deterministic_across_instances():
    a = ConsistentHashRing(["n1", "n2", "n3"], 64)
    b = ConsistentHashRing(["n1", "n2", "n3"], 64)
    assert [a.get_node(k) for k in KEYS[:500]] == [b.get_node(k) for k in KEYS[:500]]


def test_vectorised_mapping_matches_scalar_lookup():
    ring = ConsistentHashRing(["a", "b", "c", "d"], 32)
    assert ring.map_keys(KEYS[:2000]) == [ring.get_node(k) for k in KEYS[:2000]]


def test_more_virtual_nodes_improve_balance():
    def imb(v):
        ring = ConsistentHashRing([f"n{i}" for i in range(4)], v)
        return imbalance(np.bincount(ring.map_hashes(hash_many(KEYS)), minlength=4))
    assert imb(256) < imb(4)
    assert imb(256) < 1.15


def test_ownership_sums_to_one():
    ring = ConsistentHashRing(["a", "b", "c"], 50)
    assert sum(ring.ownership().values()) == pytest.approx(1.0)


def test_adding_a_node_moves_about_one_over_n_plus_one():
    nodes = [f"n{i}" for i in range(4)]
    before = ConsistentHashRing(nodes, 128)
    after = ConsistentHashRing(nodes + ["n4"], 128)
    h = hash_many(KEYS)
    moved = (before.map_hashes(h) != after.map_hashes(h)).mean()
    assert 0.12 < moved < 0.28          # ideal 0.20
    # every moved key moves *to the new node* (consistent hashing property)
    a_idx = after.map_hashes(h)
    changed = before.map_hashes(h) != a_idx
    assert set(np.unique(a_idx[changed])) == {4}


def test_modulo_hashing_moves_most_keys():
    h = hash_many(KEYS)
    m4, m5 = ModuloHasher(["a", "b", "c", "d"]), ModuloHasher(["a", "b", "c", "d", "e"])
    assert (m4.map_hashes(h) != m5.map_hashes(h)).mean() > 0.7


def test_add_remove_and_errors():
    ring = ConsistentHashRing(["a"], 8)
    ring.add_node("b")
    assert set(ring.nodes) == {"a", "b"}
    with pytest.raises(ValueError):
        ring.add_node("a")
    ring.remove_node("a")
    assert ring.nodes == ["b"]
    with pytest.raises(KeyError):
        ring.remove_node("zzz")
    with pytest.raises(ValueError):
        ConsistentHashRing(["x"], 0)
    with pytest.raises(RuntimeError):
        ConsistentHashRing([], 8).get_node("k")


def test_adaptive_rule_shifts_weight_from_hot_to_cold_and_respects_floor():
    new = adaptive_vnode_update({"hot": 100, "cold": 100}, {"hot": 300.0, "cold": 100.0})
    assert new["hot"] < 100 < new["cold"]
    floor = adaptive_vnode_update({"hot": 20, "cold": 20}, {"hot": 1000.0, "cold": 1.0}, floor=16)
    assert floor["hot"] == 16


def test_balance_metrics():
    assert imbalance([10, 10, 10]) == pytest.approx(1.0)
    assert imbalance([20, 10, 0]) == pytest.approx(2.0)
    assert coefficient_of_variation([5, 5]) == 0.0
