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


# ── New types for Nexus Optimization Roadmap ──────────────────────────────────

class ConfidenceScore(BaseModel):
    """LLM-estimated confidence in a plan or answer."""
    score: float = Field(ge=0.0, le=1.0, description="0 = no confidence, 1 = fully confident")
    reason: str = ""
    needs_verification: bool = False


class ArtifactRecord(BaseModel):
    """Track files/outputs created during task execution."""
    path: str
    kind: str = "file"           # file | dir | url | image
    created_at: str = ""         # ISO-8601
    size_bytes: int = 0
    task_name: str = ""
    verified: bool = False


class ExecutionMetrics(BaseModel):
    """Per-run observability data."""
    total_tasks: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0
    healed: int = 0              # tasks recovered via self-healing
    wall_time_ms: float = 0.0
    token_count: int = 0
    cache_hits: int = 0


class HeartbeatEvent(BaseModel):
    """Emitted during streaming execution to update the CLI spinner."""
    stage: str                   # plan | execute | verify | synthesize
    task_name: str = ""
    message: str = ""


class PlanTask(BaseModel):
    name: str
    kind: Literal["retrieve", "mcp", "write", "code", "web", "scrape", "git", "download"]
    query: str
    priority: int = 0
    depends_on: list[str] = Field(default_factory=list, description="names of tasks this one depends on")
    can_parallel: bool = True    # hint: safe to run concurrently with other tasks
    artifact_targets: list[str] = Field(default_factory=list, description="specific file paths or endpoints expected to be created/changed")
    verification_rules: list[str] = Field(default_factory=list, description="custom rules, exit codes, or checks to execute for validation")


class ExecutionPlan(BaseModel):
    objective: str
    tasks: list[PlanTask]
    response_style: str = "concise"
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list, description="constraints and limitations extracted from user input")
    confidence: float = 0.8      # planner's self-assessed confidence


class WorkerResult(BaseModel):
    task_name: str
    kind: str
    success: bool
    summary: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    retries: int = 0
    healed: bool = False         # True if recovered via alternative approach
    confidence: float = 1.0


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
    metrics: ExecutionMetrics = Field(default_factory=ExecutionMetrics)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    memory_context: str = ""     # recalled from embedding memory

