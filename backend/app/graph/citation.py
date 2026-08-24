"""引用图谱抽象：Neo4j 实现 + 内存实现（测试/降级）。

图谱用途：
- 综述分簇组织（社区发现）
- 找核心文献（被引最多）
- 定位引用空白
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from app.config import settings


@dataclass
class GraphPaper:
    """图谱节点。key 是项目内唯一标识（优先 DOI，回退 paper_id）。"""

    key: str
    paper_id: str | None = None
    title: str | None = None
    year: int | None = None


@dataclass
class GraphStats:
    node_count: int = 0
    edge_count: int = 0


@dataclass
class CitationEdge:
    source_key: str
    target_key: str


@runtime_checkable
class CitationGraph(Protocol):
    async def add_paper(self, project_id: str, paper: GraphPaper) -> None: ...

    async def add_citations(
        self, project_id: str, source_key: str, target_keys: list[str]
    ) -> int: ...

    async def neighbors(self, project_id: str, key: str) -> list[str]: ...

    async def stats(self, project_id: str) -> GraphStats: ...

    async def most_cited(self, project_id: str, limit: int = 10) -> list[tuple[str, int]]: ...

    async def all_edges(self, project_id: str) -> list[CitationEdge]: ...

    async def clear_project(self, project_id: str) -> None: ...


class InMemoryCitationGraph:
    """进程内图谱。测试与小项目足够，无需 Neo4j。"""

    def __init__(self) -> None:
        self._papers: dict[str, dict[str, GraphPaper]] = {}
        self._edges: dict[str, set[tuple[str, str]]] = {}

    async def add_paper(self, project_id: str, paper: GraphPaper) -> None:
        papers = self._papers.setdefault(project_id, {})
        existing = papers.get(paper.key)
        if existing:
            # 补全缺失字段，不覆盖已有信息
            existing.paper_id = existing.paper_id or paper.paper_id
            existing.title = existing.title or paper.title
            existing.year = existing.year or paper.year
        else:
            papers[paper.key] = paper

    async def add_citations(
        self, project_id: str, source_key: str, target_keys: list[str]
    ) -> int:
        edges = self._edges.setdefault(project_id, set())
        before = len(edges)
        for target in target_keys:
            if target and target != source_key:
                edges.add((source_key, target))
                # 被引文献也作为节点存在（可能未在库中）
                await self.add_paper(project_id, GraphPaper(key=target))
        return len(edges) - before

    async def neighbors(self, project_id: str, key: str) -> list[str]:
        edges = self._edges.get(project_id, set())
        out = {t for s, t in edges if s == key}
        out |= {s for s, t in edges if t == key}
        return sorted(out)

    async def stats(self, project_id: str) -> GraphStats:
        return GraphStats(
            node_count=len(self._papers.get(project_id, {})),
            edge_count=len(self._edges.get(project_id, set())),
        )

    async def most_cited(self, project_id: str, limit: int = 10) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for _source, target in self._edges.get(project_id, set()):
            counts[target] = counts.get(target, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:limit]

    async def all_edges(self, project_id: str) -> list[CitationEdge]:
        return [
            CitationEdge(source_key=s, target_key=t)
            for s, t in sorted(self._edges.get(project_id, set()))
        ]

    async def clear_project(self, project_id: str) -> None:
        self._papers.pop(project_id, None)
        self._edges.pop(project_id, None)

    async def papers(self, project_id: str) -> list[GraphPaper]:
        return list(self._papers.get(project_id, {}).values())


class Neo4jCitationGraph:
    """Neo4j 实现。节点 (:Paper {project_id, key})，边 [:CITES]。"""

    def __init__(self, url: str, user: str, password: str) -> None:
        self.url = url
        self.user = user
        self.password = password
        self._driver: Any = None

    def _get_driver(self) -> Any:
        if self._driver is None:
            try:
                from neo4j import AsyncGraphDatabase
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("neo4j driver not installed") from exc
            self._driver = AsyncGraphDatabase.driver(
                self.url, auth=(self.user, self.password)
            )
        return self._driver

    async def ensure_constraints(self) -> None:
        """建唯一约束，兼作连通性检查。"""
        query = (
            "CREATE CONSTRAINT paper_key IF NOT EXISTS "
            "FOR (p:Paper) REQUIRE (p.project_id, p.key) IS UNIQUE"
        )
        async with self._get_driver().session() as session:
            await session.run(query)
        logger.info("neo4j constraints ensured")

    async def add_paper(self, project_id: str, paper: GraphPaper) -> None:
        query = """
        MERGE (p:Paper {project_id: $project_id, key: $key})
        ON CREATE SET p.paper_id = $paper_id, p.title = $title, p.year = $year
        ON MATCH SET p.paper_id = coalesce(p.paper_id, $paper_id),
                     p.title = coalesce(p.title, $title),
                     p.year = coalesce(p.year, $year)
        """
        async with self._get_driver().session() as session:
            await session.run(
                query,
                project_id=project_id,
                key=paper.key,
                paper_id=paper.paper_id,
                title=paper.title,
                year=paper.year,
            )

    async def add_citations(
        self, project_id: str, source_key: str, target_keys: list[str]
    ) -> int:
        targets = [t for t in target_keys if t and t != source_key]
        if not targets:
            return 0
        query = """
        MATCH (s:Paper {project_id: $project_id, key: $source_key})
        UNWIND $targets AS target
        MERGE (t:Paper {project_id: $project_id, key: target})
        MERGE (s)-[r:CITES]->(t)
        RETURN count(r) AS created
        """
        async with self._get_driver().session() as session:
            result = await session.run(
                query, project_id=project_id, source_key=source_key, targets=targets
            )
            record = await result.single()
        return int(record["created"]) if record else 0

    async def neighbors(self, project_id: str, key: str) -> list[str]:
        query = """
        MATCH (p:Paper {project_id: $project_id, key: $key})-[:CITES]-(n:Paper)
        RETURN DISTINCT n.key AS key ORDER BY key
        """
        async with self._get_driver().session() as session:
            result = await session.run(query, project_id=project_id, key=key)
            return [record["key"] async for record in result]

    async def stats(self, project_id: str) -> GraphStats:
        query = """
        MATCH (p:Paper {project_id: $project_id})
        WITH count(p) AS nodes
        OPTIONAL MATCH (:Paper {project_id: $project_id})-[r:CITES]->(:Paper {project_id: $project_id})
        RETURN nodes, count(r) AS edges
        """
        async with self._get_driver().session() as session:
            result = await session.run(query, project_id=project_id)
            record = await result.single()
        if not record:
            return GraphStats()
        return GraphStats(node_count=int(record["nodes"]), edge_count=int(record["edges"]))

    async def most_cited(self, project_id: str, limit: int = 10) -> list[tuple[str, int]]:
        query = """
        MATCH (:Paper {project_id: $project_id})-[:CITES]->(t:Paper {project_id: $project_id})
        RETURN t.key AS key, count(*) AS citations
        ORDER BY citations DESC, key ASC LIMIT $limit
        """
        async with self._get_driver().session() as session:
            result = await session.run(query, project_id=project_id, limit=limit)
            return [(record["key"], int(record["citations"])) async for record in result]

    async def all_edges(self, project_id: str) -> list[CitationEdge]:
        query = """
        MATCH (s:Paper {project_id: $project_id})-[:CITES]->(t:Paper {project_id: $project_id})
        RETURN s.key AS source, t.key AS target ORDER BY source, target
        """
        async with self._get_driver().session() as session:
            result = await session.run(query, project_id=project_id)
            return [
                CitationEdge(source_key=record["source"], target_key=record["target"])
                async for record in result
            ]

    async def clear_project(self, project_id: str) -> None:
        query = "MATCH (p:Paper {project_id: $project_id}) DETACH DELETE p"
        async with self._get_driver().session() as session:
            await session.run(query, project_id=project_id)

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None


_graph: CitationGraph | None = None


def get_citation_graph() -> CitationGraph:
    global _graph
    if _graph is None:
        _graph = Neo4jCitationGraph(
            settings.neo4j_url, settings.neo4j_user, settings.neo4j_password
        )
    return _graph


def set_citation_graph(graph: CitationGraph | None) -> None:
    """注入自定义图谱（测试用）。"""
    global _graph
    _graph = graph
