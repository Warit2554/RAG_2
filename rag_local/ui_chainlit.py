from __future__ import annotations

import chainlit as cl

from rag_local.config import SETTINGS
from rag_local.embed import OllamaClient, build_messages
from rag_local.graph import ask
from rag_local.memory import compress_history
from rag_local.orchestrator import SYNTHESIZER_SYSTEM, build_plan, run_parallel_tasks
from rag_local.router import route_query
from rag_local.search import hybrid_retrieve
from rag_local.types import RagState


def _synthesis_prompt(state: RagState) -> str:
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
    return "\n".join(content_lines)


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("history", [])
    await cl.Message(content="Local RAG ready. Ask about indexed files, code, or fresh web info.").send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    history = cl.user_session.get("history", [])
    prior_history = compress_history(list(history))
    turn_history = list(history) + [{"role": "user", "content": message.content}]
    status = cl.Message(content="Routing...")
    await status.send()

    decision = await route_query(message.content, prior_history)
    if decision.decision.route == "general":
        client = OllamaClient()
        prompt = build_messages(
            "You are a concise local assistant.",
            message.content,
            prior_history,
        )
        status.content = "Generating response..."
        await status.update()
        answer_msg = cl.Message(content="")
        await answer_msg.send()
        answer = ""
        try:
            async for token in client.chat_stream(
                SETTINGS.ollama_chat_model,
                prompt,
                temperature=0.2,
                keep_alive=SETTINGS.rag_keep_alive,
            ):
                answer += token
                await answer_msg.stream_token(token)
        except Exception:
            result = await ask(message.content, history=prior_history)
            answer = result.get("final_answer") or result.get("general_answer") or "No answer produced."
            await answer_msg.stream_token(answer)
        if not answer:
            answer = "No answer produced."
            await answer_msg.stream_token(answer)
        await answer_msg.update()
        turn_history.append({"role": "assistant", "content": answer})
        cl.user_session.set("history", turn_history)
        return

    status.content = "Planning..."
    await status.update()
    state = RagState(user_input=message.content, route=decision.decision.route, route_reason=decision.decision.reason, chat_history=prior_history)
    plan = await build_plan(state)
    state.plan = plan
    status.content = "Retrieving local context..."
    await status.update()
    retrieval = await hybrid_retrieve(message.content)
    state.retrieved_chunks = retrieval.hits
    if plan.tasks:
        status.content = "Running parallel workers..."
        await status.update()
        worker_results = await run_parallel_tasks(plan)
        state.code_results = [r for r in worker_results if r.kind == "code"]
        state.web_results = [r for r in worker_results if r.kind == "web"]

    client = OllamaClient()
    prompt = build_messages(SYNTHESIZER_SYSTEM, _synthesis_prompt(state), prior_history)
    status.content = "Synthesizing response..."
    await status.update()
    answer_msg = cl.Message(content="")
    await answer_msg.send()
    answer = ""
    try:
        async for token in client.chat_stream(
            SETTINGS.ollama_chat_model,
            prompt,
            temperature=0.2,
            keep_alive=SETTINGS.rag_keep_alive,
        ):
            answer += token
            await answer_msg.stream_token(token)
    except Exception:
        result = await ask(message.content, history=prior_history)
        answer = result.get("fina    jjjl_answer") or result.get("general_answer") or "No answer produced."
        await answer_msg.stream_token(answer)
    if not answer:
        answer = "No answer produced."
        await answer_msg.stream_token(answer)
    await answer_msg.update()
    turn_history.append({"role": "assistant", "content": answer})
    cl.user_session.set("history", turn_history)
