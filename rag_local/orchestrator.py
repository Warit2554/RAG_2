from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .config import SETTINGS
from .embed import OllamaClient, build_messages
from .memory import compress_history
from .sandbox import run_python_in_docker
from .search import hybrid_retrieve
from .searxng import local_web_search
from .store import QdrantStore
from .types import ExecutionPlan, PlanTask, RagState, WorkerResult
from .utils import safe_json_loads


ORCHESTRATOR_SYSTEM = """You are a local RAG orchestrator.
Create a short JSON plan with keys objective, tasks, response_style.
Each task must have name, kind, query, priority.
Kinds: retrieve, code, web.
Use retrieve for local indexed docs, code for repository/code analysis, web for fresh external info.
"""


def _normalize_kind(kind: str, query: str) -> str:
    value = kind.strip().lower()
    if value in {"code", "code_analysis", "analysis"}:
        return "code"
    if value in {"web", "web_search", "search", "internet"}:
        return "web"
    if value == "retrieve":
        return "retrieve"
    lower_query = query.lower()
    if any(word in lower_query for word in ["code", "class", "function", "bug", "traceback", "repo"]):
        return "code"
    return "web"


async def build_plan(state: RagState) -> ExecutionPlan:
    client = OllamaClient()
    messages = build_messages(ORCHESTRATOR_SYSTEM, state.user_input, compress_history(state.chat_history))
    try:
        raw = await client.chat(
            SETTINGS.ollama_orchestrator_model,
            messages,
            temperature=0.2,
            keep_alive=SETTINGS.rag_keep_alive,
        )
        parsed = safe_json_loads(raw)
        parsed = parsed if isinstance(parsed, dict) else {}
        tasks = [
            PlanTask(
                name=item.get("name", f"task_{idx}"),
                kind=_normalize_kind(str(item.get("kind", "")), str(item.get("query", state.user_input))),
                query=item.get("query", state.user_input),
                priority=int(item.get("priority", idx)),
            )
            for idx, item in enumerate(parsed.get("tasks", [])[:4], start=1)
            if isinstance(item, dict) and str(item.get("kind", "")).lower() != "retrieve"
        ]
        return ExecutionPlan(
            objective=str(parsed.get("objective", state.user_input)),
            tasks=tasks,
            response_style=str(parsed.get("response_style", "concise")),
        )
    except Exception:
        lower = state.user_input.lower()
        tasks = []
        if any(word in lower for word in ["code", "class", "function", "bug", "traceback", "repo"]):
            tasks.append(PlanTask(name="code_inspection", kind="code", query=state.user_input, priority=0))
        if any(word in lower for word in ["search", "news", "latest", "current", "today", "web", "internet"]):
            tasks.append(PlanTask(name="web_lookup", kind="web", query=state.user_input, priority=1))
        return ExecutionPlan(objective=state.user_input, tasks=tasks, response_style="concise")


async def execute_task(task: PlanTask) -> WorkerResult:
    if task.kind == "retrieve":
        retrieval = await hybrid_retrieve(task.query)
        summary = "\n".join(
            f"- {hit.title} ({hit.source_path}:{hit.start_line or 0}-{hit.end_line or 0}) score={hit.score:.2f}"
            for hit in retrieval.hits[:5]
        ) or "No local matches."
        return WorkerResult(
            task_name=task.name,
            kind=task.kind,
            success=True,
            summary=summary,
            artifacts=[hit.model_dump() for hit in retrieval.hits],
        )
    if task.kind == "web":
        try:
            results = await local_web_search(task.query)
            summary = "\n".join(f"- {r.title} {r.url}" for r in results) or "No local web search results."
            return WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success=True,
                summary=summary,
                artifacts=[r.__dict__ for r in results],
            )
        except Exception as exc:
            return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary=str(exc))
    if task.kind == "code":
        sandboxed = run_python_in_docker(
            "print('code analysis sandbox is ready')",
            timeout_seconds=10,
        )
        return WorkerResult(
            task_name=task.name,
            kind=task.kind,
            success=sandboxed.success,
            summary=sandboxed.stdout.strip() or sandboxed.stderr.strip() or "Code tool executed.",
            artifacts=[{"stdout": sandboxed.stdout, "stderr": sandboxed.stderr, "exit_code": sandboxed.exit_code}],
        )
    return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary="Unknown task kind.")


async def run_parallel_tasks(plan: ExecutionPlan) -> list[WorkerResult]:
    ordered = sorted(plan.tasks, key=lambda item: item.priority)
    return await asyncio.gather(*(execute_task(task) for task in ordered))


SYNTHESIZER_SYSTEM = """You are the final synthesizer for a local RAG system.
Use only the provided worker results and retrieved context.
Answer clearly, call out uncertainty, and keep the response practical.
"""


async def synthesize(state: RagState) -> str:
    client = OllamaClient()
    content_lines = [f"User request: {state.user_input}", f"Route: {state.route}", f"Plan: {state.plan.model_dump() if state.plan else {}}"]
    if state.retrieved_chunks:
        content_lines.append("Retrieved chunks:")
        for hit in state.retrieved_chunks[:5]:
            content_lines.append(f"- {hit.title} [{hit.source_path}] {hit.summary}")
    if state.code_results:
        content_lines.append("Code results:")
        for result in state.code_results:
            content_lines.append(f"- {result.task_name}: {result.summary}")
    if state.web_results:
        content_lines.append("Web results:")
        for result in state.web_results:
            content_lines.append(f"- {result.task_name}: {result.summary}")
    if state.general_answer:
        content_lines.append(f"General answer: {state.general_answer}")
    messages = build_messages(SYNTHESIZER_SYSTEM, "\n".join(content_lines), state.chat_history)
    try:
        answer = await client.chat(
            SETTINGS.ollama_chat_model,
            messages,
            temperature=0.2,
            keep_alive=SETTINGS.rag_keep_alive,
        )
        return answer
    except Exception:
        parts = [f"Route: {state.route}"]
        if state.retrieved_chunks:
            parts.append("Local evidence:")
            parts.extend(f"- {hit.title}: {hit.summary}" for hit in state.retrieved_chunks[:5])
        if state.code_results:
            parts.append("Code evidence:")
            parts.extend(f"- {r.task_name}: {r.summary}" for r in state.code_results)
        if state.web_results:
            parts.append("Web evidence:")
            parts.extend(f"- {r.task_name}: {r.summary}" for r in state.web_results)
        if state.general_answer:
            parts.append(state.general_answer)
        return "\n".join(parts) if len(parts) > 1 else "No model available to synthesize a response."
