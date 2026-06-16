"""Unit tests for the query router."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from rag_local.router import route_query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chat_response(route: str, confidence: float = 0.9, reason: str = "test") -> str:
    return json.dumps({"route": route, "confidence": confidence, "reason": reason})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_routes_to_web_search():
    """download / install / deploy queries must NOT go to rag or general."""
    queries = [
        "Download the Debian stable netinst ISO image",
        "Install nginx on my server",
        "Deploy the app to production",
    ]
    with patch("rag_local.router.OllamaClient.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = _chat_response("general")  # LLM tries 'general'
        for q in queries:
            decision = await route_query(q)
            assert decision.decision.route == "web_search", (
                f"Expected web_search for '{q}', got '{decision.decision.route}'"
            )


@pytest.mark.asyncio
async def test_code_question_routes_to_code_analysis():
    with patch("rag_local.router.OllamaClient.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = _chat_response("code_analysis")
        decision = await route_query("What does the orchestrator.py file do?")
        assert decision.decision.route == "code_analysis"


@pytest.mark.asyncio
async def test_greeting_routes_to_general():
    with patch("rag_local.router.OllamaClient.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = _chat_response("general")
        decision = await route_query("Hello, how are you?")
        assert decision.decision.route == "general"


@pytest.mark.asyncio
async def test_create_verb_stays_rag_not_web_search():
    """create/configure/setup should stay in rag, NOT escalate to web_search."""
    with patch("rag_local.router.OllamaClient.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = _chat_response("general")
        decision = await route_query("Create a new Python script in the project")
        # Should be redirected from general → rag (local file intent)
        assert decision.decision.route in {"rag", "code_analysis"}


@pytest.mark.asyncio
async def test_fallback_router_download_goes_to_web_search():
    """When Ollama is unavailable the fallback keyword router must handle download."""
    with patch("rag_local.router.OllamaClient.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = Exception("Connection refused")
        decision = await route_query("Download the latest Ubuntu ISO")
        assert decision.decision.route == "web_search"


@pytest.mark.asyncio
async def test_router_returns_confidence_between_0_and_1():
    with patch("rag_local.router.OllamaClient.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = _chat_response("rag", confidence=0.8)
        decision = await route_query("Explain what chunking.py does")
        assert 0.0 <= decision.decision.confidence <= 1.0


@pytest.mark.asyncio
async def test_time_query_routes_to_web_search():
    """Queries about time, clock, date must route to web_search."""
    queries = ["What is the current time?", "Show me the clock", "What date is it today?"]
    with patch("rag_local.router.OllamaClient.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = _chat_response("general")
        for q in queries:
            decision = await route_query(q)
            assert decision.decision.route == "web_search"


@pytest.mark.asyncio
async def test_fallback_router_time_goes_to_web_search():
    """Fallback router should route time queries to web_search on exception."""
    with patch("rag_local.router.OllamaClient.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = Exception("Ollama error")
        decision = await route_query("What time is it now?")
        assert decision.decision.route == "web_search"
