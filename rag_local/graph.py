from __future__ import annotations

import asyncio
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .orchestrator import build_plan, run_parallel_tasks, synthesize
from .router import route_query
from .search import hybrid_retrieve
from .types import ExecutionPlan, RagState, RouteDecision, SearchHit, WorkerResult


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


async def router_node(state: GraphState) -> dict[str, Any]:
    decision = await route_query(state["user_input"], state.get("chat_history"))
    return {"route": decision.decision.route, "route_reason": decision.decision.reason}


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
    results = await run_parallel_tasks(plan)
    code_results = [r for r in results if r.kind == "code"]
    web_results = [r for r in results if r.kind == "web"]
    retrieved = state.get("retrieved_chunks", [])
    return {"code_results": code_results, "web_results": web_results, "retrieved_chunks": retrieved}


async def synthesize_node(state: GraphState, config: Any = None) -> dict[str, Any]:
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

