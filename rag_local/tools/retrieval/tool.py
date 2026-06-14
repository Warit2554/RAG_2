from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    PointStruct,
    VectorParams,
)
from rank_bm25 import BM25Okapi

from rag_local.config import SETTINGS
from rag_local.embed import OllamaClient
from rag_local.types import ChunkRecord, SearchHit
from rag_local.utils import normalize_scores, read_text


@dataclass(slots=True)
class PersistedIndex:
    path: Path
    tokens: list[list[str]]
    chunks: list[dict]

    @classmethod
    def load(cls, path: Path) -> "PersistedIndex":
        if path.exists():
            data = json.loads(read_text(path))
            return cls(path=path, tokens=data["tokens"], chunks=data["chunks"])
        return cls(path=path, tokens=[], chunks=[])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"tokens": self.tokens, "chunks": self.chunks}, indent=2), encoding="utf-8")


class QdrantStore:
    def __init__(self, client: QdrantClient | None = None) -> None:
        if client:
            self.client = client
        else:
            use_local = False
            if "localhost" in SETTINGS.qdrant_url or "127.0.0.1" in SETTINGS.qdrant_url:
                try:
                    with httpx.Client(timeout=1.0) as check_client:
                        resp = check_client.get(SETTINGS.qdrant_url.rstrip("/") + "/readyz")
                        if resp.status_code != 200:
                            use_local = True
                except Exception:
                    use_local = True
            if use_local:
                local_path = SETTINGS.rag_data_dir / "qdrant_local"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                self.client = QdrantClient(path=str(local_path))
            else:
                self.client = QdrantClient(url=SETTINGS.qdrant_url)
        self.collection = SETTINGS.qdrant_collection
        self.index_file = SETTINGS.index_dir / f"{self.collection}_bm25.json"
        self.persisted = PersistedIndex.load(self.index_file)

    def ensure_collection(self, vector_size: int) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection in existing:
            try:
                info = self.client.get_collection(collection_name=self.collection)
                vectors_config = info.config.params.vectors
                if hasattr(vectors_config, "size"):
                    current_size = vectors_config.size
                elif isinstance(vectors_config, dict) and "size" in vectors_config:
                    current_size = vectors_config["size"]
                else:
                    current_size = None
                
                if current_size is not None and current_size != vector_size:
                    # Dimensions mismatch! Delete and recreate the collection and BM25 index
                    self.client.delete_collection(collection_name=self.collection)
                    existing.remove(self.collection)
                    self.persisted.tokens = []
                    self.persisted.chunks = []
                    self.persisted.save()
            except Exception:
                pass

        if self.collection not in existing:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    def upsert_chunks(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        self.ensure_collection(len(embeddings[0]))
        points = []
        import uuid
        for chunk, vector in zip(chunks, embeddings):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk.chunk_id))
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=chunk.model_dump(),
                )
            )
        self.client.upsert(collection_name=self.collection, points=points)
        for chunk in chunks:
            self.persisted.tokens.append(_tokenize(chunk.source_path + " " + chunk.content + " " + chunk.summary))
            self.persisted.chunks.append(chunk.model_dump())
        self.persisted.save()

    def _bm25(self) -> BM25Okapi:
        if not self.persisted.tokens:
            return BM25Okapi([["empty"]])
        return BM25Okapi(self.persisted.tokens)

    def hybrid_search(self, query: str, query_embedding: list[float], top_k: int = 5) -> list[SearchHit]:
        dense_hits = []
        try:
            dense = self.client.query_points(
                collection_name=self.collection,
                query=query_embedding,
                limit=max(top_k * 2, top_k),
                with_payload=True,
            )
            for point in dense.points:
                payload = point.payload or {}
                dense_hits.append((str(payload.get("chunk_id", point.id)), float(point.score or 0.0)))
        except Exception:
            dense_hits = []

        bm25 = self._bm25()
        bm25_scores = bm25.get_scores(_tokenize(query))
        lexical_hits = []
        for idx, score in enumerate(bm25_scores):
            if idx < len(self.persisted.chunks):
                chunk = self.persisted.chunks[idx]
                lexical_hits.append((str(chunk["chunk_id"]), float(score)))

        dense_map = normalize_scores(dense_hits)
        lexical_map = normalize_scores(lexical_hits)
        by_id = {str(chunk["chunk_id"]): chunk for chunk in self.persisted.chunks}
        fused: dict[str, float] = {}
        query_lower = query.lower()
        for chunk_id, chunk in by_id.items():
            source_path = chunk["source_path"].lower()
            filename = Path(source_path).name.lower()
            is_match = False
            if filename in query_lower:
                is_match = True
            else:
                parts = filename.split('.')
                if len(parts) > 1 and len(parts[0]) > 2 and parts[0] in query_lower:
                    is_match = True
            if is_match:
                fused[chunk_id] = 3.0

        for chunk_id, score in dense_map.items():
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 0.7 * score
        for chunk_id, score in lexical_map.items():
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 0.3 * score

        ranking = sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_k]
        results: list[SearchHit] = []
        for chunk_id, score in ranking:
            payload = by_id.get(chunk_id)
            if not payload:
                continue
            results.append(
                SearchHit(
                    chunk_id=chunk_id,
                    score=float(score),
                    source_path=payload["source_path"],
                    title=payload["title"],
                    content=payload["content"],
                    summary=payload["summary"],
                    language=payload["language"],
                    chunk_type=payload["chunk_type"],
                    start_line=payload.get("start_line"),
                    end_line=payload.get("end_line"),
                    metadata=payload.get("metadata", {}),
                )
            )
        return results

    def search_by_paths(self, query: str, paths: list[str], top_k: int = 5) -> list[SearchHit]:
        conditions = [FieldCondition(key="source_path", match=MatchAny(any=paths))]
        query_filter = Filter(must=conditions)
        response = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=query_filter,
            with_payload=True,
            limit=1000,
        )
        results = []
        query_tokens = set(_tokenize(query))
        records = response[0]
        for record in records:
            payload = record.payload or {}
            text = f"{payload.get('title', '')} {payload.get('summary', '')} {payload.get('content', '')}"
            overlap = len(query_tokens.intersection(_tokenize(text)))
            if overlap == 0:
                continue
            results.append(
                SearchHit(
                    chunk_id=str(payload.get("chunk_id", record.id)),
                    score=float(overlap),
                    source_path=payload["source_path"],
                    title=payload["title"],
                    content=payload["content"],
                    summary=payload["summary"],
                    language=payload["language"],
                    chunk_type=payload["chunk_type"],
                    start_line=payload.get("start_line"),
                    end_line=payload.get("end_line"),
                    metadata=payload.get("metadata", {}),
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]


def _tokenize(text: str) -> list[str]:
    return re.findall(r'[a-zA-Z0-9_]+', text.lower())


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


async def compress_context_with_embeddings(query: str, hits: list[SearchHit], top_n_snippets: int = 5, threshold: float = 0.35) -> list[SearchHit]:
    """Compress retrieved hits by keeping only the most semantically relevant snippets of content."""
    from rag_local.embed import OllamaClient
    from rag_local.config import SETTINGS
    import math
    import logging

    logger = logging.getLogger(__name__)

    if not hits:
        return hits

    client = OllamaClient()
    try:
        query_vecs = await client.embed(SETTINGS.ollama_embed_model, [query])
        if not query_vecs:
            return hits
        query_vec = query_vecs[0]
        
        compressed_hits = []
        for hit in hits:
            content = hit.content or ""
            sections = [s.strip() for s in content.split("\n\n") if s.strip()]
            if not sections or len(content) < 500:
                compressed_hits.append(hit)
                continue
                
            section_vecs = await client.embed(SETTINGS.ollama_embed_model, sections)
            if not section_vecs or len(section_vecs) != len(sections):
                compressed_hits.append(hit)
                continue
                
            scored_sections = []
            for sec, sec_vec in zip(sections, section_vecs):
                dot = sum(a * b for a, b in zip(query_vec, sec_vec))
                norm_q = math.sqrt(sum(a * a for a in query_vec))
                norm_s = math.sqrt(sum(a * a for a in sec_vec))
                sim = dot / (norm_q * norm_s) if norm_q > 0 and norm_s > 0 else 0.0
                if sim >= threshold:
                    scored_sections.append((sim, sec))
                    
            scored_sections.sort(key=lambda x: x[0], reverse=True)
            top_sections = scored_sections[:top_n_snippets]
            
            if top_sections:
                ordered = sorted(top_sections, key=lambda x: sections.index(x[1]))
                compressed_content = "\n\n... [snipped less relevant code] ...\n\n".join(sec for _, sec in ordered)
                
                compressed_hit = SearchHit(
                    chunk_id=hit.chunk_id,
                    score=hit.score,
                    source_path=hit.source_path,
                    title=hit.title,
                    content=compressed_content,
                    summary=hit.summary,
                    language=hit.language,
                    chunk_type=hit.chunk_type,
                    start_line=hit.start_line,
                    end_line=hit.end_line,
                    metadata=hit.metadata
                )
                compressed_hits.append(compressed_hit)
            else:
                compressed_hit = SearchHit(
                    chunk_id=hit.chunk_id,
                    score=hit.score,
                    source_path=hit.source_path,
                    title=hit.title,
                    content=f"No directly relevant code block found matching threshold. Summary: {hit.summary}",
                    summary=hit.summary,
                    language=hit.language,
                    chunk_type=hit.chunk_type,
                    start_line=hit.start_line,
                    end_line=hit.end_line,
                    metadata=hit.metadata
                )
                compressed_hits.append(compressed_hit)
        return compressed_hits
    except Exception as e:
        logger.warning("[CompressContext] Context compression failed: %s", e)
        return hits
