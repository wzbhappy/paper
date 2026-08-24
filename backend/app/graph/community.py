"""社区发现：标签传播算法（LPA）+ 连通分量兜底。

纯 Python 实现，避免引入 networkx/igraph。LPA 比 Louvain 简单且对
文献引用网络这种稀疏图效果足够；用确定性的迭代顺序保证结果可重复。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Community:
    """一个引用簇，对应综述的一个小节。"""

    id: int
    members: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)


def build_adjacency(
    edges: list[tuple[str, str]], nodes: list[str] | None = None
) -> dict[str, set[str]]:
    """构建无向邻接表。引用方向对主题聚类不重要，按无向处理。"""
    adjacency: dict[str, set[str]] = {n: set() for n in (nodes or [])}
    for source, target in edges:
        if source == target:
            continue
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
    return adjacency


def connected_components(adjacency: dict[str, set[str]]) -> list[list[str]]:
    """连通分量。用于边稀疏时的兜底分组。"""
    seen: set[str] = set()
    components: list[list[str]] = []

    for node in sorted(adjacency):
        if node in seen:
            continue
        stack = [node]
        component: list[str] = []
        seen.add(node)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))

    components.sort(key=lambda c: (-len(c), c[0] if c else ""))
    return components


def label_propagation(
    adjacency: dict[str, set[str]], iterations: int = 20
) -> list[Community]:
    """标签传播。每个节点迭代取邻居中最常见的标签，平票时取字典序最小。"""
    if not adjacency:
        return []

    labels = {node: node for node in adjacency}
    order = sorted(adjacency)

    for _ in range(iterations):
        changed = False
        for node in order:
            neighbors = adjacency.get(node, set())
            if not neighbors:
                continue
            counts: dict[str, int] = {}
            for neighbor in neighbors:
                label = labels[neighbor]
                counts[label] = counts.get(label, 0) + 1
            # 票数优先，平票取字典序最小，保证确定性
            best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if labels[node] != best:
                labels[node] = best
                changed = True
        if not changed:
            break

    grouped: dict[str, list[str]] = {}
    for node, label in labels.items():
        grouped.setdefault(label, []).append(node)

    communities = [
        Community(id=0, members=sorted(members)) for members in grouped.values()
    ]
    communities.sort(key=lambda c: (-c.size, c.members[0]))
    for index, community in enumerate(communities):
        community.id = index
    return communities


def detect_communities(
    edges: list[tuple[str, str]],
    nodes: list[str] | None = None,
    min_edges_for_lpa: int = 3,
) -> list[Community]:
    """检测引用簇。

    边太少时 LPA 退化成「每个节点自成一簇」，此时改用连通分量，
    孤立节点合并为一个「未连接」簇，避免产生大量单元素簇。
    """
    adjacency = build_adjacency(edges, nodes)
    if not adjacency:
        return []

    if len(edges) < min_edges_for_lpa:
        components = connected_components(adjacency)
        multi = [c for c in components if len(c) > 1]
        isolated = [c[0] for c in components if len(c) == 1]
        result = [Community(id=i, members=c) for i, c in enumerate(multi)]
        if isolated:
            result.append(Community(id=len(result), members=sorted(isolated)))
        return result

    return label_propagation(adjacency)
