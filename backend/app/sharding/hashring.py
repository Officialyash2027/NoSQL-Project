"""Consistent hashing with virtual nodes (Karger et al., 1997) and a modulo-hashing baseline.

Every key and every virtual node token is hashed with MD5 (first 8 bytes, big endian) onto a
64-bit ring. A key belongs to the first virtual node clockwise from its hash. The mapping is fully
deterministic, so the same key always lands on the same shard for a given ring configuration.
"""
from __future__ import annotations

import bisect
import hashlib
from typing import Iterable, Sequence

import numpy as np

RING_SIZE = 2**64


def hash64(value: str) -> int:
    return int.from_bytes(hashlib.md5(value.encode("utf-8")).digest()[:8], "big")


def hash_many(keys: Sequence[str]) -> np.ndarray:
    return np.fromiter((hash64(k) for k in keys), dtype=np.uint64, count=len(keys))


class ConsistentHashRing:
    """A hash ring whose nodes may own different numbers of virtual nodes (weights)."""

    def __init__(self, nodes: Iterable[str] | dict[str, int] = (), vnodes: int = 128):
        if vnodes < 1:
            raise ValueError("vnodes must be >= 1")
        self.default_vnodes = vnodes
        self._vnodes: dict[str, int] = {}
        if isinstance(nodes, dict):
            self._vnodes.update({n: int(v) for n, v in nodes.items()})
        else:
            for n in nodes:
                self._vnodes[n] = vnodes
        self._rebuild()

    # ---------------------------------------------------------------- configuration
    @property
    def nodes(self) -> list[str]:
        return list(self._vnodes)

    @property
    def vnodes(self) -> dict[str, int]:
        return dict(self._vnodes)

    def add_node(self, node: str, vnodes: int | None = None) -> None:
        if node in self._vnodes:
            raise ValueError(f"node {node!r} already on the ring")
        self._vnodes[node] = vnodes or self.default_vnodes
        self._rebuild()

    def remove_node(self, node: str) -> None:
        if node not in self._vnodes:
            raise KeyError(node)
        del self._vnodes[node]
        self._rebuild()

    def set_vnodes(self, weights: dict[str, int]) -> None:
        for n, v in weights.items():
            if n not in self._vnodes:
                raise KeyError(n)
            if v < 1:
                raise ValueError("vnode count must be >= 1")
            self._vnodes[n] = int(v)
        self._rebuild()

    def copy(self) -> "ConsistentHashRing":
        return ConsistentHashRing(dict(self._vnodes), self.default_vnodes)

    def _rebuild(self) -> None:
        tokens: list[tuple[int, str]] = []
        for node, count in self._vnodes.items():
            for i in range(count):
                tokens.append((hash64(f"{node}#vn{i}"), node))
        tokens.sort()
        self._hashes = [t[0] for t in tokens]
        self._owners = [t[1] for t in tokens]
        self._hash_arr = np.array(self._hashes, dtype=np.uint64)
        self._node_index = {n: i for i, n in enumerate(self._vnodes)}
        self._owner_idx = np.array([self._node_index[o] for o in self._owners], dtype=np.int64)

    # ---------------------------------------------------------------- lookups
    def get_node(self, key: str) -> str:
        if not self._hashes:
            raise RuntimeError("hash ring has no nodes")
        i = bisect.bisect_left(self._hashes, hash64(key))
        return self._owners[i % len(self._owners)]

    def locate(self, key: str) -> dict:
        """Lookup with the details shown in the UI (key hash, owning token, ring position)."""
        h = hash64(key)
        i = bisect.bisect_left(self._hashes, h) % len(self._owners)
        return {
            "key": key,
            "key_hash": h,
            "ring_position": h / RING_SIZE,
            "token_hash": self._hashes[i],
            "token_position": self._hashes[i] / RING_SIZE,
            "node": self._owners[i],
        }

    def map_hashes(self, hashes: np.ndarray) -> np.ndarray:
        """Vectorised owner lookup: returns node indices (order of :attr:`nodes`)."""
        idx = np.searchsorted(self._hash_arr, hashes, side="left") % len(self._hash_arr)
        return self._owner_idx[idx]

    def map_keys(self, keys: Sequence[str]) -> list[str]:
        names = self.nodes
        return [names[i] for i in self.map_hashes(hash_many(keys))]

    def ownership(self) -> dict[str, float]:
        """Fraction of the 64-bit ring owned by each node."""
        own = {n: 0 for n in self._vnodes}
        prev = self._hashes[-1] - RING_SIZE
        for h, o in zip(self._hashes, self._owners):
            own[o] += h - prev
            prev = h
        return {n: v / RING_SIZE for n, v in own.items()}

    def tokens_sample(self, max_tokens: int = 512) -> list[dict]:
        step = max(1, len(self._hashes) // max_tokens)
        return [{"position": self._hashes[i] / RING_SIZE, "node": self._owners[i]}
                for i in range(0, len(self._hashes), step)]


class ModuloHasher:
    """Static ``hash(key) mod N`` placement - the baseline from Review II."""

    def __init__(self, nodes: Iterable[str]):
        self.nodes = list(nodes)

    def get_node(self, key: str) -> str:
        return self.nodes[hash64(key) % len(self.nodes)]

    def map_hashes(self, hashes: np.ndarray) -> np.ndarray:
        return (hashes % np.uint64(len(self.nodes))).astype(np.int64)


# -------------------------------------------------------------------- balance metrics
def imbalance(loads: Sequence[float]) -> float:
    """Maximum load divided by mean load (1.0 = perfectly balanced)."""
    arr = np.asarray(loads, dtype=float)
    return float(arr.max() / arr.mean()) if arr.size and arr.mean() > 0 else 0.0


def coefficient_of_variation(loads: Sequence[float]) -> float:
    arr = np.asarray(loads, dtype=float)
    return float(arr.std() / arr.mean()) if arr.size and arr.mean() > 0 else 0.0


def adaptive_vnode_update(vnodes: dict[str, int], load: dict[str, float],
                          alpha: float = 0.5, floor: int = 16) -> dict[str, int]:
    """Load-skew rebalancing rule from Review II.

    ``v_i <- max(floor, round(v_i * (1 - alpha * (load_i / mean_load - 1))))``

    Overloaded nodes give up ring space; under-loaded nodes take more.
    """
    mean = float(np.mean([load.get(n, 0.0) for n in vnodes])) or 1.0
    out = {}
    for n, v in vnodes.items():
        factor = 1.0 - alpha * (load.get(n, 0.0) / mean - 1.0)
        out[n] = max(floor, int(round(v * factor)))
    return out
