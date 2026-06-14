"""Unit tests for orchestrator build_plan and execute_task."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from rag_local.config import SETTINGS
from rag_local.types import RagState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan_response(tasks: list[dict], objective: str = "Test") -> str:
    return json.dumps({
        "objective": objective,
        "success_criteria": ["task completed"],
        "tasks": tasks,
        "response_style": "concise",
    })


def _make_mcp_task(name: str, server: str, tool: str, args: dict, priority: int = 1) -> dict:
    return {
        "name": name,
        "kind": "mcp",
        "query": {"server_name": server, "tool_name": tool, "arguments": args},
        "priority": priority,
    }


def _make_stream(response: str):
    async def _stream(*args, **kwargs):
        yield response
    return _stream


# ---------------------------------------------------------------------------
# build_plan tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_respects_max_tasks_setting():
    """Plan should never produce more tasks than SETTINGS.rag_plan_max_tasks."""
    # Generate more tasks than the cap
    num_tasks = SETTINGS.rag_plan_max_tasks + 3
    tasks = [
        _make_mcp_task(f"task_{i}", "duckduckgo", "search", {"query": f"step {i}"}, i)
        for i in range(num_tasks)
    ]
    with (
        patch("rag_local.orchestrator.OllamaClient.chat_stream", side_effect=_make_stream(_plan_response(tasks))),
        patch("rag_local.mcp_client.mcp_manager.get_all_tools", new_callable=AsyncMock, return_value=[]),
    ):
        from rag_local.orchestrator import build_plan
        state = RagState(user_input="do many things", route="web_search")
        plan = await build_plan(state)
        assert len(plan.tasks) <= SETTINGS.rag_plan_max_tasks, (
            f"Got {len(plan.tasks)} tasks, expected ≤ {SETTINGS.rag_plan_max_tasks}"
        )


@pytest.mark.asyncio
async def test_plan_has_success_criteria():
    """Planner output must include at least one success criterion."""
    tasks = [_make_mcp_task("search", "duckduckgo", "search", {"query": "test"})]
    with (
        patch("rag_local.orchestrator.OllamaClient.chat_stream", side_effect=_make_stream(_plan_response(tasks, objective="Search for something"))),
        patch("rag_local.mcp_client.mcp_manager.get_all_tools", new_callable=AsyncMock, return_value=[]),
    ):
        from rag_local.orchestrator import build_plan
        state = RagState(user_input="search for something", route="web_search")
        plan = await build_plan(state)
        assert len(plan.success_criteria) > 0


@pytest.mark.asyncio
async def test_plan_fallback_on_ollama_unreachable():
    """When Ollama is down, build_plan must return a fallback plan (not crash)."""
    import httpx

    async def mock_err_stream(*args, **kwargs):
        if False:
            yield ""
        raise httpx.ConnectError("Connection refused")

    with (
        patch("rag_local.orchestrator.OllamaClient.chat_stream", side_effect=mock_err_stream),
        patch("rag_local.mcp_client.mcp_manager.get_all_tools", new_callable=AsyncMock, return_value=[]),
    ):
        from rag_local.orchestrator import build_plan
        state = RagState(user_input="download ubuntu iso", route="web_search")
        plan = await build_plan(state)
        # Should not raise; fallback plan may be empty but must be an ExecutionPlan
        from rag_local.types import ExecutionPlan
        assert isinstance(plan, ExecutionPlan)


@pytest.mark.asyncio
async def test_plan_fallback_on_bad_json():
    """Malformed LLM JSON must not crash the planner."""
    with (
        patch("rag_local.orchestrator.OllamaClient.chat_stream", side_effect=_make_stream("this is not json {")),
        patch("rag_local.mcp_client.mcp_manager.get_all_tools", new_callable=AsyncMock, return_value=[]),
    ):
        from rag_local.orchestrator import build_plan
        state = RagState(user_input="search for news", route="web_search")
        plan = await build_plan(state)
        from rag_local.types import ExecutionPlan
        assert isinstance(plan, ExecutionPlan)


# ---------------------------------------------------------------------------
# execute_task — download kind fix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_download_kind_uses_curl_not_write_file():
    """Legacy 'download' kind must dispatch curl via operations, NOT write_file."""
    from rag_local.types import PlanTask
    from rag_local.orchestrator import execute_task

    task = PlanTask(
        name="dl_test",
        kind="download",
        query="https://example.com/file.iso",
        priority=0,
    )

    captured_calls: list[dict] = []

    async def mock_call_tool(server_name, tool_name, arguments, **kwargs):
        captured_calls.append({
            "server_name": server_name,
            "tool_name": tool_name,
            "arguments": arguments,
        })
        return "Success: downloaded"

    with (
        patch("rag_local.mcp_client.mcp_manager") as mock_mgr,
        patch("rag_local.orchestrator.verify_action", new_callable=AsyncMock, return_value=(True, "ok")),
    ):
        mock_mgr.sessions = {"operations": object()}
        mock_mgr.call_tool = AsyncMock(side_effect=mock_call_tool)

        await execute_task(task)

    # The call must go to operations/execute_operational_command with a curl command
    assert len(captured_calls) > 0, "No MCP call was made"
    call = captured_calls[0]
    assert call["server_name"] == "operations", (
        f"Expected 'operations', got '{call['server_name']}'"
    )
    assert call["tool_name"] == "execute_operational_command", (
        f"Expected 'execute_operational_command', got '{call['tool_name']}'"
    )
    command = call["arguments"].get("command", "")
    assert "curl" in command or "wget" in command, (
        f"Expected curl/wget in command, got: {command}"
    )
    # Must NOT write the query text as file content
    assert "https://example.com/file.iso" not in call["arguments"].get("content", ""), (
        "download kind must not write the URL as file content"
    )


@pytest.mark.asyncio
async def test_plan_retries_without_json_format_on_first_failure():
    """If first attempt (JSON format) fails or is unreachable, build_plan must retry without format constraint."""
    from rag_local.orchestrator import build_plan
    from rag_local.types import RagState

    captured_formats: list[str | None] = []

    async def mock_chat_stream_fn(model, messages, *, format=None, **kwargs):
        captured_formats.append(format)
        if len(captured_formats) == 1:
            if False:
                yield ""
            raise ValueError("Simulated JSON constraint error")
        yield json.dumps({
            "objective": "Retry success",
            "success_criteria": ["succeeds on retry"],
            "tasks": [
                {
                    "name": "retried_task",
                    "kind": "mcp",
                    "query": {"server_name": "duckduckgo", "tool_name": "search", "arguments": {"query": "retried"}},
                    "priority": 1
                }
            ],
            "response_style": "concise"
        })

    with (
        patch("rag_local.orchestrator.OllamaClient.chat_stream", side_effect=mock_chat_stream_fn),
        patch("rag_local.mcp_client.mcp_manager.get_all_tools", new_callable=AsyncMock, return_value=[]),
    ):
        state = RagState(user_input="check storage", route="rag")
        plan = await build_plan(state)
        
        # Verify both attempts were made: first with JSON format, second without
        assert len(captured_formats) == 2
        assert captured_formats[0] == "json"
        assert captured_formats[1] is None
        
        # Verify retry plan was parsed successfully
        assert plan.objective == "Retry success"
        assert len(plan.tasks) == 1
        assert plan.tasks[0].name == "retried_task"


@pytest.mark.asyncio
async def test_plan_assigned_agents():
    """Planner must extract and map tasks to Researcher or Coder correctly."""
    from rag_local.orchestrator import build_plan
    from rag_local.types import RagState, SharedMemoryState

    tasks = [
        {
            "name": "research_step",
            "kind": "mcp",
            "query": {"server_name": "duckduckgo", "tool_name": "search", "arguments": {"query": "test"}},
            "priority": 1,
            "assigned_agent": "researcher"
        },
        {
            "name": "coding_step",
            "kind": "mcp",
            "query": {"server_name": "operations", "tool_name": "execute_operational_command", "arguments": {"command": "echo 'hello'"}},
            "priority": 2,
            "assigned_agent": "coder"
        },
        {
            "name": "default_coder_step",
            "kind": "mcp",
            "query": "echo 'default'",
            "priority": 3
            # assigned_agent missing -> should default to coder
        }
    ]

    with (
        patch("rag_local.orchestrator.OllamaClient.chat_stream", side_effect=_make_stream(_plan_response(tasks))),
        patch("rag_local.mcp_client.mcp_manager.get_all_tools", new_callable=AsyncMock, return_value=[]),
    ):
        shared_memory = SharedMemoryState(
            blackboard=["[Planner] Started test run."],
            iteration=1
        )
        state = RagState(user_input="test query", route="web_search", shared_memory=shared_memory)
        plan = await build_plan(state)

        assert len(plan.tasks) == 3
        assert plan.tasks[0].assigned_agent == "researcher"
        assert plan.tasks[1].assigned_agent == "coder"
        assert plan.tasks[2].assigned_agent == "coder"

