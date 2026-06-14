"""Tests for rag_local.executor — parallel execution, retry, self-healing, confidence."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from rag_local.types import ExecutionPlan, PlanTask, RagState


def _make_plan(*task_dicts) -> ExecutionPlan:
    tasks = []
    for td in task_dicts:
        tasks.append(PlanTask(**td))
    return ExecutionPlan(
        objective="test",
        tasks=tasks,
        success_criteria=["done"],
    )


def _mcp_task(name: str, priority: int = 0, depends_on=None, can_parallel=True) -> dict:
    return {
        "name": name,
        "kind": "mcp",
        "query": '{"server_name": "duckduckgo", "tool_name": "search", "arguments": {"query": "test"}}',
        "priority": priority,
        "depends_on": depends_on or [],
        "can_parallel": can_parallel,
    }


# ---------------------------------------------------------------------------
# Dependency layer builder
# ---------------------------------------------------------------------------

def test_dependency_layers_sequential():
    """Tasks with explicit depends_on must form separate layers."""
    from rag_local.executor import _build_dependency_layers
    tasks = [
        PlanTask(**_mcp_task("a", priority=0)),
        PlanTask(**_mcp_task("b", priority=1, depends_on=["a"])),
        PlanTask(**_mcp_task("c", priority=2, depends_on=["b"])),
    ]
    layers = _build_dependency_layers(tasks)
    assert len(layers) == 3
    assert layers[0][0].name == "a"
    assert layers[1][0].name == "b"
    assert layers[2][0].name == "c"


def test_dependency_layers_parallel():
    """Independent tasks must be in the same layer."""
    from rag_local.executor import _build_dependency_layers
    tasks = [
        PlanTask(**_mcp_task("x", priority=0)),
        PlanTask(**_mcp_task("y", priority=0)),
        PlanTask(**_mcp_task("z", priority=1, depends_on=["x", "y"])),
    ]
    layers = _build_dependency_layers(tasks)
    assert len(layers) == 2
    first_layer_names = {t.name for t in layers[0]}
    assert "x" in first_layer_names and "y" in first_layer_names
    assert layers[1][0].name == "z"


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confidence_all_success():
    from rag_local.executor import score_confidence
    from rag_local.types import WorkerResult
    plan = _make_plan(_mcp_task("a"))
    plan = plan.model_copy(update={"confidence": 0.9})
    results = [WorkerResult(task_name="a", kind="mcp", success=True, summary="ok", confidence=1.0)]
    conf = await score_confidence(plan, results)
    assert conf.score >= 0.8
    assert not conf.needs_verification


@pytest.mark.asyncio
async def test_confidence_all_fail():
    from rag_local.executor import score_confidence
    from rag_local.types import WorkerResult
    plan = _make_plan(_mcp_task("a"))
    plan = plan.model_copy(update={"confidence": 0.4})
    results = [WorkerResult(task_name="a", kind="mcp", success=False, summary="Error", confidence=0.0)]
    conf = await score_confidence(plan, results)
    assert conf.score < 0.6
    assert conf.needs_verification


@pytest.mark.asyncio
async def test_confidence_empty_results():
    from rag_local.executor import score_confidence
    plan = _make_plan(_mcp_task("a"))
    conf = await score_confidence(plan, [])
    assert conf.score == 0.5


# ---------------------------------------------------------------------------
# execute_single_task — cache integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_single_task_uses_cache():
    """Second call for same args must hit cache and not invoke the base executor."""
    from rag_local.executor import execute_single_task
    from rag_local.cache import ResultCache

    task = PlanTask(**_mcp_task("search1"))
    state = RagState(user_input="find python news", route="web_search")

    fake_cache = ResultCache(ttl=60, max_size=10)
    # Pre-populate the cache with the task's parameters
    import json
    params = json.loads(task.query)
    key = ResultCache.make_key(params["server_name"], params["tool_name"], params["arguments"])
    fake_cache.put(key, "cached_result")

    base_called = []

    async def _fake_base(t, s):
        base_called.append(True)
        from rag_local.types import WorkerResult
        return WorkerResult(task_name=t.name, kind="mcp", success=True, summary="from_base")

    # get_cache is called inside execute_single_task via "from .cache import get_cache"
    # We patch at the module level where it's defined
    with patch("rag_local.cache.get_cache", return_value=fake_cache):
        result = await execute_single_task(task, state)

    # Cache pre-populated → base executor must NOT be called
    assert not base_called, "Base executor should not be called on cache hit"
    assert result.success
    assert result.summary == "cached_result"



# ---------------------------------------------------------------------------
# Verification agent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verification_detects_error_string():
    from rag_local.executor import VerificationAgent
    from rag_local.types import WorkerResult, PlanTask
    agent = VerificationAgent()
    task = PlanTask(**_mcp_task("t"))
    result = WorkerResult(task_name="t", kind="mcp", success=True, summary="Error: connection refused")
    passed, msg = await agent.check(task, result)
    assert not passed
    assert "error" in msg.lower()


@pytest.mark.asyncio
async def test_verification_passes_clean_result():
    from rag_local.executor import VerificationAgent
    from rag_local.types import WorkerResult, PlanTask
    agent = VerificationAgent()
    task = PlanTask(**_mcp_task("t"))
    result = WorkerResult(task_name="t", kind="mcp", success=True, summary="Found 3 results for your query.")
    passed, msg = await agent.check(task, result)
    assert passed
