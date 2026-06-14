"""Tool Selection Router for Nexus.

Before the LLM planner sees the full MCP tool catalogue, this module pre-filters
the available tools down to the most relevant subset using embedding cosine
similarity.  This has two benefits:

1. **Shorter prompts** — the planner only sees tools it might actually use,
   keeping token counts low.
2. **Better tool choice** — fewer distracting options means the LLM is less
   likely to pick an irrelevant tool.

How it works
------------
- On first call, fetch all tools from ``mcp_manager.get_all_tools()``.
- Embed each tool description (name + description + schema hint).
- Embed the user query.
- Return the top-K tools by cosine similarity.
- Results are cached for the session (tool descriptions are stable).

Fallback
--------
If the embedding model is unavailable, the router returns all tools unchanged
(graceful degradation).
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Session-level cache: tool_name → embedding vector
_tool_embed_cache: dict[str, list[float]] = {}
_all_tools_snapshot: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tool_text(tool: dict[str, Any]) -> str:
    """Build a short text representation of a tool for embedding."""
    server = tool.get("server_name", "")
    name = tool.get("name", "")
    desc = (tool.get("description") or "").strip().replace("\n", " ")[:200]
    # Include parameter names if present
    schema = tool.get("inputSchema") or {}
    props = list((schema.get("properties") or {}).keys())
    param_hint = " params: " + ", ".join(props[:6]) if props else ""
    return f"{server}/{name}: {desc}{param_hint}"


# ---------------------------------------------------------------------------
# Main selection function
# ---------------------------------------------------------------------------

async def select_tools(
    query: str,
    all_tools: list[dict[str, Any]],
    top_k: int = 12,
) -> list[dict[str, Any]]:
    """Return the ``top_k`` most relevant tools for ``query``.

    Falls back to returning all tools if embedding fails.
    """
    global _tool_embed_cache, _all_tools_snapshot

    if not all_tools:
        return []

    if len(all_tools) <= top_k:
        # No need to filter when the catalogue is already small
        return all_tools

    try:
        from .embed import OllamaClient
        from .config import SETTINGS

        client = OllamaClient()
        model = SETTINGS.ollama_embed_model

        # Detect if tool catalogue changed (e.g. new MCP server connected)
        current_names = {t.get("name", "") for t in all_tools}
        cached_names = {t.get("name", "") for t in _all_tools_snapshot}
        if current_names != cached_names:
            _tool_embed_cache.clear()
            _all_tools_snapshot = list(all_tools)

        # Embed tools that haven't been embedded yet (batch)
        new_tools = [t for t in all_tools if t.get("name", "") not in _tool_embed_cache]
        if new_tools:
            texts = [_tool_text(t) for t in new_tools]
            try:
                vectors = await asyncio.wait_for(
                    client.embed(model, texts), timeout=30.0
                )
                for tool, vec in zip(new_tools, vectors):
                    _tool_embed_cache[tool.get("name", "")] = vec
            except Exception as exc:
                logger.warning("[ToolRouter] Embedding failed for tools: %s", exc)
                return all_tools[:top_k]  # graceful fallback

        # Embed the query
        query_vec_list = await asyncio.wait_for(
            client.embed(model, [query]), timeout=10.0
        )
        query_vec = query_vec_list[0]

        # Score each tool
        scored: list[tuple[float, dict[str, Any]]] = []
        for tool in all_tools:
            tool_vec = _tool_embed_cache.get(tool.get("name", ""))
            if tool_vec is None:
                continue
            sim = _cosine(query_vec, tool_vec)
            scored.append((sim, tool))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [t for _, t in scored[:top_k]]

        logger.debug(
            "[ToolRouter] Selected %d/%d tools for query (top sim=%.3f)",
            len(selected),
            len(all_tools),
            scored[0][0] if scored else 0.0,
        )
        return selected

    except Exception as exc:
        logger.warning("[ToolRouter] Falling back to full tool list: %s", exc)
        return all_tools


# ---------------------------------------------------------------------------
# Helper: format tool catalogue for the planner prompt
# ---------------------------------------------------------------------------

def format_tools_prompt(tools: list[dict[str, Any]]) -> str:
    """Render a compact tool catalogue suitable for inclusion in a system prompt."""
    if not tools:
        return "No MCP tools available."

    lines = ["Available MCP tools (server/tool — description):"]
    seen_servers: set[str] = set()

    for tool in tools:
        server = tool.get("server_name", "unknown")
        name = tool.get("name", "?")
        desc = (tool.get("description") or "").strip().replace("\n", " ")[:120]
        schema = tool.get("inputSchema") or {}
        props = list((schema.get("properties") or {}).keys())
        required = schema.get("required") or []

        param_str = ""
        if props:
            param_parts = []
            for p in props[:5]:
                req_marker = "*" if p in required else ""
                param_parts.append(f"{p}{req_marker}")
            param_str = f" [{', '.join(param_parts)}]"

        if server not in seen_servers:
            lines.append(f"\n  [{server}]")
            seen_servers.add(server)
        lines.append(f"    • {name}{param_str}: {desc}")

    return "\n".join(lines)
