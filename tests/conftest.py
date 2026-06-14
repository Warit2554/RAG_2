"""Shared pytest fixtures for the Nexus test suite."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ollama_response(payload: dict) -> str:
    """Serialise a dict as the JSON string that OllamaClient.chat returns."""
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ollama_chat():
    """Patch OllamaClient.chat so tests never hit a real Ollama server."""
    with patch("rag_local.embed.OllamaClient.chat", new_callable=AsyncMock) as m:
        yield m


@pytest.fixture
def mock_mcp_tools():
    """Patch mcp_manager.get_all_tools to return an empty list."""
    with patch(
        "rag_local.mcp_client.mcp_manager.get_all_tools",
        new_callable=AsyncMock,
        return_value=[],
    ) as m:
        yield m
