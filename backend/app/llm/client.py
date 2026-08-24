"""LLM 客户端：任务路由 + 缓存 + usage 记账 + JSON 解析。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.llm.base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    TaskType,
)
from app.llm.providers import OpenAICompatProvider

T = TypeVar("T", bound=BaseModel)

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> str:
    """从模型输出里剥出 JSON：优先去掉 ``` 围栏，其次截取首尾大括号。"""
    match = _JSON_FENCE.search(text)
    if match:
        return match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


class LLMClient:
    """统一入口。所有业务代码通过它调用模型，不直接碰 provider。"""

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._cache: dict[str, LLMResponse] = {}
        self.usage_log: list[dict[str, Any]] = []
        self._register_providers()

    def _register_providers(self) -> None:
        if settings.openai_api_key:
            self._providers["openai"] = OpenAICompatProvider(
                "openai", settings.openai_base_url, settings.openai_api_key
            )
        if settings.deepseek_api_key:
            self._providers["deepseek"] = OpenAICompatProvider(
                "deepseek", settings.deepseek_base_url, settings.deepseek_api_key
            )
        # Ollama 本地无需 key，始终注册，作为零成本兜底。
        self._providers["ollama"] = OpenAICompatProvider(
            "ollama", f"{settings.ollama_base_url.rstrip('/')}/v1", None
        )
        logger.info("LLM providers registered: {}", sorted(self._providers))

    def register(self, name: str, provider: LLMProvider) -> None:
        """注入自定义 provider（测试或扩展用）。"""
        self._providers[name] = provider

    def resolve_model(self, task: TaskType, override: str | None = None) -> str:
        if override:
            return override
        return settings.llm_routing.get(task.value, settings.llm_default)

    def _provider_for(self, model: str) -> LLMProvider:
        """按模型名前缀选 provider。"""
        prefix_map = {
            "gpt-": "openai",
            "o1": "openai",
            "o3": "openai",
            "deepseek": "deepseek",
        }
        for prefix, name in prefix_map.items():
            if model.startswith(prefix) and name in self._providers:
                return self._providers[name]
        # 未匹配到云端模型时交给 Ollama（本地模型名任意）。
        if "ollama" in self._providers:
            return self._providers["ollama"]
        raise LLMError(f"no provider available for model {model!r}")

    @staticmethod
    def _cache_key(req: LLMRequest, model: str) -> str:
        payload = json.dumps(
            {
                "model": model,
                "messages": [[m.role.value, m.content] for m in req.messages],
                "temperature": req.temperature,
                "json_mode": req.json_mode,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def complete(self, req: LLMRequest, use_cache: bool = True) -> LLMResponse:
        model = self.resolve_model(req.task, req.model)
        key = self._cache_key(req, model)
        if use_cache and key in self._cache:
            logger.debug("LLM cache hit task={} model={}", req.task.value, model)
            return self._cache[key]

        provider = self._provider_for(model)
        resp = await provider.complete(req, model)

        self.usage_log.append(
            {
                "task": req.task.value,
                "model": resp.model,
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
            }
        )
        logger.info(
            "LLM done task={} model={} tokens={}",
            req.task.value,
            resp.model,
            resp.usage.total_tokens,
        )
        if use_cache:
            self._cache[key] = resp
        return resp

    def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        model = self.resolve_model(req.task, req.model)
        return self._provider_for(model).stream(req, model)

    async def complete_json(
        self,
        req: LLMRequest,
        schema: type[T],
        retries: int = 1,
    ) -> T:
        """要求模型返回 JSON 并校验到 pydantic 模型；失败会重试并附上错误。"""
        req = req.model_copy(update={"json_mode": True})
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            resp = await self.complete(req, use_cache=attempt == 0)
            try:
                return schema.model_validate_json(_extract_json(resp.content))
            except (ValidationError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "LLM JSON validation failed (attempt {}/{}): {}",
                    attempt + 1,
                    retries + 1,
                    exc,
                )
                if attempt < retries:
                    req = req.model_copy(
                        update={
                            "messages": [
                                *req.messages,
                                Message(role=Role.ASSISTANT, content=resp.content),
                                Message(
                                    role=Role.USER,
                                    content=(
                                        "上次输出不符合要求的 JSON 结构，错误："
                                        f"{exc}\n请只输出合法 JSON，不要任何解释或代码围栏。"
                                    ),
                                ),
                            ]
                        }
                    )

        raise LLMError(f"failed to obtain valid JSON after {retries + 1} attempts: {last_error}")


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """进程内单例，避免重复构建 provider。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def set_llm(client: LLMClient | None) -> None:
    """注入自定义 client（测试用）。"""
    global _client
    _client = client
