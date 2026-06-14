from __future__ import annotations

import asyncio
from typing import Annotated, Any, TypedDict, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .config import SETTINGS
from .embed import OllamaClient, build_messages
from .orchestrator import build_plan, run_parallel_tasks, synthesize
from .router import route_query
from rag_local.tools.retrieval.tool import hybrid_retrieve
from .types import ExecutionPlan, RagState, RouteDecision, SearchHit, WorkerResult
from .utils import safe_json_loads


class GraphState(TypedDict, total=False):
    user_input: str
    route: str
    route_reason: str
    plan: ExecutionPlan
    retrieved_chunks: list[SearchHit]
    code_results: list[WorkerResult]
    web_results: list[WorkerResult]
    general_answer: str
    final_answer: str
    chat_history: list[dict[str, str]]
    diagnostics: list[str]
    clarification_prompt: dict[str, Any]
    clarification_response: str


async def router_node(state: GraphState) -> dict[str, Any]:
    from .mcp_client import mcp_manager
    import atexit
    
    # Skip lazy-init if background startup is already in progress or done
    if not mcp_manager.sessions and not getattr(mcp_manager, "_started", False):
        mcp_manager._started = True
        await mcp_manager.start_all()

        # Register atexit handler to ensure subprocesses are cleaned up on exit
        def cleanup_mcp():
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(mcp_manager.stop_all())
                else:
                    loop.run_until_complete(mcp_manager.stop_all())
            except Exception:
                try:
                    asyncio.run(mcp_manager.stop_all())
                except Exception:
                    pass
        atexit.register(cleanup_mcp)

    decision = await route_query(state["user_input"], state.get("chat_history"))
    route = decision.decision.route
    reason = decision.decision.reason

    if route != "general" and not state.get("clarification_response"):
        from pathlib import Path
        from .config import WORKSPACE_DIR
        workspace = str(WORKSPACE_DIR)
        downloads = str(Path("~/Downloads").expanduser().resolve())
        
        system_prompt = (
            "You are a smart clarification agent for a local AI assistant (called Nexus).\n"
            "Your job: read the user's query and decide if any critical parameter is MISSING or AMBIGUOUS.\n\n"
            "Ambiguity examples that REQUIRE clarification (clarification_needed = true):\n"
            "- Save/download tasks: WHERE to save (path, directory)\n"
            "- Code analysis: WHICH file, module, class, or function to inspect\n"
            "- Search tasks: HOW specific (broad overview vs deep details)\n"
            "- Image tasks: WHAT format or quality (jpg vs png, low res vs high res)\n"
            "- Refactoring/editing: WHAT scope (single file vs whole project)\n"
            "- Time-based queries: WHAT time range (last week vs last month vs all time)\n"
            "- Multi-step tasks: WHAT priority (do A first, or B first?)\n"
            "- Output format: SHORT summary vs DETAILED report vs RAW data\n"
            "- Ambiguous subject: 'the code' (which file?), 'the image' (from where?), 'my drive' (which folder?)\n\n"
            "Clear queries that do NOT need clarification (clarification_needed = false):\n"
            "- Simple greetings, casual questions\n"
            "- Queries with all required details already specified\n"
            "- Basic factual lookups\n\n"
            "When clarification IS needed, construct:\n"
            "1. `question`: One concise question about the most critical missing detail.\n"
            "2. `options`: Exactly 2 context-aware choices as human-readable labels (embed the real value in the label if applicable, e.g. 'Active Workspace: /path/to/dir').\n"
            "3. `paths`: Exactly 2 selectable values (absolute paths, enum values, or descriptive strings) corresponding 1:1 with options.\n"
            "4. `default_index`: 0 (always recommend option 1).\n\n"
            "CRITICAL PATH RULE: If you are asking the user where to save a file, download an image, or write search results, you MUST put the Active Workspace as the first choice (options[0] and paths[0]) and the Downloads folder as the second choice (options[1] and paths[1]). This ensures the Active Workspace is always the default/recommended path.\n\n"
            f"Context:\n"
            f"- Active Workspace: {workspace}\n"
            f"- Downloads folder: {downloads}\n\n"
            "IMPORTANT: paths[0] must correspond to options[0], paths[1] to options[1]. Never use placeholder text like 'value1'.\n"
            "Return ONLY a valid JSON object (no markdown wrapping):\n"
            "{\n"
            "  \"clarification_needed\": true,\n"
            "  \"question\": \"Question text\",\n"
            "  \"options\": [\"Label for choice 1 (value1)\", \"Label for choice 2 (value2)\"],\n"
            "  \"paths\": [\"/real/value/1\", \"/real/value/2\"],\n"
            "  \"default_index\": 0\n"
            "}"
        )
        try:
            client = OllamaClient()
            raw = await client.chat(
                SETTINGS.ollama_router_model,
                build_messages(system_prompt, state["user_input"]),
                temperature=0.0,
                keep_alive=SETTINGS.rag_keep_alive,
                format="json",
            )
            parsed = safe_json_loads(raw)
            if isinstance(parsed, dict) and parsed.get("clarification_needed"):
                import re as _re
                raw_paths = list(parsed.get("paths", []))
                options = list(parsed.get("options", []))
                fallback_paths = [workspace, downloads]
                sanitized_paths = []
                for i, opt_text in enumerate(options):
                    opt_lower = opt_text.lower()
                    workspace_name = Path(workspace).name.lower()
                    # Force matching if the option explicitly refers to the workspace or downloads folder
                    if "workspace" in opt_lower or (workspace_name and workspace_name in opt_lower):
                        sanitized_paths.append(workspace)
                        continue
                    if "downloads" in opt_lower:
                        sanitized_paths.append(downloads)
                        continue

                    # Get the LLM-provided path/value for this option
                    llm_val = str(raw_paths[i]).strip() if i < len(raw_paths) else ""

                    # Case A: LLM gave a real filesystem path → use it directly
                    if llm_val.startswith("/") or llm_val.startswith("~"):
                        sanitized_paths.append(llm_val)
                        continue

                    # Case B: LLM gave a short keyword value (format, scope, etc.)
                    # e.g. 'jpg', 'png', 'summary', 'detailed', 'whole project'
                    # Trust it as-is if it's non-empty and not a placeholder like 'value1'
                    is_placeholder = _re.match(r'^value\d*$', llm_val, _re.IGNORECASE)
                    if llm_val and not is_placeholder:
                        sanitized_paths.append(llm_val)
                        continue

                    # Case C: Placeholder or empty — try to extract a /path from option label
                    match = _re.search(r"(/[^\s,;\"']+)", opt_text)
                    if match:
                        sanitized_paths.append(match.group(1))
                        continue

                    # Case D: Last resort — context paths
                    sanitized_paths.append(fallback_paths[i] if i < len(fallback_paths) else workspace)

                prompt = {
                    "question": str(parsed.get("question", "Clarification needed")),
                    "options": options,
                    "paths": sanitized_paths,
                    "default_index": int(parsed.get("default_index", 0)),
                }
                return {"route": "clarification", "clarification_prompt": prompt, "route_reason": reason}
        except Exception:
            pass

    return {"route": route, "route_reason": reason}


async def general_node(state: GraphState) -> dict[str, Any]:
    return {
        "general_answer": "This query does not require local retrieval. Ask for repo analysis, document lookup, or web search when needed.",
    }


async def planning_node(state: GraphState) -> dict[str, Any]:
    plan = await build_plan(RagState(**state))
    return {"plan": plan}


async def retrieval_node(state: GraphState) -> dict[str, Any]:
    retrieval = await hybrid_retrieve(state["user_input"])
    return {"retrieved_chunks": retrieval.hits}


async def execute_workers_node(state: GraphState) -> dict[str, Any]:
    plan = state.get("plan")
    if not plan:
        return {}
    results = await run_parallel_tasks(plan, RagState(**state))
    code_results = [r for r in results if r.kind in {"code", "git", "download", "mcp", "write"}]
    web_results = [r for r in results if r.kind in {"web", "scrape"}]
    retrieved = state.get("retrieved_chunks", [])
    return {"code_results": code_results, "web_results": web_results, "retrieved_chunks": retrieved}


async def synthesize_node(state: GraphState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    prompt = state.get("clarification_prompt")
    if prompt:
        # CLI REPL loop handles rendering the question — just propagate the prompt.
        # Do NOT emit final_answer text here or it will render twice.
        return {"clarification_prompt": prompt}
    answer = await synthesize(RagState(**state), config)
    return {"final_answer": answer}


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("router", router_node)
    graph.add_node("general", general_node)
    graph.add_node("plan", planning_node)
    graph.add_node("retrieve", retrieval_node)
    graph.add_node("workers", execute_workers_node)
    graph.add_node("synthesize", synthesize_node)

    def route_selector(state: GraphState) -> str:
        return state.get("route", "general")

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        route_selector,
        {
            "general": "general",
            "rag": "plan",
            "code_analysis": "plan",
            "web_search": "plan",
            "clarification": "synthesize",
        },
    )
    graph.add_edge("general", "synthesize")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "workers")
    graph.add_edge("workers", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()


APP = build_graph()


async def ask(question: str, history: list[dict[str, str]] | None = None) -> GraphState:
    result = await APP.ainvoke({"user_input": question, "chat_history": history or []})
    return result

