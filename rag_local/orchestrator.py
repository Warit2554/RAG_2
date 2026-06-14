from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from .config import SETTINGS
from .embed import OllamaClient, build_messages
from .memory import compress_history
from rag_local.tools.code_execution.tool import run_python_in_docker
from rag_local.tools.retrieval.tool import hybrid_retrieve, QdrantStore
from rag_local.tools.web_search.tool import local_web_search
from rag_local.tools.web_scraper.tool import scrape_url
from rag_local.tools.git_inspector.tool import run_git_command
from rag_local.tools.image_downloader.tool import download_image, search_for_image_url
from .types import ExecutionPlan, PlanTask, RagState, WorkerResult
from .utils import safe_json_loads
from .prompts import NEXUS_MCP_AUTHORITY_PROMPT


_LOCAL_KIND_TO_MCP = {
    "web": {"server_name": "duckduckgo", "tool_name": "search"},
    "scrape": {"server_name": "fetch", "tool_name": "fetch"},
    "git": {"server_name": "git", "tool_name": "git_status"},
    "code": {"server_name": "operations", "tool_name": "execute_operational_command"},
    "write": {"server_name": "filesystem", "tool_name": "write_file"},
    "download": {"server_name": "filesystem", "tool_name": "write_file"},
}

ORCHESTRATOR_SYSTEM = """You are a local RAG orchestrator that uses MCP (Model Context Protocol) tools.
Create a short JSON plan with keys: objective, tasks, response_style.
Each task must have: name, kind, query, priority.

The ONLY valid kinds are:
- retrieve: search locally indexed documents for information about the codebase.
- mcp: call a tool from a connected MCP server for everything else.

For 'mcp' tasks, format query as a JSON object:
  {"server_name": "<server>", "tool_name": "<tool>", "arguments": {<args>}}

FILE DOWNLOAD STRATEGY (use this order):
1. Search for the official download URL using duckduckgo search.
2. Use execute_operational_command with wget or curl to download it:
   {"server_name": "operations", "tool_name": "execute_operational_command", "arguments": {"command": "wget -O filename.jar 'URL'", "timeout_seconds": 120}}
3. Verify the file was downloaded with: {"server_name": "filesystem", "tool_name": "get_file_info", "arguments": {"path": "filename.jar"}}
NEVER rely only on fetch/scrape to download binary files. Always use wget/curl via execute_operational_command.

Example Output:
{
  "objective": "Understand the repository",
  "tasks": [
    {
      "name": "list_files",
      "kind": "mcp",
      "query": {"server_name": "filesystem", "tool_name": "list_directory", "arguments": {"path": "."}},
      "priority": 1
    }
  ],
  "response_style": "detailed"
}

CRITICAL RULES:
- Use kind 'mcp' for ALL tool calls (web search, file operations, git, code, browser, etc.).
- Use kind 'retrieve' ONLY for searching local indexed documents.
- Do NOT use any other kind value.
"""


def _normalize_kind(kind: str, query: str) -> str:
    value = kind.strip().lower()
    lower_query = query.lower().strip()

    # Save-to-disk intent always wins — even if LLM says 'code'
    if any(phrase in lower_query for phrase in [
        "save as .sh", "save as .py", "save as .txt", "save as .bash",
        "save to file", "write to file", "save it as", "save the script",
    ]) or re.search(r'\bsave\b.{0,20}\.(sh|py|bash|zsh|fish|txt|ps1|bat|cmd)\b', lower_query):
        return "write"

    if value == "mcp":
        return "mcp"
    if value in {"write"}:
        return "write"
    if value in {"code", "code_analysis", "analysis"}:
        return "code"
    if value in {"web", "web_search", "search", "internet"}:
        return "web"
    if value == "retrieve":
        return "retrieve"
    if value == "scrape":
        return "scrape"
    if value == "git":
        return "git"
    if value == "download":
        return "download"
    if lower_query.startswith(("http://", "https://")):
        return "scrape"
    if any(word in lower_query for word in ["git status", "git diff", "git log", "git show", "git branch"]):
        return "git"
    if any(word in lower_query for word in ["download", "save", "write"]):
        return "download"
    if any(word in lower_query for word in ["code", "class", "function", "bug", "traceback", "repo"]):
        return "code"
    return "web"


ALLOWED_MCP_TOOLS = {
    "filesystem": {"directory_tree", "list_directory", "read_file", "write_file", "search_files", "get_file_info"},
    "git": {"git_status", "git_diff", "git_log", "git_show"},
    "duckduckgo": {"search"},
    "fetch": {"fetch"},
    "operations": {"execute_operational_command"},
}


async def build_plan(state: RagState) -> ExecutionPlan:
    from .mcp_client import mcp_manager
    import json
    all_tools = await mcp_manager.get_all_tools()
    
    tools_prompt = ""
    if all_tools:
        tools_prompt = "\nAvailable Dynamic MCP Tools (Server -> Tool):\n"
        for t in all_tools:
            srv = t.get('server_name')
            name = t.get('name')
            if srv in ALLOWED_MCP_TOOLS and name in ALLOWED_MCP_TOOLS[srv]:
                props = t.get('input_schema', {}).get('properties', {}) or {}
                req = t.get('input_schema', {}).get('required', []) or []
                args_hint = ", ".join(f"{k} (required)" if k in req else k for k in props.keys())
                desc = (t.get('description') or '').replace('\n', ' ')[:60]
                tools_prompt += f"- {srv} -> {name}: {desc} | Args: {{{args_hint}}}\n"
        tools_prompt += (
            "\nTo use any of these dynamic MCP tools, you MUST set kind to 'mcp', and format 'query' as a JSON object: \n"
            "  \"query\": {\"server_name\": \"<server_name>\", \"tool_name\": \"<tool_name>\", \"arguments\": {<args>}}\n"
        )

    client = OllamaClient()
    system_prompt = ORCHESTRATOR_SYSTEM + "\n\n" + tools_prompt
    messages = build_messages(system_prompt, state.user_input, compress_history(state.chat_history))
    try:
        raw = await client.chat(
            SETTINGS.ollama_orchestrator_model,
            messages,
            temperature=0.2,
            keep_alive=SETTINGS.rag_keep_alive,
            format="json",
        )
        parsed = safe_json_loads(raw)
        parsed = parsed if isinstance(parsed, dict) else {}
        
        def _parse_query(q: Any) -> str:
            if isinstance(q, dict):
                return json.dumps(q)
            return str(q)

        tasks = [
            PlanTask(
                name=item.get("name", f"task_{idx}"),
                kind=_normalize_kind(str(item.get("kind", "")), str(item.get("query", state.user_input))),
                query=_parse_query(item.get("query", state.user_input)),
                priority=int(item.get("priority", idx)),
            )
            for idx, item in enumerate(parsed.get("tasks", [])[:4], start=1)
            if isinstance(item, dict) and str(item.get("kind", "")).lower() != "retrieve"
        ]
        if not tasks:
            raise ValueError("No valid tasks parsed from LLM plan.")
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
        if any(word in lower for word in ["save", "download", "write"]):
            tasks.append(PlanTask(name="web_lookup", kind="web", query=state.user_input, priority=0))
            tasks.append(PlanTask(name="image_download", kind="download", query=state.user_input, priority=1))
        return ExecutionPlan(objective=state.user_input, tasks=tasks, response_style="concise")


async def execute_task(task: PlanTask, state: RagState | None = None) -> WorkerResult:
    """Execute a single task.  Only 'mcp' and 'retrieve' are live; all other kinds
    are re-routed through MCP automatically."""
    import json as _json

    # ── retrieve: local vector store (kept as-is) ─────────────────────────────
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

    # ── mcp: call MCP tool directly ───────────────────────────────────────────
    if task.kind == "mcp":
        from .mcp_client import mcp_manager
        try:
            # Try to parse structured query first
            try:
                params = _json.loads(task.query)
            except Exception:
                # query is plain text → try to infer the best MCP tool
                params = {"server_name": "duckduckgo", "tool_name": "search", "arguments": {"query": task.query}}

            server_name = params.get("server_name", "")
            tool_name   = params.get("tool_name", "")
            arguments   = params.get("arguments", {})

            if not server_name or not tool_name:
                return WorkerResult(task_name=task.name, kind=task.kind, success=False,
                                    summary="MCP task missing server_name or tool_name.")

            result_str = await mcp_manager.call_tool(server_name, tool_name, arguments)
            return WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success="Error" not in result_str,
                summary=result_str,
                artifacts=[{"server_name": server_name, "tool_name": tool_name,
                            "arguments": arguments, "result": result_str}],
            )
        except Exception as exc:
            return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary=str(exc))

    # ── legacy local kinds → redirect to MCP ─────────────────────────────────
    # These should no longer appear (orchestrator prompt forbids them) but if they
    # do (e.g. LLM hallucination) we gracefully forward to the closest MCP server.
    from .mcp_client import mcp_manager
    fallback = _LOCAL_KIND_TO_MCP.get(task.kind)
    if fallback and mcp_manager.sessions:
        server_name = fallback["server_name"]
        tool_name   = fallback["tool_name"]
        # Build best-effort arguments
        if task.kind in {"web"}:
            arguments = {"query": task.query}
        elif task.kind == "scrape":
            import re as _re
            url_match = _re.search(r"https?://\S+", task.query)
            arguments = {"url": url_match.group(0) if url_match else task.query}
        elif task.kind == "git":
            arguments = {"repo_path": ".", "command": "git status"}
        elif task.kind in {"code"}:
            arguments = {"command": task.query, "timeout_seconds": 30}
        elif task.kind in {"write", "download"}:
            arguments = {"path": "output.txt", "content": task.query}
        else:
            arguments = {"query": task.query}

        result_str = await mcp_manager.call_tool(server_name, tool_name, arguments)
        return WorkerResult(
            task_name=task.name,
            kind="mcp",
            success="Error" not in result_str,
            summary=result_str,
            artifacts=[{"redirected_from": task.kind, "server_name": server_name,
                        "tool_name": tool_name, "result": result_str}],
        )

    return WorkerResult(task_name=task.name, kind=task.kind, success=False,
                        summary=f"Unknown task kind '{task.kind}'. Only 'mcp' and 'retrieve' are supported.")



async def run_parallel_tasks(plan: ExecutionPlan, state: RagState) -> list[WorkerResult]:

    ordered = sorted(plan.tasks, key=lambda item: item.priority)
    return await asyncio.gather(*(execute_task(task, state) for task in ordered))


SYNTHESIZER_SYSTEM = """You are the final synthesizer for a local RAG system.
Use only the provided worker results and retrieved context.
Answer clearly, call out uncertainty, and keep the response practical.

FAILURE RECOVERY RULES:
If a tool task failed or produced an error:
1. Identify what failed and why (e.g. URL not found, file not accessible, command error).
2. Suggest the exact alternative command nexus should try next, for example:
   - For file downloads: use `wget -O <filename> '<url>'` or `curl -L -o <filename> '<url>'` via execute_operational_command.
   - For web lookups: try a more specific search query or the official domain directly.
   - For file not found: check with list_directory or get_file_info first.
3. If this is a RETRY attempt (query starts with [RETRY]), aggressively try alternative methods — do NOT repeat the same approach that failed.

Never just give up and explain the failure. Always attempt an alternative tool path.
"""


async def synthesize(state: RagState, config: Any = None) -> str:
    client = OllamaClient()
    content_lines = [f"User request: {state.user_input}", f"Route: {state.route}", f"Plan: {state.plan.model_dump() if state.plan else {}}"]
    if state.retrieved_chunks:
        content_lines.append("Retrieved chunks:")
        for hit in state.retrieved_chunks[:5]:
            content_lines.append(f"- {hit.title} [{hit.source_path}]\nSummary: {hit.summary}\nContent:\n{hit.content}\n---")
    if state.code_results:
        content_lines.append("Code results:")
        for result in state.code_results:
            summary = result.summary or ""
            if len(summary) > 4000:
                summary = summary[:4000] + "\n... [TRUNCATED due to length] ..."
            content_lines.append(f"- {result.task_name}: {summary}")
    if state.web_results:
        content_lines.append("Web results:")
        for result in state.web_results:
            summary = result.summary or ""
            if len(summary) > 4000:
                summary = summary[:4000] + "\n... [TRUNCATED due to length] ..."
            content_lines.append(f"- {result.task_name}: {summary}")
    if state.general_answer:
        content_lines.append(f"General answer: {state.general_answer}")

    system_prompt = SYNTHESIZER_SYSTEM
    messages = build_messages(system_prompt, "\n".join(content_lines), state.chat_history)
    
    token_callback = None
    if config:
        if isinstance(config, dict):
            token_callback = config.get("configurable", {}).get("token_callback")
        else:
            try:
                configurable = getattr(config, "get", lambda k, d=None: None)("configurable") or getattr(config, "configurable", None)
                if isinstance(configurable, dict):
                    token_callback = configurable.get("token_callback")
            except Exception:
                pass

    try:
        if token_callback:
            answer = ""
            async for token in client.chat_stream(
                SETTINGS.ollama_chat_model,
                messages,
                temperature=0.2,
                keep_alive=SETTINGS.rag_keep_alive,
            ):
                answer += token
                if asyncio.iscoroutinefunction(token_callback):
                    await token_callback(token)
                else:
                    token_callback(token)
            return answer
        else:
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
            parts.extend(f"- {hit.title}: {hit.content}" for hit in state.retrieved_chunks[:5])
        if state.code_results:
            parts.append("Code evidence:")
            parts.extend(f"- {r.task_name}: {r.summary}" for r in state.code_results)
        if state.web_results:
            parts.append("Web evidence:")
            parts.extend(f"- {r.task_name}: {r.summary}" for r in state.web_results)
        if state.general_answer:
            parts.append(state.general_answer)
        return "\n".join(parts) if len(parts) > 1 else "No model available to synthesize a response."
