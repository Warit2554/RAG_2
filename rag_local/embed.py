from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable

import httpx

from .config import SETTINGS


@dataclass(slots=True)
class OllamaClient:
    host: str = SETTINGS.ollama_host
    timeout: float = 120.0

    def _url(self, path: str) -> str:
        return f"{self.host.rstrip('/')}/{path.lstrip('/')}"

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        keep_alive: str | None = None,
        stream: bool = False,
        format: str | None = None,
    ) -> str | httpx.Response:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if keep_alive:
            payload["keep_alive"] = keep_alive
        if format:
            payload["format"] = format
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if stream:
                return await client.post(self._url("/api/chat"), json=payload)
            response = await client.post(self._url("/api/chat"), json=payload)
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        keep_alive: str | None = None,
        format: str | None = None,
    ):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if keep_alive:
            payload["keep_alive"] = keep_alive
        if format:
            payload["format"] = format
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", self._url("/api/chat"), json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    delta = data.get("message", {}).get("content")
                    if delta:
                        yield delta
                    if data.get("done"):
                        break

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts.

        Attempts to use Ollama's batch ``/api/embed`` endpoint (≥ v0.1.32) to
        send all texts in a single request.  Falls back to the legacy
        ``/api/embeddings`` serial loop for older Ollama versions.
        """
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # ── Try batch endpoint first ─────────────────────────────────────
            try:
                response = await client.post(
                    self._url("/api/embed"),
                    json={"model": model, "input": texts},
                )
                if response.status_code == 200:
                    data = response.json()
                    embeddings = data.get("embeddings") or data.get("embedding")
                    if embeddings and len(embeddings) == len(texts):
                        return [list(v) for v in embeddings]
            except Exception:
                pass  # fall through to serial loop

            # ── Serial fallback for older Ollama builds ───────────────────────
            vectors: list[list[float]] = []
            for text in texts:
                response = await client.post(
                    self._url("/api/embeddings"),
                    json={"model": model, "prompt": text},
                )
                response.raise_for_status()
                vectors.append(response.json()["embedding"])
            return vectors


def build_messages(system: str, user: str, history: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user})
    return messages
