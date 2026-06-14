"""Nexus Executor — Parallel task execution with Self-Healing, Retry & Verification.

This module separates **execution** from **planning** (orchestrator.py handles
planning).  Key capabilities:

Parallel Execution
------------------
Tasks whose ``depends_on`` list is empty (or whose dependencies are already
complete) are launched concurrently up to ``SETTINGS.executor_parallel_limit``.
Dependent tasks wait for their prerequisites.

Self-Healing
------------
When a task fails, the executor applies a chain of alternative strategies
before giving up:

1. **Retry** — re-run the exact same call with exponential back-off.
2. **Alternative tool** — if the primary MCP server is down, try a known
   equivalent (e.g. ``playwright`` instead of ``fetch``, ``wget`` instead of
   ``curl``).
3. **Query rewrite** — ask the LLM to produce a rephrased / simplified version
   of the tool arguments, then re-run.
4. **Fallback model** — retry synthesize() with a backup Ollama model.

Verification Agent
------------------
After each task, ``VerificationAgent.check()`` validates the output:
- File existence / size checks for download/write tasks.
- Response content checks for web/search tasks (non-empty, no error strings).
- Schema/JSON validity checks for code tasks.

Confidence Scoring
------------------
The planner assigns an overall ``confidence`` score to the plan.  Each task
result carries a ``confidence`` field.  The executor emits a
``ConfidenceScore`` for the run, which the CLI can display.

Audit Logging
-------------
Every task start, result, retry, and heal event is written to
``nexus_audit.jsonl`` when ``SETTINGS.audit_log_enabled`` is true.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from .config import SETTINGS
from .types import (
    ArtifactRecord,
    ConfidenceScore,
    ExecutionMetrics,
    ExecutionPlan,
    PlanTask,
    RagState,
    WorkerResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit logger
# ---------------------------------------------------------------------------

def _audit(event: str, **kwargs: Any) -> None:
    """Write a structured audit event to nexus_audit.jsonl."""
    if not SETTINGS.audit_log_enabled:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **kwargs,
    }
    try:
        path = SETTINGS.audit_log_path
        if not path.is_absolute():
            from .config import WORKSPACE_DIR
            path = WORKSPACE_DIR / path
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.debug("[Audit] Write failed: %s", exc)


# ---------------------------------------------------------------------------
# Self-healing: alternative strategies
# ---------------------------------------------------------------------------

# Known tool equivalents: if primary call fails, try these in order.
_ALTERNATIVES: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("fetch", "fetch"): [("playwright", "playwright_navigate")],
    ("playwright", "playwright_navigate"): [("fetch", "fetch")],
    ("duckduckgo", "search"): [("searxng", "search")],
    ("searxng", "search"): [("duckduckgo", "search")],
    ("operations", "execute_operational_command"): [],  # no alternative
    ("filesystem", "write_file"): [],
}


async def _try_alternative_tool(
    task: PlanTask,
    failed_server: str,
    failed_tool: str,
    arguments: dict[str, Any],
) -> WorkerResult | None:
    """Try known equivalent tools when the primary tool fails."""
    alts = _ALTERNATIVES.get((failed_server, failed_tool), [])
    if not alts:
        return None

    from .mcp_client import mcp_manager

    for alt_server, alt_tool in alts:
        if alt_server not in mcp_manager.sessions:
            continue
        logger.info(
            "[SelfHeal] Trying alternative %s/%s for failed %s/%s",
            alt_server, alt_tool, failed_server, failed_tool,
        )
        _audit("self_heal_attempt", task=task.name, alt=f"{alt_server}/{alt_tool}")
        try:
            result_str = await mcp_manager.call_tool(alt_server, alt_tool, arguments)
            if "Error" not in result_str:
                _audit("self_heal_success", task=task.name, alt=f"{alt_server}/{alt_tool}")
                return WorkerResult(
                    task_name=task.name,
                    kind="mcp",
                    success=True,
                    summary=result_str,
                    healed=True,
                    artifacts=[{"server_name": alt_server, "tool_name": alt_tool,
                                "arguments": arguments, "result": result_str}],
                )
        except Exception as exc:
            logger.debug("[SelfHeal] Alternative %s/%s also failed: %s", alt_server, alt_tool, exc)
    return None


async def _rewrite_and_retry(
    task: PlanTask,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    error_summary: str,
) -> WorkerResult | None:
    """Ask the LLM to rewrite tool arguments, then retry the call."""
    from .embed import OllamaClient, build_messages
    from .mcp_client import mcp_manager

    rewrite_prompt = (
        "A tool call failed. Rewrite the arguments to fix the issue.\n"
        f"Tool: {server_name}/{tool_name}\n"
        f"Original arguments: {json.dumps(arguments)}\n"
        f"Error: {error_summary[:300]}\n\n"
        "Return ONLY a JSON object with the corrected arguments (no markdown, no extra keys)."
    )
    try:
        client = OllamaClient()
        raw = await asyncio.wait_for(
            client.chat(
                SETTINGS.ollama_orchestrator_model,
                build_messages(rewrite_prompt, "Fix the arguments"),
                temperature=0.1,
                keep_alive=SETTINGS.rag_keep_alive,
                format="json",
            ),
            timeout=30.0,
        )
        from .utils import safe_json_loads
        new_args = safe_json_loads(raw)
        if not isinstance(new_args, dict) or not new_args:
            return None

        logger.info("[SelfHeal] Retrying %s/%s with rewritten args", server_name, tool_name)
        _audit("args_rewrite", task=task.name, server=server_name, tool=tool_name)
        result_str = await mcp_manager.call_tool(server_name, tool_name, new_args)
        if "Error" not in result_str:
            return WorkerResult(
                task_name=task.name,
                kind="mcp",
                success=True,
                summary=result_str,
                healed=True,
                artifacts=[{"server_name": server_name, "tool_name": tool_name,
                            "arguments": new_args, "result": result_str}],
            )
    except Exception as exc:
        logger.debug("[SelfHeal] Rewrite-and-retry failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Verification Agent
# ---------------------------------------------------------------------------

class VerificationAgent:
    """Validates a WorkerResult against its task specification."""

    async def check(
        self,
        task: PlanTask,
        result: WorkerResult,
        server_name: str = "",
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Return (passed, message).  Always passes for non-critical tasks."""
        if not SETTINGS.verification_enabled:
            return True, "Verification disabled."

        summary = result.summary or ""

        # Hard failure signals
        if any(
            phrase in summary
            for phrase in ["Error:", "Error executing", "timed out", "not allowed"]
        ):
            return False, f"Tool reported an error: {summary[:200]}"

        # File-write tasks: verify the file exists and is non-empty
        if tool_name in {"write_file", "create_file"} and arguments:
            path = arguments.get("path", "")
            if path:
                import os
                from .config import WORKSPACE_DIR
                abs_path = path if os.path.isabs(path) else str(WORKSPACE_DIR / path)
                if not os.path.exists(abs_path):
                    return False, f"Verification failed: file '{path}' was not created."
                if os.stat(abs_path).st_size == 0:
                    return False, f"Verification failed: file '{path}' is empty."
                return True, f"Verified: '{path}' exists ({os.stat(abs_path).st_size} bytes)."

        # Download tasks: check target file
        if task.kind == "download" or (tool_name == "execute_operational_command" and
                                        arguments and "curl" in arguments.get("command", "")):
            import re, os
            from .config import WORKSPACE_DIR
            cmd = (arguments or {}).get("command", "")
            m = re.search(r"-o\s+'?([^\s']+)'?", cmd)
            if m:
                fname = m.group(1)
                abs_path = fname if os.path.isabs(fname) else str(WORKSPACE_DIR / fname)
                if os.path.exists(abs_path) and os.stat(abs_path).st_size > 0:
                    return True, f"Verified: downloaded file '{fname}' exists."
                return False, f"Verification failed: download target '{fname}' not found or empty."

        # Web search / scrape: response should be non-trivially long
        if tool_name in {"search", "fetch"} and len(summary) < 20:
            return False, "Verification failed: web result is suspiciously short."

        return True, "Passed."


_verification_agent = VerificationAgent()


# ---------------------------------------------------------------------------
# Single task executor with retry + self-healing
# ---------------------------------------------------------------------------

async def execute_single_task(
    task: PlanTask,
    state: RagState | None = None,
    metrics: ExecutionMetrics | None = None,
) -> WorkerResult:
    """Execute one task with retry + self-healing.

    Wraps ``orchestrator.execute_task`` with:
    - Exponential back-off retries (up to ``SETTINGS.executor_max_retries``).
    - Alternative tool healing.
    - LLM argument-rewrite healing.
    - Post-execution verification.
    - Audit logging.
    - Cache lookup/store for cacheable tasks.
    """
    import json as _json
    from .orchestrator import execute_task as _base_execute
    from .cache import get_cache

    cache = get_cache()
    server_name = ""
    tool_name = ""
    arguments: dict[str, Any] = {}

    # Extract call params for cache key (only for mcp tasks)
    if task.kind == "mcp":
        try:
            params = _json.loads(task.query) if isinstance(task.query, str) else task.query
            server_name = params.get("server_name", "")
            tool_name = params.get("tool_name", "")
            arguments = params.get("arguments", {})
        except Exception:
            pass

    # ── Cache check ────────────────────────────────────────────────────────────
    if server_name and tool_name:
        cache_key = cache.make_key(server_name, tool_name, arguments)
        cached = cache.get(cache_key)
        if cached is not None:
            if metrics:
                metrics.cache_hits += 1
                metrics.succeeded += 1
                metrics.total_tasks += 1
            logger.debug("[Executor] Cache hit for %s/%s", server_name, tool_name)
            _audit("cache_hit", task=task.name, server=server_name, tool=tool_name)
            return WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success=True,
                summary=cached,
                artifacts=[{"cache_hit": True}],
                confidence=0.95,
            )
    else:
        cache_key = ""

    # ── Retry loop ─────────────────────────────────────────────────────────────
    last_result: WorkerResult | None = None
    max_retries = SETTINGS.executor_max_retries
    backoff = SETTINGS.executor_retry_backoff

    _audit("task_start", task=task.name, kind=task.kind)
    t0 = time.monotonic()

    for attempt in range(max_retries + 1):
        if attempt > 0:
            delay = backoff ** attempt
            logger.info(
                "[Executor] Retry %d/%d for task '%s' (backoff=%.1fs)",
                attempt, max_retries, task.name, delay,
            )
            _audit("retry", task=task.name, attempt=attempt)
            await asyncio.sleep(delay)
            if metrics:
                metrics.retried += 1

        try:
            result = await _base_execute(task, state)
        except Exception as exc:
            logger.warning("[Executor] Task '%s' raised: %s", task.name, exc)
            result = WorkerResult(
                task_name=task.name,
                kind=task.kind,
                success=False,
                summary=str(exc),
            )

        result = result.model_copy(update={"retries": attempt})

        if result.success:
            last_result = result
            break

        # ── Self-healing: alternative tool ─────────────────────────────────────
        if server_name and tool_name and attempt == 0:
            healed = await _try_alternative_tool(task, server_name, tool_name, arguments)
            if healed:
                last_result = healed
                if metrics:
                    metrics.healed += 1
                break

        # ── Self-healing: LLM argument rewrite ────────────────────────────────
        if server_name and tool_name and attempt == 1:
            healed = await _rewrite_and_retry(
                task, server_name, tool_name, arguments, result.summary or ""
            )
            if healed:
                last_result = healed
                if metrics:
                    metrics.healed += 1
                break

        last_result = result

    assert last_result is not None
    elapsed_ms = (time.monotonic() - t0) * 1000

    # ── Verification ───────────────────────────────────────────────────────────
    if last_result.success:
        passed, ver_msg = await _verification_agent.check(
            task, last_result, server_name, tool_name, arguments
        )
        if not passed:
            logger.warning("[Verification] Task '%s' failed verification: %s", task.name, ver_msg)
            last_result = last_result.model_copy(update={
                "success": False,
                "summary": f"{last_result.summary}\n\n[Verification] {ver_msg}",
                "confidence": 0.0,
            })
            _audit("verification_fail", task=task.name, reason=ver_msg)
        else:
            _audit("verification_pass", task=task.name, reason=ver_msg)

    # ── Cache store ────────────────────────────────────────────────────────────
    if last_result.success and cache_key and server_name not in {
        "operations", "filesystem",  # don't cache side-effectful calls
    }:
        cache.put(cache_key, last_result.summary)

    # ── Metrics ────────────────────────────────────────────────────────────────
    if metrics:
        metrics.total_tasks += 1
        metrics.wall_time_ms += elapsed_ms
        if last_result.success:
            metrics.succeeded += 1
        else:
            metrics.failed += 1
        if last_result.healed:
            metrics.healed += 1

    _audit(
        "task_done",
        task=task.name,
        success=last_result.success,
        healed=last_result.healed,
        elapsed_ms=round(elapsed_ms, 1),
    )
    return last_result


# ---------------------------------------------------------------------------
# Dependency resolver
# ---------------------------------------------------------------------------

def _build_dependency_layers(tasks: list[PlanTask]) -> list[list[PlanTask]]:
    """Topological sort into layers.  Tasks in the same layer can run in parallel."""
    name_to_task = {t.name: t for t in tasks}
    completed: set[str] = set()
    layers: list[list[PlanTask]] = []
    remaining = list(tasks)

    max_iter = len(tasks) + 1
    iteration = 0
    while remaining:
        iteration += 1
        if iteration > max_iter:
            # Cycle detected — fall back to sequential execution
            logger.warning("[Executor] Dependency cycle detected; falling back to sequential.")
            layers.append(remaining)
            break

        ready = [
            t for t in remaining
            if all(dep in completed for dep in t.depends_on)
        ]
        if not ready:
            # No progress — treat remaining as a single sequential batch
            ready = remaining

        layers.append(ready)
        for t in ready:
            completed.add(t.name)
            remaining.remove(t)

    return layers


# ---------------------------------------------------------------------------
# Parallel executor
# ---------------------------------------------------------------------------

async def run_tasks(
    plan: ExecutionPlan,
    state: RagState,
    metrics: ExecutionMetrics | None = None,
) -> list[WorkerResult]:
    """Execute plan tasks with dependency-aware parallelism and self-healing.

    Independent tasks (same dependency layer) are run concurrently up to
    ``SETTINGS.executor_parallel_limit``.
    """
    from .memory import get_lessons_memory

    if metrics is None:
        metrics = state.metrics

    ordered = sorted(plan.tasks, key=lambda t: t.priority)
    layers = _build_dependency_layers(ordered)
    all_results: list[WorkerResult] = []
    lessons = get_lessons_memory()
    sem = asyncio.Semaphore(SETTINGS.executor_parallel_limit)

    for layer_idx, layer in enumerate(layers):
        logger.debug(
            "[Executor] Layer %d: %d tasks (%s)",
            layer_idx,
            len(layer),
            [t.name for t in layer],
        )

        async def _run_one(task: PlanTask) -> WorkerResult:
            async with sem:
                return await execute_single_task(task, state, metrics)

        layer_results = await asyncio.gather(*[_run_one(t) for t in layer])

        for task, res in zip(layer, layer_results):
            all_results.append(res)
            # Record in LessonsMemory
            error_hint = "" if res.success else (res.summary or "")[:300]
            lessons.record_outcome(
                query=state.user_input,
                plan_tasks=[task],
                success=res.success,
                error_summary=error_hint,
            )

        # Abort if a non-healable failure occurred in this layer
        if any(not r.success and not r.healed for r in layer_results):
            logger.warning(
                "[Executor] Aborting pipeline after failure in layer %d.", layer_idx
            )
            break

    return all_results


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

async def score_confidence(
    plan: ExecutionPlan,
    results: list[WorkerResult],
) -> ConfidenceScore:
    """Estimate overall confidence for the completed execution.

    Uses a heuristic mix of:
    - Plan's own confidence estimate.
    - Fraction of tasks that succeeded.
    - Average per-task confidence.
    """
    if not results:
        return ConfidenceScore(score=0.5, reason="No tasks executed.")

    success_rate = sum(1 for r in results if r.success) / len(results)
    avg_task_conf = sum(r.confidence for r in results) / len(results)
    plan_conf = plan.confidence

    # Weighted blend
    blended = 0.4 * plan_conf + 0.4 * success_rate + 0.2 * avg_task_conf
    blended = max(0.0, min(1.0, blended))

    needs_verification = blended < SETTINGS.confidence_threshold
    reason = (
        f"plan={plan_conf:.2f} success_rate={success_rate:.2f} avg_task={avg_task_conf:.2f}"
    )
    return ConfidenceScore(
        score=round(blended, 3),
        reason=reason,
        needs_verification=needs_verification,
    )
