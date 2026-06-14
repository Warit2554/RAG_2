"""Tests for rag_local.cache — ResultCache TTL & LRU behaviour."""
from __future__ import annotations
import time
import pytest
from rag_local.cache import ResultCache, _NullCache


def test_cache_put_and_get():
    cache = ResultCache(ttl=60, max_size=10)
    key = cache.make_key("svc", "tool", {"a": 1})
    assert cache.get(key) is None
    cache.put(key, "hello")
    assert cache.get(key) == "hello"


def test_cache_ttl_expiry():
    cache = ResultCache(ttl=0.05, max_size=10)  # 50 ms TTL
    key = cache.make_key("svc", "tool", {"x": 2})
    cache.put(key, "value")
    assert cache.get(key) == "value"
    time.sleep(0.1)
    assert cache.get(key) is None


def test_cache_lru_eviction():
    cache = ResultCache(ttl=60, max_size=3)
    for i in range(4):
        cache.put(f"key{i}", f"val{i}")
    # First entry should have been evicted
    assert cache.size == 3
    assert cache.get("key0") is None


def test_cache_hit_rate():
    cache = ResultCache(ttl=60, max_size=10)
    cache.put("k1", "v1")
    cache.get("k1")   # hit
    cache.get("k2")   # miss
    assert cache.hit_rate == 0.5


def test_null_cache_is_noop():
    null = _NullCache(ttl=60, max_size=256)
    null.put("k", "v")
    assert null.get("k") is None


def test_cache_key_determinism():
    k1 = ResultCache.make_key("a", "b", {"x": 1, "y": 2})
    k2 = ResultCache.make_key("a", "b", {"y": 2, "x": 1})
    assert k1 == k2, "Key must be order-independent"


def test_cache_clear():
    cache = ResultCache(ttl=60, max_size=10)
    cache.put("k1", "v1")
    cache.put("k2", "v2")
    assert cache.size == 2
    cache.clear()
    assert cache.size == 0


def test_stats_structure():
    cache = ResultCache(ttl=60, max_size=10)
    s = cache.stats()
    assert "size" in s
    assert "hit_rate" in s
    assert "total_hits" in s
