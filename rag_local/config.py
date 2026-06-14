from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Locate workspace directory
WORKSPACE_DIR = Path(".").resolve()
PACKAGE_ROOT = Path(__file__).parent.parent.resolve()

# Find config source directory
if (WORKSPACE_DIR / "mcp_config.json").exists() or (WORKSPACE_DIR / ".env").exists():
    CONFIG_DIR = WORKSPACE_DIR
else:
    CONFIG_DIR = PACKAGE_ROOT

import dotenv
# Load the .env from CONFIG_DIR
dotenv.load_dotenv(CONFIG_DIR / ".env")


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
    rag_plan_max_tasks: int = int(_env("RAG_PLAN_MAX_TASKS", "8"))
    mcp_ops_timeout: float = float(_env("MCP_OPS_TIMEOUT", "300.0"))

    # ── Caching ────────────────────────────────────────────────────────────────
    cache_enabled: bool = _env("NEXUS_CACHE_ENABLED", "true").lower() == "true"
    cache_ttl_seconds: int = int(_env("NEXUS_CACHE_TTL", "300"))
    cache_max_size: int = int(_env("NEXUS_CACHE_MAX_SIZE", "256"))

    # ── Self-Healing / Retry ────────────────────────────────────────────────────
    executor_max_retries: int = int(_env("NEXUS_EXECUTOR_MAX_RETRIES", "3"))
    executor_retry_backoff: float = float(_env("NEXUS_EXECUTOR_RETRY_BACKOFF", "1.5"))
    executor_parallel_limit: int = int(_env("NEXUS_PARALLEL_LIMIT", "4"))

    # ── Confidence & Verification ───────────────────────────────────────────────
    confidence_threshold: float = float(_env("NEXUS_CONFIDENCE_THRESHOLD", "0.6"))
    verification_enabled: bool = _env("NEXUS_VERIFICATION_ENABLED", "true").lower() == "true"

    # ── Agent Memory ────────────────────────────────────────────────────────────
    agent_memory_enabled: bool = _env("NEXUS_AGENT_MEMORY_ENABLED", "true").lower() == "true"
    agent_memory_top_k: int = int(_env("NEXUS_AGENT_MEMORY_TOP_K", "5"))
    agent_memory_collection: str = _env("NEXUS_AGENT_MEMORY_COLLECTION", "nexus_agent_memory")

    # ── Prompt Registry ─────────────────────────────────────────────────────────
    prompts_dir: Path = Path(_env("NEXUS_PROMPTS_DIR", "./prompts")).expanduser()

    # ── Observability ────────────────────────────────────────────────────────────
    audit_log_enabled: bool = _env("NEXUS_AUDIT_LOG", "true").lower() == "true"
    audit_log_path: Path = Path(_env("NEXUS_AUDIT_LOG_PATH", "./nexus_audit.jsonl")).expanduser()

    def __post_init__(self) -> None:
        if not self.rag_data_dir.is_absolute():
            self.rag_data_dir = (WORKSPACE_DIR / self.rag_data_dir).resolve()

    @property
    def index_dir(self) -> Path:
        return self.rag_data_dir / "indexes"


SETTINGS = Settings()

