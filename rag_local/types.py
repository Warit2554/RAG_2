from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChunkRecord(BaseModel):
    chunk_id: str
    source_path: str
    language: str
    chunk_type: str
    title: str
    content: str
    summary: str
    scope: list[str] = Field(default_factory=list)
    start_line: int | None = None
    end_line: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    chunk_id: str
    score: float
    source_path: str
    title: str
    content: str
    summary: str
    language: str
    chunk_type: str
    start_line: int | None = None
    end_line: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteDecision(BaseModel):
    route: Literal["general", "rag", "code_analysis", "web_search"]
    confidence: float = Field(ge=0, le=1)
    reason: str


class PlanTask(BaseModel):
    name: str
    kind: Literal["retrieve", "mcp", "write", "code", "web", "scrape", "git", "download"]
    query: str
    priority: int = 0


class ExecutionPlan(BaseModel):
    objective: str
    tasks: list[PlanTask]
    response_style: str = "concise"


class WorkerResult(BaseModel):
    task_name: str
    kind: str
    success: bool
    summary: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class RagState(BaseModel):
    user_input: str
    route: str = "general"
    route_reason: str = ""
    plan: ExecutionPlan | None = None
    retrieved_chunks: list[SearchHit] = Field(default_factory=list)
    code_results: list[WorkerResult] = Field(default_factory=list)
    web_results: list[WorkerResult] = Field(default_factory=list)
    general_answer: str = ""
    final_answer: str = ""
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    clarification_prompt: dict[str, Any] | None = None
    clarification_response: str | None = None


@dataclass(slots=True)
class AppPaths:
    root: Path
    data_dir: Path
    index_dir: Path

    @classmethod
    def from_env(cls, root: Path, data_dir: Path | None = None) -> "AppPaths":
        base = data_dir or (root / "data")
        return cls(root=root, data_dir=base, index_dir=base / "indexes")


@dataclass(slots=True)
class ChunkingOptions:
    max_tokens: int = 320
    max_chars_fallback: int = 1600
    summary_chars: int = 240


@dataclass(slots=True)
class IngestedDocument:
    path: Path
    language: str
    chunks: list[ChunkRecord] = field(default_factory=list)

