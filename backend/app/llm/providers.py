"""OpenAI 兼容 provider：覆盖 OpenAI / DeepSeek / Ollama(OpenAI 兼容端点)。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from loguru import logger

from app.llm.base import LLMError, LLMRequest, LLMResponse, Usage


class OpenAICompatProvider:
    """走 /chat/completions 的通用实现。

    OpenAI、DeepSeek、Ollama 都提供该端点，差异仅在 base_url 与鉴权。
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, req: LLMRequest, model: str, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": m.role.value, "content": m.content} for m in req.messages
            ],
            "temperature": req.temperature,
            "stream": stream,
        }
        if req.max_tokens:
            payload["max_tokens"] = req.max_tokens
        if req.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    url, headers=self._headers(), json=self._payload(req, model, False)
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"{self.name}: HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name}: malformed response: {exc}") from exc

        raw_usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            model=data.get("model", model),
            usage=Usage(
                prompt_tokens=raw_usage.get("prompt_tokens", 0) or 0,
                completion_tokens=raw_usage.get("completion_tokens", 0) or 0,
            ),
            raw=data,
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        url = f"{self.base_url}/chat/completions"
        payload = self._payload(req, model, True)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", url, headers=self._headers(), json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        raise LLMError(
                            f"{self.name}: HTTP {resp.status_code}: {body[:500]!r}"
                        )
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            delta = json.loads(chunk)["choices"][0].get("delta", {})
                        except (json.JSONDecodeError, KeyError, IndexError):
                            logger.warning("{}: skip bad chunk", self.name)
                            continue
                        piece = delta.get("content")
                        if piece:
                            yield piece
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: stream failed: {exc}") from exc
