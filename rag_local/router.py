from __future__ import annotations

from dataclasses import dataclass

from .config import SETTINGS
from .embed import OllamaClient, build_messages
from .memory import compress_history
from .types import RouteDecision
from .utils import safe_json_loads


ROUTER_SYSTEM = """You are a strict intent router for a local RAG system.
Return only JSON with keys route, confidence, reason.
Routes:
- general: casual conversation or simple question with no repo/data lookup.
- rag: question about indexed documents or files.
- code_analysis: question that requests code inspection, debugging, refactoring, or repository reasoning.
- web_search: question that needs fresh external information.
Prefer rag/code_analysis when the user references files, code, modules, repos, errors, or implementation details.
Confidence must be between 0 and 1.
"""


@dataclass(slots=True)
class RouterDecision:
    decision: RouteDecision
    raw: str


async def route_query(query: str, history: list[dict[str, str]] | None = None) -> RouterDecision:
    client = OllamaClient()
    messages = build_messages(ROUTER_SYSTEM, query, compress_history(history or []))
    try:
        raw = await client.chat(
            SETTINGS.ollama_router_model,
            messages,
            temperature=0.0,
            keep_alive=SETTINGS.rag_keep_alive,
            format="json",
        )
        parsed = safe_json_loads(raw)
        parsed = parsed if isinstance(parsed, dict) else {}
        route = parsed.get("route", "rag")
        lower = query.lower()
        if route in {"general", "rag"} and any(w in lower for w in ["save", "download", "write"]):
            # If they want to download or save, only keep as rag/code_analysis if there's explicit local codebase reference
            has_local_ref = any(ind in lower for ind in [".py", ".js", ".ts", ".json", ".md", "class ", "def ", "config.py", "rag_local", "repo", "codebase", "repository"])
            if not has_local_ref:
                route = "web_search"
        elif route == "general" and any(ind in lower for ind in [".py", ".js", ".ts", ".json", ".md", "class ", "def ", "config.py", "rag_local"]):
            route = "code_analysis"
        
        # Action verb override: do not allow general route for execution requests
        action_verbs = ["create", "install", "download", "setup", "configure", "deploy"]
        if any(verb in lower for verb in action_verbs):
            if route == "general":
                route = "rag"
        
        decision = RouteDecision(
            route=route,
            confidence=float(parsed.get("confidence", 0.5)),
            reason=str(parsed.get("reason", "model-router")),
        )
        return RouterDecision(decision=decision, raw=raw)
    except Exception as exc:
        lower = query.lower()
        has_local_ref = any(word in lower for word in ["repo", "code", "bug", "function", "class", "stack trace", "traceback", "markdown", "rag", "retrieve"])
        if any(word in lower for word in ["save", "download", "write"]):
            if not has_local_ref:
                route = "web_search"
            else:
                route = "code_analysis" if any(word in lower for word in ["code", "bug", "function", "class", "repo"]) else "rag"
        elif any(word in lower for word in ["file", "repo", "code", "bug", "function", "class", "stack trace", "traceback"]):
            route = "code_analysis"
        elif any(word in lower for word in ["news", "today", "latest", "current", "web", "internet"]):
            route = "web_search"
        elif any(word in lower for word in ["document", "docs", "markdown", "rag", "retrieve"]):
            route = "rag"
        else:
            route = "general"
        return RouterDecision(
            decision=RouteDecision(route=route, confidence=0.55, reason=f"fallback-router:{exc.__class__.__name__}"),
            raw="fallback",
        )
