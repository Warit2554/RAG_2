from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-based conversation compression
# ---------------------------------------------------------------------------

_COMPRESS_SYSTEM = """\
You are a conversation compressor for an AI coding and DevOps assistant.
Summarise the OLDER part of the conversation (provided below) into a compact
paragraph that preserves:
- Tasks that were completed and their outcomes (e.g. "User downloaded X.iso")
- Key facts discovered (e.g. "The project uses Python 3.11")
- Any unresolved questions or follow-ups the user mentioned
- Files or paths that were created, modified, or inspected

DO NOT include:
- Greetings, pleasantries, or meta-commentary
- Raw tool outputs longer than ~50 words (keep only the outcome)
- Duplicate information

Output a single paragraph of at most 300 words.
"""


async def _llm_compress(turns: list[dict[str, str]]) -> str:
    """Use the Ollama chat model to produce a summary of older turns."""
    from .embed import OllamaClient, build_messages
    from .config import SETTINGS
    import asyncio

    content = "\n".join(
        f"{m.get('role', '?').upper()}: {m.get('content', '')[:400]}"
        for m in turns
    )
    messages = build_messages(_COMPRESS_SYSTEM, content)
    try:
        client = OllamaClient()
        summary = await asyncio.wait_for(
            client.chat(
                SETTINGS.ollama_chat_model,
                messages,
                temperature=0.1,
                keep_alive=SETTINGS.rag_keep_alive,
            ),
            timeout=30.0,
        )
        return summary.strip()
    except Exception as exc:
        logger.debug("[Memory] LLM compression failed, using truncation fallback: %s", exc)
        return ""


def _truncation_compress(turns: list[dict[str, str]], max_chars: int = 1200) -> str:
    """Simple truncation-based compression (no LLM required)."""
    parts = []
    for m in turns:
        role = m.get("role", "unknown")
        content = m.get("content", "").strip().replace("\n", " ")
        if content:
            parts.append(f"{role}: {content[:180]}")
    text = " | ".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


async def compress_history_async(
    history: list[dict[str, str]],
    *,
    keep_last: int = 8,
    max_chars: int = 1200,
    use_llm: bool = True,
) -> list[dict[str, str]]:
    """Async version: compresses older turns via LLM, falling back to truncation.

    Use this inside the graph nodes where an event loop is available.
    """
    if len(history) <= keep_last:
        return history
    older = history[:-keep_last]
    recent = history[-keep_last:]

    summary_text = ""
    if use_llm:
        summary_text = await _llm_compress(older)

    if not summary_text:
        summary_text = _truncation_compress(older, max_chars)

    if not summary_text:
        return recent

    summary_message = {
        "role": "system",
        "content": f"Earlier conversation summary: {summary_text}",
    }
    return [summary_message] + recent


def compress_history(
    history: list[dict[str, str]],
    *,
    keep_last: int = 8,
    max_chars: int = 1200,
) -> list[dict[str, str]]:
    """Synchronous wrapper that always uses truncation.

    Call ``compress_history_async`` from async contexts for LLM compression.
    """
    if len(history) <= keep_last:
        return history
    older = history[:-keep_last]
    recent = history[-keep_last:]
    summary_text = _truncation_compress(older, max_chars)
    if not summary_text:
        return recent
    return [{"role": "system", "content": f"Earlier conversation summary: {summary_text}"}] + recent


class LessonsMemory:
    """Persist task outcomes to a JSONL file and surface recent lessons as
    few-shot examples for the planner prompt.

    Each record written by ``record_outcome`` captures:
    - ``ts``: ISO-8601 timestamp (UTC)
    - ``query``: the original user query
    - ``tasks``: list of ``{name, kind}`` dicts from the plan
    - ``success``: bool
    - ``error``: short error summary string (may be empty)

    ``get_recent_lessons`` returns the last *n* failures (and successes, if
    fewer than *n* failures exist) formatted as a compact natural-language
    block suitable for insertion into a system prompt.
    """

    FILENAME = "lessons.jsonl"
    MAX_FILE_RECORDS = 500  # rotate after this many lines

    def __init__(self, workspace_dir: Path | str | None = None) -> None:
        if workspace_dir is None:
            from .config import PACKAGE_ROOT
            workspace_dir = PACKAGE_ROOT
        self._path = Path(workspace_dir) / self.FILENAME

    # ── Public API ────────────────────────────────────────────────────────────

    def record_outcome(
        self,
        query: str,
        plan_tasks: list[Any],
        success: bool,
        error_summary: str = "",
    ) -> None:
        """Append one outcome record to the lessons file (non-blocking)."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": query[:300],
            "tasks": [
                {"name": getattr(t, "name", "?"), "kind": getattr(t, "kind", "?")}
                for t in (plan_tasks or [])
            ],
            "success": success,
            "error": error_summary[:400] if error_summary else "",
        }
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._maybe_rotate()
        except Exception as exc:
            logging.debug("[LessonsMemory] Could not write record: %s", exc)

    def get_recent_lessons(self, n: int = 5) -> str:
        """Return the last *n* records as a formatted string for the planner.

        Failures are prioritised over successes so the planner can avoid
        repeating known-bad approaches.
        """
        records = self._read_all()
        if not records:
            return ""

        # Prefer failures, fill remainder with successes
        failures = [r for r in records if not r.get("success")][-n:]
        successes = [r for r in records if r.get("success")][-(max(0, n - len(failures))):]
        chosen = (failures + successes)[-n:]

        if not chosen:
            return ""

        lines = ["Recent task lessons (use these to avoid repeating mistakes):"]
        for r in chosen:
            status = "✓ succeeded" if r.get("success") else "✗ failed"
            task_summary = ", ".join(
                f"{t['kind']}:{t['name']}" for t in r.get("tasks", [])
            ) or "no tasks"
            error_note = f" — Error: {r['error']}" if r.get("error") else ""
            lines.append(
                f"  [{status}] \"{r.get('query', '')}\" → [{task_summary}]{error_note}"
            )
        return "\n".join(lines)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        records: list[dict] = []
        try:
            with open(self._path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logging.debug("[LessonsMemory] Could not read lessons: %s", exc)
        return records

    def _maybe_rotate(self) -> None:
        """Keep the file under MAX_FILE_RECORDS by trimming the oldest half."""
        try:
            records = self._read_all()
            if len(records) > self.MAX_FILE_RECORDS:
                keep = records[len(records) // 2:]
                with open(self._path, "w", encoding="utf-8") as fh:
                    for r in keep:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception as exc:
            logging.debug("[LessonsMemory] Rotation failed: %s", exc)


# Module-level singleton (lazy-initialised on first use)
_lessons: LessonsMemory | None = None


def get_lessons_memory() -> LessonsMemory:
    global _lessons
    if _lessons is None:
        _lessons = LessonsMemory()
    return _lessons
