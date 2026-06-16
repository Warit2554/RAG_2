"""Tests for rag_local.tool_router — semantic tool selection."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


SAMPLE_TOOLS = [
    {
        "server_name": "filesystem",
        "name": "read_file",
        "description": "Read the contents of a file from the filesystem",
        "inputSchema": {"properties": {"path": {}}, "required": ["path"]},
    },
    {
        "server_name": "filesystem",
        "name": "write_file",
        "description": "Write content to a file on the filesystem",
        "inputSchema": {"properties": {"path": {}, "content": {}}, "required": ["path", "content"]},
    },
    {
        "server_name": "duckduckgo",
        "name": "search",
        "description": "Search the web using DuckDuckGo",
        "inputSchema": {"properties": {"query": {}}, "required": ["query"]},
    },
    {
        "server_name": "operations",
        "name": "execute_operational_command",
        "description": "Execute a terminal shell command on the host machine",
        "inputSchema": {"properties": {"command": {}, "timeout_seconds": {}}, "required": ["command"]},
    },
    {
        "server_name": "fetch",
        "name": "fetch",
        "description": "Fetch and scrape the content of a web page or URL",
        "inputSchema": {"properties": {"url": {}}, "required": ["url"]},
    },
]


@pytest.mark.asyncio
async def test_select_tools_returns_subset():
    """select_tools must return at most top_k items."""
    from rag_local.tool_router import select_tools

    fake_vecs = [[float(i)] * 4 for i in range(len(SAMPLE_TOOLS) + 1)]
    # OllamaClient is imported locally inside select_tools — patch at source
    with patch("rag_local.embed.OllamaClient") as MockClient:
        instance = MockClient.return_value
        instance.embed = AsyncMock(side_effect=lambda model, texts: fake_vecs[: len(texts)])
        result = await select_tools("search the web for python news", SAMPLE_TOOLS, top_k=2)

    assert len(result) <= 2


@pytest.mark.asyncio
async def test_select_tools_similarity_filtering():
    """select_tools must filter out tools below threshold, returning only highly relevant ones."""
    from rag_local.tool_router import select_tools
    import rag_local.tool_router as tr
    tr._tool_embed_cache.clear()
    tr._all_tools_snapshot.clear()

    with patch("rag_local.embed.OllamaClient") as MockClient:
        instance = MockClient.return_value
        
        def mock_embed(model, texts):
            # 5 tools
            if len(texts) == 5:
                return [
                    [1.0, 0.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0, 1.0]
                ]
            # query
            return [[0.0, 0.0, 1.0, 0.0, 0.0]]
            
        instance.embed = AsyncMock(side_effect=mock_embed)
        result = await select_tools("query for search", SAMPLE_TOOLS, top_k=3)
        
    # Only the 'search' tool should meet the threshold (0.28)
    assert len(result) == 1
    assert result[0]["name"] == "search"


@pytest.mark.asyncio
async def test_select_tools_returns_all_when_catalogue_small():
    """When catalogue size ≤ top_k, all tools are returned without embedding."""
    from rag_local.tool_router import select_tools

    result = await select_tools("anything", SAMPLE_TOOLS[:2], top_k=10)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_select_tools_fallback_on_embed_error():
    """On embedding failure, the router gracefully returns up to top_k tools."""
    from rag_local.tool_router import select_tools
    import rag_local.tool_router as tr
    # Clear module-level cache so it tries to embed
    tr._tool_embed_cache.clear()
    tr._all_tools_snapshot.clear()

    # OllamaClient is imported locally — patch at source
    with patch("rag_local.embed.OllamaClient") as MockClient:
        instance = MockClient.return_value
        instance.embed = AsyncMock(side_effect=RuntimeError("Ollama offline"))
        result = await select_tools("install nginx", SAMPLE_TOOLS, top_k=3)

    # Fallback: at most top_k results, no crash
    assert len(result) <= 3


def test_format_tools_prompt_empty():
    from rag_local.tool_router import format_tools_prompt
    assert "No MCP tools" in format_tools_prompt([])


def test_format_tools_prompt_contains_names():
    from rag_local.tool_router import format_tools_prompt
    result = format_tools_prompt(SAMPLE_TOOLS)
    assert "read_file" in result
    assert "duckduckgo" in result
    assert "execute_operational_command" in result
