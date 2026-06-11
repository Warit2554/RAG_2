from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def detect_language(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".md": "markdown",
        ".txt": "text",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix, "text")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def approximate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_list(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def safe_json_loads(text: str) -> dict | list | None:
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
        return None


def compact_whitespace(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize_scores(items: Iterable[tuple[str, float]]) -> dict[str, float]:
    values = list(items)
    if not values:
        return {}
    max_score = max(score for _, score in values) or 1.0
    return {key: score / max_score for key, score in values}

