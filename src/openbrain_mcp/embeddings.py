from __future__ import annotations

import httpx

from .config import Settings


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=60.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def embed(self, text: str) -> list[float]:
        provider = self.settings.embedding_provider.lower()
        if provider == "ollama":
            return await self._embed_ollama(text)
        if provider == "openai":
            return await self._embed_openai(text)
        if provider == "openrouter":
            return await self._embed_openrouter(text)
        raise EmbeddingError(f"unknown embedding provider: {provider}")

    async def _embed_ollama(self, text: str) -> list[float]:
        url = f"{self.settings.ollama_base_url.rstrip('/')}/api/embed"
        resp = await self._client.post(
            url,
            json={"model": self.settings.embedding_model, "input": text},
        )
        if resp.status_code != 200:
            raise EmbeddingError(f"ollama embed failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings or not isinstance(embeddings, list):
            raise EmbeddingError(f"ollama embed missing embeddings: {data!r}")
        return embeddings[0]

    async def _embed_openai(self, text: str) -> list[float]:
        if not self.settings.openai_api_key:
            raise EmbeddingError("OPENAI_API_KEY not set")
        resp = await self._client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
            json={"model": self.settings.openai_model, "input": text},
        )
        if resp.status_code != 200:
            raise EmbeddingError(f"openai embed failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        return data["data"][0]["embedding"]

    async def _embed_openrouter(self, text: str) -> list[float]:
        if not self.settings.openrouter_api_key:
            raise EmbeddingError("OPENROUTER_API_KEY not set")
        resp = await self._client.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={"Authorization": f"Bearer {self.settings.openrouter_api_key}"},
            json={"model": self.settings.embedding_model, "input": text},
        )
        if resp.status_code != 200:
            raise EmbeddingError(f"openrouter embed failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        return data["data"][0]["embedding"]
