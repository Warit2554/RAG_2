from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import SETTINGS
from .embed import OllamaClient
from .store import QdrantStore
from .types import SearchHit


@dataclass(slots=True)
class RetrievalResult:
    query: str
    hits: list[SearchHit]


async def hybrid_retrieve(query: str, *, top_k: int | None = None) -> RetrievalResult:
    client = OllamaClient()
    store = QdrantStore()
    embedding = (await client.embed(SETTINGS.ollama_embed_model, [query]))[0]
    hits = store.hybrid_search(query, embedding, top_k=top_k or SETTINGS.rag_top_k)
    return RetrievalResult(query=query, hits=hits)

