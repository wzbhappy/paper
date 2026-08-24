"""Embedding 抽象：支持 Ollama / OpenAI 兼容端点，测试用确定性 hash 实现。"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

import httpx
from loguru import logger

from app.config import settings
from app.llm.base import LLMError


@runtime_checkable
class Embedder(Protocol):
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


class HashEmbedder:
    """确定性伪 embedding：把 token hash 映射到固定维度。

    仅用于测试与无 GPU 的本地冒烟，语义质量有限但可重复。
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = text.lower().split()
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return _l2_normalize(vec)


class OllamaEmbedder:
    """调用 Ollama /api/embed，本地零成本。"""

    def __init__(self, model: str, base_url: str, dim: int, timeout: float = 120.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dim = dim
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        url = f"{self.base_url}/api/embed"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    url, json={"model": self.model, "input": texts}
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama embed failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(f"ollama embed HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        vectors = data.get("embeddings") or []
        if len(vectors) != len(texts):
            raise LLMError(
                f"ollama embed returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return [_l2_normalize([float(v) for v in vec]) for vec in vectors]


class OpenAIEmbedder:
    """调用 OpenAI 兼容 /embeddings 端点。"""

    def __init__(
        self, model: str, base_url: str, api_key: str, dim: int, timeout: float = 120.0
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dim = dim
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    url, headers=headers, json={"model": self.model, "input": texts}
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"openai embed failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(f"openai embed HTTP {resp.status_code}: {resp.text[:300]}")

        items = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
        return [_l2_normalize([float(v) for v in item["embedding"]]) for item in items]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """按配置选择 embedder：OpenAI 模型名走 OpenAI，否则走 Ollama。"""
    global _embedder
    if _embedder is not None:
        return _embedder

    model = settings.embedding_model
    if model.startswith("text-embedding") and settings.openai_api_key:
        _embedder = OpenAIEmbedder(
            model,
            settings.openai_base_url,
            settings.openai_api_key,
            settings.embedding_dim,
        )
    else:
        _embedder = OllamaEmbedder(
            model, settings.ollama_base_url, settings.embedding_dim
        )
    logger.info("embedder ready: {} (dim={})", model, settings.embedding_dim)
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """注入自定义 embedder（测试用）。"""
    global _embedder
    _embedder = embedder
