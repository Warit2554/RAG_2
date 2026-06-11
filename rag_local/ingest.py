from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .chunking import split_source
from .config import SETTINGS
from .embed import OllamaClient
from .store import QdrantStore
from .types import ChunkingOptions
from .utils import detect_language


DEFAULT_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "data", ".chainlit"}
DEFAULT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
}


@dataclass(slots=True)
class IngestionResult:
    files_seen: int
    chunks_created: int
    embedded: int


def discover_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in DEFAULT_SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in DEFAULT_EXTENSIONS:
            continue
        files.append(path)
    return sorted(files)


async def ingest_directory(root: Path, *, max_chunk_tokens: int | None = None) -> IngestionResult:
    ollama = OllamaClient()
    store = QdrantStore()
    options = ChunkingOptions(max_tokens=max_chunk_tokens or SETTINGS.rag_max_chunk_tokens)

    all_chunks = []
    all_texts = []
    files = discover_files(root)
    for file_path in files:
        chunks = split_source(file_path, options)
        for chunk in chunks:
            all_chunks.append(chunk)
            all_texts.append(chunk.content + "\n\n" + chunk.summary)

    if not all_chunks:
        return IngestionResult(files_seen=len(files), chunks_created=0, embedded=0)

    embeddings = await ollama.embed(SETTINGS.ollama_embed_model, all_texts)
    store.upsert_chunks(all_chunks, embeddings)
    return IngestionResult(files_seen=len(files), chunks_created=len(all_chunks), embedded=len(embeddings))

