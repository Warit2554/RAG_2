from __future__ import annotations

import asyncio
import logging
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
from .memory import get_lessons_memory, compress_history_async, compress_history


_LOCAL_KIND_TO_MCP = {
    "web": {"server_name": "duckduckgo", "tool_name": "search"},
    "scrape": {"server_name": "fetch", "tool_name": "fetch"},
    "git": {"server_name": "git", "tool_name": "git_status"},
    "code": {"server_name": "operations", "tool_name": "execute_operational_command"},
    "write": {"server_name": "filesystem", "tool_name": "write_file"},
    "download": {"server_name": "filesystem", "tool_name": "write_file"},
}

ORCHESTRATOR_SYSTEM = """You are a local RAG orchestrator that uses MCP (Model Context Protocol) tools.
Create a JSON plan with keys: objective, success_criteria, tasks, response_style.

'success_criteria' is an array of strings representing verifiable success criteria for the request.
Each task must have: name, kind, query, priority.

The ONLY valid kinds are:
- retrieve: search locally indexed documents for information about the codebase.
- mcp: call a tool from a connected MCP server for everything else.

For 'mcp' tasks, format query as a JSON object:
  {"server_name": "<server>", "tool_name": "<tool>", "arguments": {<args>}}

FILE DOWNLOAD STRATEGY (NO HALLUCINATED URLS):
- You MUST NEVER guess, make up, or hallucinate download URLs.
- If the user query does not provide a specific, exact download URL, you MUST find the download URL first using web tools:
  1. Use duckduckgo search to find the official website or download page (e.g. search "download debian stable iso link").
  2. Use fetch (fetch/fetch) or playwright (playwright_navigate and playwright_get_html) to view the page contents and identify the direct download link (anchors ending in .iso, .jar, .zip, .tar.gz, etc., or direct download buttons).
  3. Extract/copy the direct URL.
  4. Use execute_operational_command with curl or wget to download:
     {"server_name": "operations", "tool_name": "execute_operational_command", "arguments": {"command": "curl -L -o target_filename 'DIRECT_URL'", "timeout_seconds": 180}}
- Verify the file was downloaded with: {"server_name": "filesystem", "tool_name": "get_file_info", "arguments": {"path": "target_filename"}}
- NEVER rely only on fetch/scrape to download binary files. Always use wget/curl via execute_operational_command.

ACTION DETECTION AND PRIORITIZATION RULES:
- Detect action verbs in user query: create, install, download, setup, configure, deploy.
- If the query contains action verbs, the planner MUST generate direct, executable tasks, NOT advice or information-gathering/tutorial tasks.
- Prioritize using tools in this order:
  1. filesystem (e.g. read_file, write_file, list_directory, get_file_info)
  2. operations (with curl/wget to download)
  3. operations / desktop-commander (to execute terminal commands)
  4. docker (to manage containers)
  5. ssh (to run remote commands)
  6. duckduckgo / fetch / playwright (only if information/URL is missing)
- Web search must NOT be the first action for common tasks, EXCEPT when downloading a file and the exact, official download URL is unknown. In that case, perform a web search and fetch/playwright tasks first to find the real URL rather than guessing it.
- Do NOT output tutorials, advice, or guides on how the user can do it themselves. Output the exact tasks to execute it right now.

Example Output:
{
  "objective": "Download Debian ISO",
  "success_criteria": [
    "debian iso download link found",
    "iso file downloaded to workspace",
    "downloaded file verified"
  ],
  "tasks": [
    {
      "name": "search_debian_download_page",
      "kind": "mcp",
      "query": {"server_name": "duckduckgo", "tool_name": "search", "arguments": {"query": "debian stable netinst iso download official page"}},
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
    "desktop-commander": None,
    "docker": None,
    "ssh": None,
    "playwright": None,
}


async def build_plan(state: RagState) -> ExecutionPlan:
    from .mcp_client import mcp_manager
    from .tool_router import select_tools, format_tools_prompt
    from .agent_memory import get_agent_memory
    from .prompt_registry import registry as prompt_registry
    import json

    all_tools = await mcp_manager.get_all_tools()

    # ── Semantic tool selection: pre-filter to most relevant subset ─────────────
    if all_tools:
        selected_tools = await select_tools(state.user_input, all_tools, top_k=14)
        tools_prompt = format_tools_prompt(selected_tools)
        tools_prompt += (
            "\n\nTo call any tool above set kind='mcp' and format query as JSON:\n"
            '  {"server_name": "<server>", "tool_name": "<tool>", "arguments": {<args>}}'
        )
    else:
        tools_prompt = ""

    # ── Recall from embedding memory ────────────────────────────────────────────
    memory_context = ""
    try:
        memory_context = await get_agent_memory().recall(state.user_input)
    except Exception:
        pass

    # ── Build system prompt from registry (with fallback) ──────────────────────
    registry_prompt = prompt_registry.get("orchestrator_system")
    system_prompt = registry_prompt or ORCHESTRATOR_SYSTEM
    if tools_prompt:
        system_prompt += "\n\n" + tools_prompt

    lessons_block = get_lessons_memory().get_recent_lessons(n=5)
    if lessons_block:
        system_prompt += "\n\n" + lessons_block
    if memory_context:
        system_prompt += "\n\n" + memory_context

    client = OllamaClient()
    # Use async LLM compression for better context quality
    history = await compress_history_async(state.chat_history, use_llm=False)
    messages = build_messages(system_prompt, state.user_input, history)
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
            for idx, item in enumerate(parsed.get("tasks", [])[:SETTINGS.rag_plan_max_tasks], start=1)
            if isinstance(item, dict) and str(item.get("kind", "")).lower() != "retrieve"
        ]
        if not tasks:
            raise ValueError("No valid tasks parsed from LLM plan.")
        return ExecutionPlan(
            objective=str(parsed.get("objective", state.user_input)),
            tasks=tasks,
            response_style=str(parsed.get("response_style", "concise")),
            success_criteria=parsed.get("success_criteria", []),
        )
    except ValueError as exc:
        logging.warning("[build_plan] No valid tasks from LLM output: %s", exc)
    except json.JSONDecodeError as exc:
        logging.warning("[build_plan] LLM returned invalid JSON: %s", exc)
    except Exception as exc:
        import httpx as _httpx
        if isinstance(exc, (_httpx.ConnectError, _httpx.TimeoutException)):
            logging.warning("[build_plan] Ollama unreachable: %s", exc)
        else:
            logging.warning("[build_plan] Unexpected error: %s", exc)

    # Keyword-based fallback plan
    lower = state.user_input.lower()
    tasks = []
    if any(word in lower for word in ["code", "class", "function", "bug", "traceback", "repo"]):
        tasks.append(PlanTask(name="code_inspection", kind="code", query=state.user_input, priority=0))
    if any(word in lower for word in ["search", "news", "latest", "current", "today", "web", "internet"]):
        tasks.append(PlanTask(name="web_lookup", kind="web", query=state.user_input, priority=1))
    if any(word in lower for word in ["save", "download", "write"]):
        tasks.append(PlanTask(name="web_lookup", kind="web", query=state.user_input, priority=0))
        tasks.append(PlanTask(name="image_download", kind="download", query=state.user_input, priority=1))
    return ExecutionPlan(objective=state.user_input, tasks=tasks, response_style="concise", success_criteria=[])


async def verify_action(server_name: str, tool_name: str, arguments: dict[str, Any], result_str: str) -> tuple[bool, str]:
    """
    Verifies that the action had the desired effect:
    - Files were created and are non-empty.
    - Processes are running.
    - Expected output / port is listening.
    """
    import os
    import re
    from pathlib import Path
    from .mcp_client import mcp_manager
    from .config import WORKSPACE_DIR
    workspace_dir = str(WORKSPACE_DIR)

    # 1. filesystem/write_file verification
    if server_name == "filesystem" and tool_name == "write_file":
        path = arguments.get("path", "")
        if not path:
            return False, "Verification failed: path argument missing."
        if not os.path.isabs(path):
            path = os.path.abspath(os.path.join(workspace_dir, path))
        if not os.path.exists(path):
            return False, f"Verification failed: expected file '{path}' to exist, but it was not found."
        if os.path.getsize(path) == 0:
            return False, f"Verification failed: expected file '{path}' to be non-empty, but it is 0 bytes."
        return True, f"Verified: file '{path}' created successfully ({os.path.getsize(path)} bytes)."

    # 2. operations/execute_operational_command verification
    if server_name == "operations" and tool_name == "execute_operational_command":
        command = arguments.get("command", "")
        
        # Download verification (curl/wget)
        download_match = re.search(r'(?:wget\s+.*-O\s+|curl\s+.*-o\s+|>\s+)(\S+)', command)
        if download_match:
            filename = download_match.group(1).strip("'\"")
            run_dir = arguments.get("directory") or workspace_dir
            file_path = os.path.abspath(os.path.join(run_dir, filename))
            if not os.path.exists(file_path):
                file_path = os.path.abspath(os.path.join(workspace_dir, filename))
            
            if not os.path.exists(file_path):
                return False, f"Verification failed: download target file '{filename}' was not created."
            if os.stat(file_path).st_size == 0:
                return False, f"Verification failed: download target file '{filename}' is empty."
            return True, f"Verified: file '{filename}' downloaded successfully ({os.stat(file_path).st_size} bytes)."

        # Directory creation verification
        mkdir_match = re.search(r'mkdir\s+(?:-p\s+)?(\S+)', command)
        if mkdir_match:
            dirname = mkdir_match.group(1).strip("'\"")
            run_dir = arguments.get("directory") or workspace_dir
            dir_path = os.path.abspath(os.path.join(run_dir, dirname))
            if not os.path.exists(dir_path):
                dir_path = os.path.abspath(os.path.join(workspace_dir, dirname))
            if not os.path.exists(dir_path) or not os.path.isdir(dir_path):
                return False, f"Verification failed: directory '{dirname}' was not created."
            return True, f"Verified: directory '{dirname}' created successfully."

        # Server/process execution verification
        if "jar" in command or "server" in command or "java" in command:
            # Check EULA acceptance specifically if this command accepted it or set it up
            if "eula" in command and ("echo" in command or "write" in command or "sed" in command):
                eula_path = os.path.join(workspace_dir, "eula.txt")
                if not os.path.exists(eula_path):
                    for p in Path(workspace_dir).glob("**/eula.txt"):
                        eula_path = str(p)
                        break
                if os.path.exists(eula_path):
                    content = Path(eula_path).read_text(encoding="utf-8")
                    if "eula=true" in content.lower().replace(" ", ""):
                        return True, "Verified: eula.txt accepted."

            # Detect the listening port dynamically from the command
            # Supports: -p 1234, --port 1234, :1234 patterns
            port_match = re.search(r'(?:-p|--port)\s+(\d+)', command) or re.search(r':(\d{4,5})\b', command)
            if port_match:
                server_port = port_match.group(1)
            else:
                # Default to 25565 only for Minecraft/Fabric; otherwise skip port check
                is_minecraft = any(kw in command.lower() for kw in ["minecraft", "fabric", "spigot", "paper", "bukkit"])
                server_port = "25565" if is_minecraft else None

            # Give a brief sleep for processes to start up
            await asyncio.sleep(2)

            # Check process status on host via operations
            ps_check = await mcp_manager.call_tool("operations", "execute_operational_command", {"command": "ps aux | grep java | grep -v grep"})

            if server_port:
                lsof_check = await mcp_manager.call_tool("operations", "execute_operational_command", {"command": f"lsof -i :{server_port}"})
                if "LISTEN" in lsof_check:
                    return True, f"Verified: Server started successfully and is listening on port {server_port}."

            if "java" in ps_check:
                return True, "Verified: Java process is running on host."

            eula_path = os.path.join(workspace_dir, "eula.txt")
            if not os.path.exists(eula_path):
                for p in Path(workspace_dir).glob("**/eula.txt"):
                    eula_path = str(p)
                    break
            if os.path.exists(eula_path):
                content = Path(eula_path).read_text(encoding="utf-8")
                if "eula=true" not in content.lower().replace(" ", ""):
                    return False, "Verification failed: Server failed to start because eula.txt has not been accepted."

            port_msg = f" on port {server_port}" if server_port else ""
            return False, f"Verification failed: java server process not detected{port_msg}. Check output: {ps_check}"

    return True, "Verification skipped or passed by default."


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
            try:
                params = _json.loads(task.query)
            except Exception:
                params = {"server_name": "duckduckgo", "tool_name": "search", "arguments": {"query": task.query}}

            server_name = params.get("server_name", "")
            tool_name   = params.get("tool_name", "")
            arguments   = params.get("arguments", {})

            if not server_name or not tool_name:
                return WorkerResult(task_name=task.name, kind=task.kind, success=False,
                                    summary="MCP task missing server_name or tool_name.")

            result_str = await mcp_manager.call_tool(server_name, tool_name, arguments)
            
            # Run verification layer
            verified, ver_msg = await verify_action(server_name, tool_name, arguments, result_str)
            if not verified:
                result_str = f"{result_str}\n\n[Verification Error] {ver_msg}"

            return WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success="Error" not in result_str and verified,
                summary=result_str,
                artifacts=[{"server_name": server_name, "tool_name": tool_name,
                            "arguments": arguments, "result": result_str}],
            )
        except Exception as exc:
            return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary=str(exc))

    # ── legacy local kinds → redirect to MCP ─────────────────────────────────
    from .mcp_client import mcp_manager
    fallback = _LOCAL_KIND_TO_MCP.get(task.kind)
    if fallback and mcp_manager.sessions:
        server_name = fallback["server_name"]
        tool_name   = fallback["tool_name"]
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
        elif task.kind == "write":
            arguments = {"path": "output.txt", "content": task.query}
        elif task.kind == "download":
            # Route download through curl via operations, not filesystem/write_file
            server_name = "operations"
            tool_name = "execute_operational_command"
            # Extract a URL from the query if present, otherwise use query as-is
            url_match = re.search(r"https?://\S+", task.query)
            url = url_match.group(0) if url_match else task.query
            filename = url.rstrip("/").split("/")[-1] or "downloaded_file"
            arguments = {
                "command": f"curl -L -o '{filename}' '{url}'",
                "timeout_seconds": int(SETTINGS.mcp_ops_timeout),
            }
        else:
            arguments = {"query": task.query}

        result_str = await mcp_manager.call_tool(server_name, tool_name, arguments)
        
        # Run verification layer
        verified, ver_msg = await verify_action(server_name, tool_name, arguments, result_str)
        if not verified:
            result_str = f"{result_str}\n\n[Verification Error] {ver_msg}"

        return WorkerResult(
            task_name=task.name,
            kind="mcp",
            success="Error" not in result_str and verified,
            summary=result_str,
            artifacts=[{"redirected_from": task.kind, "server_name": server_name,
                        "tool_name": tool_name, "result": result_str}],
        )

    return WorkerResult(task_name=task.name, kind=task.kind, success=False,
                        summary=f"Unknown task kind '{task.kind}'. Only 'mcp' and 'retrieve' are supported.")


async def run_parallel_tasks(plan: ExecutionPlan, state: RagState) -> list[WorkerResult]:
    """Execute plan using the dedicated executor (parallel + self-healing)."""
    from .executor import run_tasks
    from .agent_memory import get_agent_memory

    results = await run_tasks(plan, state)

    # Store outcomes in embedding memory for future runs
    memory = get_agent_memory()
    for task, res in zip(
        sorted(plan.tasks, key=lambda t: t.priority),
        results,
    ):
        try:
            tool_call = task.query if isinstance(task.query, str) else str(task.query)
            await memory.store_task_outcome(
                query=state.user_input,
                task_name=task.name,
                tool_call=tool_call[:120],
                success=res.success,
                summary=res.summary[:200] if res.summary else "",
            )
        except Exception:
            pass  # Never let memory writes break execution

    return results



SYNTHESIZER_SYSTEM = """You are the final synthesizer for a local RAG system.
Use only the provided worker results and retrieved context.
Answer clearly, call out uncertainty, and keep the response practical.

BEHAVIOR RULES FOR ACTIONS:
- If the user requested to "create", "download", "install", "setup", "configure", or "deploy", and the plan executed tool actions, DO NOT provide tutorials, instructions, or advice on how the user can do it manually. Instead, summarize what was executed, show the outputs, paths, logs, and report success/failure.
- Only return tutorials if the user explicitly asked "how to do..." or requested instructions.

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
