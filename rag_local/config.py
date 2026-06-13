from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import dotenv
dotenv.load_dotenv()


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value else default


@dataclass(slots=True)
class Settings:
    ollama_host: str = _env("OLLAMA_HOST", "http://localhost:11434")
    ollama_chat_model: str = _env("OLLAMA_CHAT_MODEL", "llama3.1:8b")
    ollama_router_model: str = _env("OLLAMA_ROUTER_MODEL", "llama3.1:8b")
    ollama_orchestrator_model: str = _env("OLLAMA_ORCHESTRATOR_MODEL", "llama3.1:8b")
    ollama_embed_model: str = _env("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    qdrant_url: str = _env("QDRANT_URL", "http://localhost:6333")
    qdrant_collection: str = _env("QDRANT_COLLECTION", "rag_local_chunks")
    searxng_url: str = _env("SEARXNG_URL", "http://localhost:8080")
    rag_data_dir: Path = Path(_env("RAG_DATA_DIR", "./data")).expanduser()
    rag_max_chunk_tokens: int = int(_env("RAG_MAX_CHUNK_TOKENS", "320"))
    rag_top_k: int = int(_env("RAG_TOP_K", "5"))
    rag_keep_alive: str = _env("RAG_KEEP_ALIVE", "1h")
    nexus_theme: str = _env("NEXUS_THEME", "Classic Theme")

    @property
    def index_dir(self) -> Path:
        return self.rag_data_dir / "indexes"


SETTINGS = Settings()

