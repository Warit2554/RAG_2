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
