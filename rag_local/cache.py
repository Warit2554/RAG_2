"""Result caching for Nexus.

Provides a TTL-based, size-bounded in-memory LRU cache for MCP tool call
results.  Identical arguments produce a cache hit within the configured TTL,
saving latency and avoiding redundant Ollama / external API calls.

Configuration
-------------
NEXUS_CACHE_ENABLED  (bool, default true)
NEXUS_CACHE_TTL      (seconds, default 300)
NEXUS_CACHE_MAX_SIZE (entries, default 256)
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    value: str
    expires_at: float                  # monotonic clock
    hit_count: int = 0


# ---------------------------------------------------------------------------
# Main cache class
# ---------------------------------------------------------------------------

class ResultCache:
    """LRU TTL cache for MCP tool results.

    Thread-safe for *async* single-threaded use (asyncio); does NOT use locks.
    """

    def __init__(self, ttl: float = 300.0, max_size: int = 256) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._total_hits = 0
        self._total_misses = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    @staticmethod
    def make_key(server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Deterministic cache key from call parameters."""
        payload = json.dumps(
            {"s": server_name, "t": tool_name, "a": arguments},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def get(self, key: str) -> str | None:
        """Return cached value or ``None`` on miss/expiry."""
        entry = self._store.get(key)
        if entry is None:
            self._total_misses += 1
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            self._total_misses += 1
            return None
        # LRU: move to end
        self._store.move_to_end(key)
        entry.hit_count += 1
        self._total_hits += 1
        logger.debug("[Cache] HIT  key=%s hits=%d", key[:8], entry.hit_count)
        return entry.value

    def put(self, key: str, value: str) -> None:
        """Store a value.  Evicts LRU entries when at capacity."""
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key].value = value
            self._store[key].expires_at = time.monotonic() + self._ttl
            return
        while len(self._store) >= self._max_size:
            evicted_key, _ = self._store.popitem(last=False)
            logger.debug("[Cache] EVICT key=%s", evicted_key[:8])
        self._store[key] = _CacheEntry(
            value=value,
            expires_at=time.monotonic() + self._ttl,
        )
        logger.debug("[Cache] PUT   key=%s ttl=%.0fs", key[:8], self._ttl)

    def invalidate(self, key: str) -> None:
        """Remove a specific entry."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Flush the entire cache."""
        self._store.clear()
        logger.info("[Cache] Cleared.")

    def evict_expired(self) -> int:
        """Purge all expired entries. Returns count removed."""
        now = time.monotonic()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]
        if expired:
            logger.debug("[Cache] Evicted %d expired entries.", len(expired))
        return len(expired)

    # ── Statistics ─────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_rate(self) -> float:
        total = self._total_hits + self._total_misses
        return self._total_hits / total if total else 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "max_size": self._max_size,
            "ttl_seconds": self._ttl,
            "total_hits": self._total_hits,
            "total_misses": self._total_misses,
            "hit_rate": round(self.hit_rate, 3),
        }

    def summary_lines(self) -> list[str]:
        s = self.stats()
        return [
            f"  Size:      {s['size']} / {s['max_size']} entries",
            f"  TTL:       {s['ttl_seconds']}s",
            f"  Hits:      {s['total_hits']}  Misses: {s['total_misses']}",
            f"  Hit rate:  {s['hit_rate']:.1%}",
        ]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_cache_instance: ResultCache | None = None


def get_cache() -> ResultCache:
    """Lazy singleton — reads SETTINGS on first call."""
    global _cache_instance
    if _cache_instance is None:
        from .config import SETTINGS
        if SETTINGS.cache_enabled:
            _cache_instance = ResultCache(
                ttl=float(SETTINGS.cache_ttl_seconds),
                max_size=SETTINGS.cache_max_size,
            )
            logger.info(
                "[Cache] Initialised — TTL=%ds  max=%d",
                SETTINGS.cache_ttl_seconds,
                SETTINGS.cache_max_size,
            )
        else:
            # Disabled: return a no-op cache
            _cache_instance = _NullCache()  # type: ignore[assignment]
    return _cache_instance


class _NullCache(ResultCache):
    """Drop-in replacement when caching is disabled."""

    def get(self, key: str) -> str | None:
        return None

    def put(self, key: str, value: str) -> None:
        pass
