"""Unit tests for OllamaClient.embed (batch vs serial fallback)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.embed import OllamaClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status: int, body: dict) -> MagicMock:
    """Return a fake httpx Response-like object."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_uses_batch_endpoint_when_available():
    """embed() should use /api/embed batch call when Ollama supports it."""
    texts = ["hello world", "foo bar"]
    batch_embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    batch_resp = _make_response(200, {"embeddings": batch_embeddings})

    with patch("rag_local.embed.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=batch_resp)

        client = OllamaClient()
        result = await client.embed("nomic-embed-text", texts)

    assert result == batch_embeddings
    # Exactly ONE HTTP call should have been made (the batch endpoint)
    assert instance.post.call_count == 1
    call_url = instance.post.call_args[0][0]
    assert "/api/embed" in call_url


@pytest.mark.asyncio
async def test_embed_falls_back_to_serial_on_404():
    """embed() must fall back to serial /api/embeddings when /api/embed returns 404."""
    texts = ["hello", "world"]
    serial_embedding = [0.9, 0.8, 0.7]

    batch_resp = _make_response(404, {})  # old Ollama
    serial_resp = _make_response(200, {"embedding": serial_embedding})

    call_count = 0

    async def fake_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        # Use exact suffix match to distinguish /api/embed from /api/embeddings
        if url.endswith("/api/embed"):
            return batch_resp
        return serial_resp

    with patch("rag_local.embed.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(side_effect=fake_post)

        client = OllamaClient()
        result = await client.embed("nomic-embed-text", texts)

    # Should have one failed batch call + N serial calls
    assert len(result) == len(texts)
    assert result[0] == serial_embedding
    assert call_count == 1 + len(texts)  # 1 batch attempt + 2 serial


@pytest.mark.asyncio
async def test_embed_empty_list_returns_empty():
    """embed() with an empty list must return [] without making HTTP calls."""
    with patch("rag_local.embed.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock()

        client = OllamaClient()
        result = await client.embed("nomic-embed-text", [])

    assert result == []
    instance.post.assert_not_called()


@pytest.mark.asyncio
async def test_embed_falls_back_to_serial_on_exception():
    """embed() must fall back to serial if the batch endpoint raises an exception."""
    texts = ["alpha", "beta"]
    serial_embedding = [0.1, 0.2]

    serial_resp = _make_response(200, {"embedding": serial_embedding})

    async def fake_post(url, **kwargs):
        # Use exact suffix match: raise only for /api/embed, not /api/embeddings
        if url.endswith("/api/embed"):
            raise ConnectionError("simulated network error")
        return serial_resp

    with patch("rag_local.embed.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(side_effect=fake_post)

        client = OllamaClient()
        result = await client.embed("nomic-embed-text", texts)

    assert len(result) == len(texts)


@pytest.mark.asyncio
async def test_compress_context_with_embeddings():
    """compress_context_with_embeddings should keep only high-similarity sections and snip others."""
    from rag_local.types import SearchHit
    from rag_local.tools.retrieval.tool import compress_context_with_embeddings

    query = "find fabric server eula"
    content = (
        "To setup a fabric server, you must sign eula.txt first. "
        "This is a crucial first step for setting up any Minecraft server using Fabric. "
        "Make sure to read the terms and conditions carefully before proceeding. "
        "Once you have signed it, you can run the server jar file using your terminal.\n\n"
        "Here is how to bake a delicious chocolate cake: mix flour and sugar. "
        "You will need a clean bowl, two fresh eggs, a cup of cocoa powder, and some chocolate chips. "
        "Preheat your oven to 350 degrees Fahrenheit, grease the pan, and bake for approximately 35 minutes.\n\n"
        "Ensure fabric eula agreement is set to true in eula.txt file. "
        "By changing eula=false to eula=true in this text file, you acknowledge that you accept the end user license agreement. "
        "Without doing this, the server startup sequence will immediately exit and fail."
    )
    
    hit = SearchHit(
        chunk_id="chunk_1",
        score=0.9,
        source_path="instructions.txt",
        title="Server Setup",
        content=content,
        summary="A guide to set up a server.",
        language="txt",
        chunk_type="text"
    )

    async def mock_embed(model, texts):
        if texts == [query]:
            return [[1.0, 0.0]]
        return [
            [0.9, 0.1],  # sec1 vector
            [0.0, 1.0],  # sec2 vector
            [0.9, 0.1],  # sec3 vector
        ]

    with patch("rag_local.embed.OllamaClient.embed", side_effect=mock_embed):
        compressed_hits = await compress_context_with_embeddings(query, [hit], threshold=0.35)
        
    assert len(compressed_hits) == 1
    compressed_hit = compressed_hits[0]
    
    assert "fabric server" in compressed_hit.content
    assert "eula.txt" in compressed_hit.content
    assert "chocolate cake" not in compressed_hit.content
    assert "... [snipped less relevant code] ..." in compressed_hit.content


@pytest.mark.asyncio
async def test_chat_passes_num_ctx_options():
    """chat() should pass the configured OLLAMA_NUM_CTX inside options block."""
    from rag_local.config import SETTINGS
    
    with (
        patch.object(SETTINGS, "ollama_num_ctx", 8192),
        patch("rag_local.embed.httpx.AsyncClient") as MockClient
    ):
        instance = MockClient.return_value.__aenter__.return_value
        fake_resp = _make_response(200, {"message": {"content": "ok"}})
        instance.post = AsyncMock(return_value=fake_resp)
        
        client = OllamaClient()
        await client.chat("model_name", [{"role": "user", "content": "hi"}])
        
        # Verify JSON payload
        called_args = instance.post.call_args
        assert called_args is not None
        payload = called_args[1]["json"]
        assert "options" in payload
        assert payload["options"]["num_ctx"] == 8192

