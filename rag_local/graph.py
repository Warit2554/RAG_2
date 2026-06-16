"""LangGraph workflow definition for Nexus.

Node flow:
  router → [general | plan] → [retrieve?] → workers → verify → synthesize → END

New nodes vs. original:
- memory_recall: Called before planning to inject embedding memory context.
- verify: Runs confidence scoring after workers complete; low-confidence runs
  are flagged in the state so the synthesizer can warn the user.
"""
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
from .types import (
    ArtifactRecord,
    ConfidenceScore,
    ExecutionMetrics,
    ExecutionPlan,
    RagState,
    RouteDecision,
    SearchHit,
    WorkerResult,
)
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
    # New fields
    metrics: ExecutionMetrics
    artifacts: list[ArtifactRecord]
    memory_context: str
    confidence: ConfidenceScore
    shared_memory: dict[str, Any]


# ---------------------------------------------------------------------------
# Router node
# ---------------------------------------------------------------------------

async def router_node(state: GraphState) -> dict[str, Any]:
    from .mcp_client import mcp_manager
    import atexit

    # Skip lazy-init if background startup is already in progress or done
    if not mcp_manager.sessions and not getattr(mcp_manager, "_started", False):
        mcp_manager._started = True
        await mcp_manager.start_all()

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
            "2. `options`: Exactly 2 context-aware choices as human-readable labels.\n"
            "3. `paths`: Exactly 2 selectable values corresponding 1:1 with options.\n"
            "4. `default_index`: 0 (always recommend option 1).\n\n"
            "CRITICAL PATH RULE: If asking where to save a file, put the Active Workspace first.\n\n"
            f"Context:\n"
            f"- Active Workspace: {workspace}\n"
            f"- Downloads folder: {downloads}\n\n"
            "Return ONLY a valid JSON object (no markdown wrapping):\n"
            "{\n"
            "  \"clarification_needed\": true,\n"
            "  \"question\": \"Question text\",\n"
            "  \"options\": [\"Label for choice 1\", \"Label for choice 2\"],\n"
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
                    if "workspace" in opt_lower or (workspace_name and workspace_name in opt_lower):
                        sanitized_paths.append(workspace)
                        continue
                    if "downloads" in opt_lower:
                        sanitized_paths.append(downloads)
                        continue
                    llm_val = str(raw_paths[i]).strip() if i < len(raw_paths) else ""
                    if llm_val.startswith("/") or llm_val.startswith("~"):
                        sanitized_paths.append(llm_val)
                        continue
                    is_placeholder = _re.match(r'^value\d*$', llm_val, _re.IGNORECASE)
                    if llm_val and not is_placeholder:
                        sanitized_paths.append(llm_val)
                        continue
                    match = _re.search(r"(/[^\s,;\"']+)", opt_text)
                    if match:
                        sanitized_paths.append(match.group(1))
                        continue
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


# ---------------------------------------------------------------------------
# General node
# ---------------------------------------------------------------------------

async def general_node(state: GraphState) -> dict[str, Any]:
    try:
        client = OllamaClient()
        messages = build_messages(
            "You are a helpful and concise assistant. Answer the user's question directly.",
            state["user_input"],
            state.get("chat_history")
        )
        answer = await client.chat(
            SETTINGS.ollama_chat_model,
            messages,
            temperature=0.7,
            keep_alive=SETTINGS.rag_keep_alive,
        )
        return {
            "general_answer": answer,
        }
    except Exception as e:
        return {
            "general_answer": f"Error generating answer: {e}",
        }


# ---------------------------------------------------------------------------
# Memory recall node (before planning)
# ---------------------------------------------------------------------------

async def memory_recall_node(state: GraphState) -> dict[str, Any]:
    """Retrieve relevant embedding memories and attach to state."""
    if not SETTINGS.agent_memory_enabled:
        return {}
    try:
        from .agent_memory import get_agent_memory
        memory_context = await get_agent_memory().recall(state["user_input"])
        return {"memory_context": memory_context}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Planning node
# ---------------------------------------------------------------------------

async def planning_node(state: GraphState) -> dict[str, Any]:
    # Initialize shared memory if missing
    shared_memory = state.get("shared_memory")
    if not shared_memory:
        shared_memory = {
            "blackboard": ["[Planner] Project started. Planning high-level steps..."],
            "research_context": [],
            "coder_context": [],
            "qa_results": [],
            "iteration": 0
        }
    
    rag_state = RagState(**{
        k: v for k, v in state.items()
        if k in RagState.model_fields and k != "shared_memory"
    })
    from rag_local.types import SharedMemoryState
    rag_state.shared_memory = SharedMemoryState(**shared_memory)

    plan = await build_plan(rag_state)
    
    # Update blackboard
    shared_memory["blackboard"].append(f"[Planner] Iteration {shared_memory.get('iteration', 0)}. Created plan with {len(plan.tasks)} tasks. Objective: {plan.objective}")
    
    return {"plan": plan, "shared_memory": shared_memory}


# ---------------------------------------------------------------------------
# Retrieval node
# ---------------------------------------------------------------------------

async def retrieval_node(state: GraphState) -> dict[str, Any]:
    retrieval = await hybrid_retrieve(state["user_input"])
    from .tools.retrieval.tool import compress_context_with_embeddings
    compressed_hits = await compress_context_with_embeddings(state["user_input"], retrieval.hits)
    return {"retrieved_chunks": compressed_hits}


# ---------------------------------------------------------------------------
# Researcher node
# ---------------------------------------------------------------------------

async def researcher_node(state: GraphState) -> dict[str, Any]:
    plan = state.get("plan")
    shared_memory = state.get("shared_memory") or {}
    if not plan:
        return {}
        
    researcher_tasks = [t for t in plan.tasks if t.assigned_agent == "researcher"]
    if not researcher_tasks:
        shared_memory.setdefault("blackboard", []).append("[Researcher] No research tasks assigned.")
        return {"shared_memory": shared_memory}
        
    shared_memory.setdefault("blackboard", []).append("[Researcher] Beginning research tasks...")
    
    from .orchestrator import execute_task
    results = []
    for t in sorted(researcher_tasks, key=lambda x: x.priority):
        shared_memory["blackboard"].append(f"[Researcher] Running task '{t.name}': {t.query}")
        res = await execute_task(t, state=RagState(**{k: v for k, v in state.items() if k in RagState.model_fields and k != "shared_memory"}))
        results.append(res)
        if res.success:
            summary = res.summary or ""
            if len(summary) > 500:
                summary = summary[:500] + "..."
            shared_memory["blackboard"].append(f"[Researcher] Completed task '{t.name}' successfully.")
            shared_memory.setdefault("research_context", []).append(f"Task '{t.name}' query: {t.query}\nFindings: {summary}")
        else:
            shared_memory["blackboard"].append(f"[Researcher] Task '{t.name}' failed: {res.summary}")
            
    # Merge existing web_results / code_results with new ones
    existing_web = state.get("web_results") or []
    new_web = [r for r in results if r.kind in {"web", "scrape"}]
    existing_code = state.get("code_results") or []
    new_code = [r for r in results if r.kind in {"code", "git", "download", "mcp", "write"}]
    
    return {
        "web_results": list(existing_web) + new_web,
        "code_results": list(existing_code) + new_code,
        "shared_memory": shared_memory
    }


# ---------------------------------------------------------------------------
# Coder node
# ---------------------------------------------------------------------------

async def coder_node(state: GraphState) -> dict[str, Any]:
    plan = state.get("plan")
    shared_memory = state.get("shared_memory") or {}
    if not plan:
        return {}
        
    coder_tasks = [t for t in plan.tasks if t.assigned_agent == "coder"]
    if not coder_tasks:
        shared_memory.setdefault("blackboard", []).append("[Coder] No implementation tasks assigned.")
        return {"shared_memory": shared_memory}
        
    shared_memory.setdefault("blackboard", []).append("[Coder] Beginning implementation/coding tasks...")
    
    from .orchestrator import execute_task
    results = []
    
    # Pack shared memory into state for context awareness
    from rag_local.types import SharedMemoryState
    rag_state = RagState(**{k: v for k, v in state.items() if k in RagState.model_fields and k != "shared_memory"})
    rag_state.shared_memory = SharedMemoryState(**shared_memory)

    for t in sorted(coder_tasks, key=lambda x: x.priority):
        shared_memory["blackboard"].append(f"[Coder] Running task '{t.name}': {t.query}")
        res = await execute_task(t, state=rag_state)
        results.append(res)
        if res.success:
            summary = res.summary or ""
            if len(summary) > 500:
                summary = summary[:500] + "..."
            shared_memory["blackboard"].append(f"[Coder] Completed task '{t.name}' successfully.")
            shared_memory.setdefault("coder_context", []).append(f"Task '{t.name}' executed.\nOutput: {summary}")
        else:
            shared_memory["blackboard"].append(f"[Coder] Task '{t.name}' failed: {res.summary}")
            
    # Merge existing web_results / code_results with new ones
    existing_web = state.get("web_results") or []
    new_web = [r for r in results if r.kind in {"web", "scrape"}]
    existing_code = state.get("code_results") or []
    new_code = [r for r in results if r.kind in {"code", "git", "download", "mcp", "write"}]
    
    # Collect artifacts from results
    artifacts: list[ArtifactRecord] = list(state.get("artifacts") or [])
    from datetime import datetime, timezone
    for res in results:
        for art in res.artifacts:
            if isinstance(art, dict):
                path = art.get("path", "") or art.get("arguments", {}).get("path", "")
                if path:
                    artifacts.append(ArtifactRecord(
                        path=path,
                        task_name=res.task_name,
                        created_at=datetime.now(timezone.utc).isoformat(),
                        verified=res.success,
                    ))

    return {
        "web_results": list(existing_web) + new_web,
        "code_results": list(existing_code) + new_code,
        "shared_memory": shared_memory,
        "artifacts": artifacts
    }


# ---------------------------------------------------------------------------
# QA node
# ---------------------------------------------------------------------------

async def qa_node(state: GraphState) -> dict[str, Any]:
    plan = state.get("plan")
    shared_memory = state.get("shared_memory") or {}
    if not plan:
        return {}
        
    shared_memory.setdefault("blackboard", []).append("[QA] Starting verification and validation checks...")
    
    all_results = list(state.get("code_results") or []) + list(state.get("web_results") or [])
    
    from .executor import VerificationAgent
    qa_reports = []
    has_failures = False
    
    for t in plan.tasks:
        result = next((r for r in all_results if r.task_name == t.name), None)
        if not result:
            continue
        
        for rule in t.verification_rules:
            passed, msg = await VerificationAgent.check(rule, result.summary)
            if passed:
                qa_reports.append(f"[PASS] Task '{t.name}' rule '{rule}': {msg}")
            else:
                qa_reports.append(f"[FAIL] Task '{t.name}' rule '{rule}': {msg}")
                has_failures = True
                result.success = False
                
        if not result.success:
            qa_reports.append(f"[FAIL] Task '{t.name}' execution failed: {result.summary[:200]}")
            has_failures = True
            
    if not qa_reports:
        for res in all_results:
            if not res.success:
                qa_reports.append(f"[FAIL] Task '{res.task_name}' failed.")
                has_failures = True
            else:
                qa_reports.append(f"[PASS] Task '{res.task_name}' succeeded.")
                
    confidence = None
    try:
        from .executor import score_confidence
        confidence = await score_confidence(plan, all_results)
    except Exception:
        pass
        
    shared_memory["qa_results"] = qa_reports
    if has_failures:
        shared_memory["blackboard"].append(f"[QA] Validation FAILED with errors. Routing back to Planner for self-healing.")
    else:
        shared_memory["blackboard"].append(f"[QA] Validation PASSED. All verification rules satisfied.")
        
    res_dict = {
        "shared_memory": shared_memory
    }
    if confidence:
        res_dict["confidence"] = confidence
    return res_dict


# ---------------------------------------------------------------------------
# Synthesize node
# ---------------------------------------------------------------------------

async def synthesize_node(state: GraphState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    prompt = state.get("clarification_prompt")
    if prompt:
        return {"clarification_prompt": prompt}
    
    # Map GraphState to RagState
    rag_state = RagState(**{
        k: v for k, v in state.items()
        if k in RagState.model_fields and k != "shared_memory"
    })
    
    # Initialize / map shared memory
    shared_memory = state.get("shared_memory")
    if shared_memory:
        from rag_local.types import SharedMemoryState
        rag_state.shared_memory = SharedMemoryState(**shared_memory)
        
    answer = await synthesize(rag_state, config)
    return {"final_answer": answer}


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _retrieval_router(state: GraphState) -> str:
    """Skip Qdrant retrieval for web_search routes with no retrieve tasks."""
    if state.get("route") == "web_search":
        plan = state.get("plan")
        if plan is None:
            return "workers"
        has_retrieve_task = any(
            getattr(t, "kind", "") == "retrieve" for t in (plan.tasks or [])
        )
        if not has_retrieve_task:
            return "workers"
    return "retrieve"


def _retrieval_agent_router(state: GraphState) -> str:
    """Route to retrieve node first if needed, otherwise straight to researcher agent."""
    dest = _retrieval_router(state)
    return "retrieve" if dest == "retrieve" else "researcher"


def _qa_router(state: GraphState) -> str:
    """Decide whether to loop back to plan (re-plan) or proceed to synthesize."""
    shared_memory = state.get("shared_memory")
    if not shared_memory:
        return "synthesize"
        
    iteration = shared_memory.get("iteration", 0)
    if iteration >= 2: # Max 2 retries (3 iterations total)
        return "synthesize"
        
    qa_results = shared_memory.get("qa_results", [])
    has_failures = any("[fail]" in r.lower() or "fail" in r.lower() for r in qa_results)
    
    if has_failures:
        # Increment iteration count
        shared_memory["iteration"] = iteration + 1
        return "plan"
        
    return "synthesize"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("router", router_node)
    graph.add_node("general", general_node)
    graph.add_node("memory_recall", memory_recall_node)
    graph.add_node("plan", planning_node)
    graph.add_node("retrieve", retrieval_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("coder", coder_node)
    graph.add_node("qa", qa_node)
    graph.add_node("synthesize", synthesize_node)

    def route_selector(state: GraphState) -> str:
        return state.get("route", "general")

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        route_selector,
        {
            "general": "general",
            "rag": "memory_recall",
            "code_analysis": "memory_recall",
            "web_search": "memory_recall",
            "clarification": "synthesize",
        },
    )
    graph.add_edge("general", "synthesize")
    graph.add_edge("memory_recall", "plan")
    
    # Conditional edge routing to retrieval or straight to researcher agent
    graph.add_conditional_edges(
        "plan",
        _retrieval_agent_router,
        {
            "retrieve": "retrieve",
            "researcher": "researcher",
        },
    )
    graph.add_edge("retrieve", "researcher")
    graph.add_edge("researcher", "coder")
    graph.add_edge("coder", "qa")
    
    # Self-healing loop back to plan if QA fails
    graph.add_conditional_edges(
        "qa",
        _qa_router,
        {
            "plan": "plan",
            "synthesize": "synthesize",
        },
    )
    graph.add_edge("synthesize", END)
    return graph.compile()


APP = build_graph()


async def ask(question: str, history: list[dict[str, str]] | None = None) -> GraphState:
    result = await APP.ainvoke({"user_input": question, "chat_history": history or []})
    return result
