from __future__ import annotations


def compress_history(history: list[dict[str, str]], *, keep_last: int = 8, max_chars: int = 1200) -> list[dict[str, str]]:
    if len(history) <= keep_last:
        return history
    older = history[:-keep_last]
    recent = history[-keep_last:]
    summary_parts = []
    for message in older:
        role = message.get("role", "unknown")
        content = message.get("content", "").strip().replace("\n", " ")
        if not content:
            continue
        summary_parts.append(f"{role}: {content[:180]}")
    summary_text = " | ".join(summary_parts)
    if len(summary_text) > max_chars:
        summary_text = summary_text[:max_chars].rstrip() + "..."
    summary_message = {
        "role": "system",
        "content": f"Earlier conversation summary: {summary_text}",
    }
    return [summary_message] + recent
