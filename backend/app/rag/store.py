"""向量库抽象：Qdrant 实现 + 内存实现（测试/无依赖场景）。

统一接口让业务层不感知底层实现，测试无需起 Qdrant 容器。
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from app.config import settings


@dataclass
class VectorRecord:
    """入库单元：一个文献片段 + 其向量。"""

    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    async def ensure_collection(self, dim: int) -> None: ...

    async def upsert(self, records: list[VectorRecord]) -> None: ...

    async def search(
        self,
        vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]: ...

    async def delete_by_filter(self, filters: dict[str, Any]) -> None: ...

    async def count(self, filters: dict[str, Any] | None = None) -> int: ...


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _matches(payload: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = payload.get(key)
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


class InMemoryVectorStore:
    """进程内向量库。测试与小规模本地使用足够。"""

    def __init__(self) -> None:
        self._data: dict[str, VectorRecord] = {}
        self.dim: int | None = None

    async def ensure_collection(self, dim: int) -> None:
        self.dim = dim

    async def upsert(self, records: list[VectorRecord]) -> None:
        for record in records:
            self._data[record.id] = record

    async def search(
        self,
        vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        hits = [
            SearchHit(
                id=record.id,
                score=_cosine(vector, record.vector),
                payload=record.payload,
            )
            for record in self._data.values()
            if _matches(record.payload, filters)
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    async def delete_by_filter(self, filters: dict[str, Any]) -> None:
        doomed = [
            key for key, rec in self._data.items() if _matches(rec.payload, filters)
        ]
        for key in doomed:
            del self._data[key]

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        return sum(1 for rec in self._data.values() if _matches(rec.payload, filters))

    async def all_records(self, filters: dict[str, Any] | None = None) -> list[VectorRecord]:
        return [rec for rec in self._data.values() if _matches(rec.payload, filters)]


class QdrantVectorStore:
    """Qdrant 实现。延迟导入 client，缺依赖时给出明确错误。"""

    def __init__(self, url: str, collection: str) -> None:
        self.url = url
        self.collection = collection
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from qdrant_client import AsyncQdrantClient
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("qdrant-client not installed") from exc
            self._client = AsyncQdrantClient(url=self.url)
        return self._client

    async def ensure_collection(self, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        client = self._get_client()
        existing = await client.get_collections()
        names = {c.name for c in existing.collections}
        if self.collection in names:
            return
        await client.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        # project_id 是最常用过滤字段，建索引避免全量扫描
        try:
            from qdrant_client.models import PayloadSchemaType

            await client.create_payload_index(
                collection_name=self.collection,
                field_name="project_id",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("create payload index failed: {}", exc)
        logger.info("qdrant collection {} created (dim={})", self.collection, dim)

    @staticmethod
    def _build_filter(filters: dict[str, Any] | None) -> Any:
        if not filters:
            return None
        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

        conditions = []
        for key, expected in filters.items():
            if isinstance(expected, (list, tuple, set)):
                conditions.append(
                    FieldCondition(key=key, match=MatchAny(any=list(expected)))
                )
            else:
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=expected))
                )
        return Filter(must=conditions)

    async def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(id=r.id, vector=r.vector, payload=r.payload) for r in records
        ]
        await self._get_client().upsert(
            collection_name=self.collection, points=points, wait=True
        )

    async def search(
        self,
        vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        result = await self._get_client().search(
            collection_name=self.collection,
            query_vector=vector,
            limit=limit,
            query_filter=self._build_filter(filters),
            with_payload=True,
        )
        return [
            SearchHit(id=str(p.id), score=float(p.score), payload=p.payload or {})
            for p in result
        ]

    async def delete_by_filter(self, filters: dict[str, Any]) -> None:
        from qdrant_client.models import FilterSelector

        await self._get_client().delete(
            collection_name=self.collection,
            points_selector=FilterSelector(filter=self._build_filter(filters)),
            wait=True,
        )

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        result = await self._get_client().count(
            collection_name=self.collection,
            count_filter=self._build_filter(filters),
            exact=True,
        )
        return int(result.count)


def make_point_id(project_id: str, paper_id: str, chunk_index: int) -> str:
    """确定性 point id：重复入库同一片段会覆盖而非产生重复。"""
    name = f"{project_id}:{paper_id}:{chunk_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection)
    return _store


def set_vector_store(store: VectorStore | None) -> None:
    """注入自定义 store（测试用）。"""
    global _store
    _store = store
