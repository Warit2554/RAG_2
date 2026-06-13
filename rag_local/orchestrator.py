from __future__ import annotations

import asyncio
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


ORCHESTRATOR_SYSTEM = """You are a local RAG orchestrator.
Create a short JSON plan with keys objective, tasks, response_style.
Each task must have name, kind, query, priority.
Kinds: retrieve, code, web, scrape, git, download.
Use:
- retrieve: for local indexed documents.
- code: for custom script executions / code analysis.
- web: for fresh external web search engine lookups.
- scrape: to scrape the full body text of a specific URL.
- git: to check repository history, status, commits, or diffs.
- download: to download a picture or file from a URL.
"""


def _normalize_kind(kind: str, query: str) -> str:
    value = kind.strip().lower()
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
    lower_query = query.lower().strip()
    if lower_query.startswith(("http://", "https://")):
        return "scrape"
    if any(word in lower_query for word in ["git status", "git diff", "git log", "git show", "git branch"]):
        return "git"
    if any(word in lower_query for word in ["download", "save", "write"]):
        return "download"
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
            format="json",
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
        if any(word in lower for word in ["save", "download", "write"]):
            tasks.append(PlanTask(name="web_lookup", kind="web", query=state.user_input, priority=0))
            tasks.append(PlanTask(name="image_download", kind="download", query=state.user_input, priority=1))
        return ExecutionPlan(objective=state.user_input, tasks=tasks, response_style="concise")


async def execute_task(task: PlanTask, state: RagState | None = None) -> WorkerResult:
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
        import re
        client = OllamaClient()
        coder_system = (
            "You are a helpful software engineering assistant.\n"
            "Generate standard Python 3 code that executes in a sandbox to answer the following request.\n"
            "The repository files are mounted read-only at `/repo`. Your script should look at `/repo` to analyze the code.\n"
            "Your output must contain ONLY the raw python code inside a single markdown code block (e.g. ```python ... ```). Do not include any explanation before or after the code block."
        )
        try:
            raw_code = await client.chat(
                SETTINGS.ollama_chat_model,
                build_messages(coder_system, task.query),
                temperature=0.1,
                keep_alive=SETTINGS.rag_keep_alive,
            )
            match = re.search(r"```python\s*(.*?)\s*```", raw_code, re.DOTALL)
            if not match:
                match = re.search(r"```\s*(.*?)\s*```", raw_code, re.DOTALL)
            code_to_run = match.group(1) if match else raw_code.strip()
            
            sandboxed = run_python_in_docker(
                code_to_run,
                timeout_seconds=15,
            )
            return WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success=sandboxed.success,
                summary=sandboxed.stdout.strip() or sandboxed.stderr.strip() or "Code tool executed.",
                artifacts=[{"code_run": code_to_run, "stdout": sandboxed.stdout, "stderr": sandboxed.stderr, "exit_code": sandboxed.exit_code}],
            )
        except Exception as exc:
            return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary=str(exc))
    if task.kind == "scrape":
        try:
            import re
            url_match = re.search(r"https?://\S+", task.query)
            url = url_match.group(0) if url_match else task.query.strip()
            content = await scrape_url(url)
            return WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success="Error scraping URL" not in content,
                summary=content[:1500] + ("..." if len(content) > 1500 else ""),
                artifacts=[{"url": url, "content": content}],
            )
        except Exception as exc:
            return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary=str(exc))
    if task.kind == "git":
        try:
            parts = task.query.strip().split()
            if parts and parts[0].lower() == "git":
                git_args = parts[1:]
            else:
                git_args = parts
            if not git_args:
                git_args = ["status"]
            res = run_git_command(git_args)
            return WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success="Disallowed" not in res and "Unauthorized" not in res and "Git error" not in res,
                summary=res,
                artifacts=[{"command": f"git {' '.join(git_args)}", "output": res}],
            )
        except Exception as exc:
            return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary=str(exc))
    if task.kind == "download":
        try:
            save_dir = str(SETTINGS.rag_data_dir.resolve())
            if state and state.clarification_response:
                save_dir = state.clarification_response
            url = None
            import re
            # If the query contains a direct image URL, use it
            url_match = re.search(r"https?://\S+\.(?:jpg|jpeg|png|webp|gif|bmp)", task.query, re.IGNORECASE)
            if url_match:
                url = url_match.group(0)
            # Otherwise use LLM to extract the real search subject noun, then find an image
            if not url:
                search_topic = task.query
                try:
                    client = OllamaClient()
                    extraction_prompt = (
                        "Extract ONLY the main subject noun phrase the user wants an image of.\n"
                        "Return just the noun phrase, nothing else. Examples:\n"
                        "  'search for a dog picture and save it to my drive' → 'dog'\n"
                        "  'find me a photo of a golden retriever' → 'golden retriever'\n"
                        "  'download a picture of the Eiffel Tower' → 'Eiffel Tower'\n"
                        "  'get me a cat image' → 'cat'\n"
                        f"Query: {task.query}"
                    )
                    extracted = await client.chat(
                        SETTINGS.ollama_router_model,
                        [{"role": "user", "content": extraction_prompt}],
                        temperature=0.0,
                        keep_alive=SETTINGS.rag_keep_alive,
                    )
                    extracted = extracted.strip().strip('"').strip("'")
                    if extracted and len(extracted) < 60:
                        search_topic = extracted
                except Exception:
                    # Fallback: strip common filler words with regex
                    search_topic = re.sub(
                        r"\b(search for|find|get|download|save|a|an|the|picture|photo|image|file|to my drive|to my computer|and|me|please)\b",
                        "", task.query, flags=re.IGNORECASE
                    ).strip() or task.query
                url = await search_for_image_url(search_topic)
            if not url:
                return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary="Could not find a direct image URL to download.")
            res = await download_image(url, save_dir)
            return WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success="Success" in res,
                summary=res,
                artifacts=[{"source_url": url, "save_dir": save_dir, "result": res}],
            )
        except Exception as exc:
            return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary=str(exc))
    return WorkerResult(task_name=task.name, kind=task.kind, success=False, summary="Unknown task kind.")


async def run_parallel_tasks(plan: ExecutionPlan, state: RagState) -> list[WorkerResult]:
    ordered = sorted(plan.tasks, key=lambda item: item.priority)
    return await asyncio.gather(*(execute_task(task, state) for task in ordered))


SYNTHESIZER_SYSTEM = """You are the final synthesizer for a local RAG system.
Use only the provided worker results and retrieved context.
Answer clearly, call out uncertainty, and keep the response practical.
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
            content_lines.append(f"- {result.task_name}: {result.summary}")
    if state.web_results:
        content_lines.append("Web results:")
        for result in state.web_results:
            content_lines.append(f"- {result.task_name}: {result.summary}")
    if state.general_answer:
        content_lines.append(f"General answer: {state.general_answer}")
    messages = build_messages(SYNTHESIZER_SYSTEM, "\n".join(content_lines), state.chat_history)
    
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
