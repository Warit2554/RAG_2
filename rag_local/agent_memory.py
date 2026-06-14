"""Nexus Embedding Memory — Long-term vector memory via Qdrant.

Stores important facts, task outcomes, and successful tool invocations in a
dedicated Qdrant collection (``nexus_agent_memory``).  Before each planning
cycle, the most relevant memories are recalled and injected into the planner's
system prompt as few-shot context.

Memory Ranking
--------------
Retrieved memories are ranked by a combination of:
1. **Cosine similarity** — embedding distance to the current query.
2. **Recency** — newer memories get a small score boost.
3. **Success weight** — successful outcomes are preferred over failed ones.

All three signals are blended into a final score before top-K selection.

Configuration
-------------
NEXUS_AGENT_MEMORY_ENABLED    (bool, default true)
NEXUS_AGENT_MEMORY_TOP_K      (int,  default 5)
NEXUS_AGENT_MEMORY_COLLECTION (str,  default "nexus_agent_memory")
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Qdrant payload fields
_F_TEXT = "text"
_F_KIND = "kind"           # "fact" | "tool_success" | "tool_failure" | "lesson"
_F_TS = "ts"               # Unix timestamp (float)
_F_SUCCESS = "success"     # bool
_F_QUERY = "query"         # original user query
_F_META = "meta"           # arbitrary dict


# ---------------------------------------------------------------------------
# AgentMemory
# ---------------------------------------------------------------------------

class AgentMemory:
    """Embedding-backed long-term memory for Nexus."""

    def __init__(self) -> None:
        from .config import SETTINGS
        self._collection = SETTINGS.agent_memory_collection
        self._top_k = SETTINGS.agent_memory_top_k
        self._enabled = SETTINGS.agent_memory_enabled
        self._ready = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def ensure_collection(self) -> None:
        """Create the Qdrant collection if it doesn't exist."""
        if not self._enabled or self._ready:
            return
        try:
            from qdrant_client import AsyncQdrantClient
            from qdrant_client.models import Distance, VectorParams
            from .config import SETTINGS

            client = AsyncQdrantClient(url=SETTINGS.qdrant_url)
            collections = await client.get_collections()
            existing = {c.name for c in collections.collections}
            if self._collection not in existing:
                # Probe vector size from the embed model
                from .embed import OllamaClient
                probe = await OllamaClient().embed(SETTINGS.ollama_embed_model, ["probe"])
                vec_size = len(probe[0]) if probe else 768
                await client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(size=vec_size, distance=Distance.COSINE),
                )
                logger.info("[AgentMemory] Collection '%s' created (dim=%d).", self._collection, vec_size)
            await client.close()
            self._ready = True
        except Exception as exc:
            logger.warning("[AgentMemory] Could not initialise collection: %s", exc)

    # ── Writing ───────────────────────────────────────────────────────────────

    async def store(
        self,
        text: str,
        kind: str = "fact",
        query: str = "",
        success: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Embed and store a memory."""
        if not self._enabled:
            return
        await self.ensure_collection()
        try:
            from qdrant_client import AsyncQdrantClient
            from qdrant_client.models import PointStruct
            from .embed import OllamaClient
            from .config import SETTINGS

            embed_client = OllamaClient()
            vectors = await embed_client.embed(SETTINGS.ollama_embed_model, [text])
            if not vectors:
                return

            point_id = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
            payload = {
                _F_TEXT: text[:500],
                _F_KIND: kind,
                _F_TS: time.time(),
                _F_SUCCESS: success,
                _F_QUERY: query[:200],
                _F_META: meta or {},
            }
            client = AsyncQdrantClient(url=SETTINGS.qdrant_url)
            await client.upsert(
                collection_name=self._collection,
                points=[PointStruct(id=point_id, vector=vectors[0], payload=payload)],
            )
            await client.close()
            logger.debug("[AgentMemory] Stored memory kind=%s id=%d", kind, point_id)
        except Exception as exc:
            logger.warning("[AgentMemory] Store failed: %s", exc)

    async def store_task_outcome(
        self,
        query: str,
        task_name: str,
        tool_call: str,
        success: bool,
        summary: str,
    ) -> None:
        """Convenience: store a task execution outcome."""
        text = (
            f"Task: {task_name} | Tool: {tool_call} | "
            f"{'SUCCESS' if success else 'FAILURE'}: {summary[:200]}"
        )
        await self.store(
            text=text,
            kind="tool_success" if success else "tool_failure",
            query=query,
            success=success,
            meta={"task": task_name, "tool": tool_call},
        )

    # ── Reading ────────────────────────────────────────────────────────────────

    async def recall(self, query: str) -> str:
        """Return a formatted string of top-K relevant memories.

        Memories are ranked by a blend of similarity, recency, and success.
        Returns ``""`` if memory is disabled or Qdrant is unreachable.
        """
        if not self._enabled:
            return ""
        await self.ensure_collection()
        try:
            from qdrant_client import AsyncQdrantClient
            from .embed import OllamaClient
            from .config import SETTINGS

            embed_client = OllamaClient()
            vectors = await embed_client.embed(SETTINGS.ollama_embed_model, [query])
            if not vectors:
                return ""

            client = AsyncQdrantClient(url=SETTINGS.qdrant_url)
            # Fetch top_k * 3 candidates then re-rank
            hits = await client.query_points(
                collection_name=self._collection,
                query=vectors[0],
                limit=self._top_k * 3,
                with_payload=True,
            )
            await client.close()

            if not hits.points:
                return ""

            now = time.time()
            ONE_DAY = 86400.0

            scored = []
            for point in hits.points:
                sim = getattr(point, "score", 0.5)
                payload = point.payload or {}
                age_days = (now - float(payload.get(_F_TS, now))) / ONE_DAY
                recency = math.exp(-0.1 * age_days)   # decay ~10%/day
                success_weight = 1.0 if payload.get(_F_SUCCESS, True) else 0.7
                final = 0.6 * sim + 0.25 * recency + 0.15 * success_weight
                scored.append((final, payload))

            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[: self._top_k]

            if not top:
                return ""

            lines = ["Relevant past memories:"]
            for score, payload in top:
                kind = payload.get(_F_KIND, "?")
                text = payload.get(_F_TEXT, "")
                ok = "✓" if payload.get(_F_SUCCESS, True) else "✗"
                lines.append(f"  [{ok} {kind}] (score={score:.2f}) {text}")
            return "\n".join(lines)

        except Exception as exc:
            logger.debug("[AgentMemory] Recall failed: %s", exc)
            return ""


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_memory_instance: AgentMemory | None = None


def get_agent_memory() -> AgentMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = AgentMemory()
    return _memory_instance
