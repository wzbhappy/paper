"""轻量主题聚类：纯 Python k-means（余弦距离），避免引入 sklearn/numpy 重依赖。

用于把项目文献按语义分成若干主题簇，为方向生成提供「主题分布」上下文。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class Cluster:
    """一个主题簇。"""

    id: int
    member_indices: list[int] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)
    label: str = ""


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _mean(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for vec in vectors:
        for i in range(dim):
            out[i] += vec[i]
    n = len(vectors)
    return [v / n for v in out]


def suggest_k(n_items: int, max_k: int = 6) -> int:
    """样本少时不宜分太多簇：k ≈ sqrt(n/2)，并限制上下界。"""
    if n_items <= 2:
        return 1
    k = max(2, int(math.sqrt(n_items / 2)))
    return min(k, max_k, n_items)


def kmeans(
    vectors: list[list[float]],
    k: int | None = None,
    iterations: int = 25,
    seed: int = 42,
) -> list[Cluster]:
    """余弦距离 k-means。返回非空簇列表。

    用 k-means++ 风格的最远点初始化，结果比随机初始化稳定。
    """
    if not vectors:
        return []

    n = len(vectors)
    k = k or suggest_k(n)
    k = max(1, min(k, n))

    if k == 1:
        return [Cluster(id=0, member_indices=list(range(n)), centroid=_mean(vectors))]

    rng = random.Random(seed)
    # 初始化：首个随机，其余选与已有中心最不相似的点
    first = rng.randrange(n)
    centroids = [list(vectors[first])]
    while len(centroids) < k:
        best_idx, best_dist = None, -1.0
        for i, vec in enumerate(vectors):
            dist = 1.0 - max(_cosine(vec, c) for c in centroids)
            if dist > best_dist:
                best_idx, best_dist = i, dist
        if best_idx is None:
            break
        centroids.append(list(vectors[best_idx]))

    assignments = [0] * n
    for _ in range(iterations):
        changed = False
        for i, vec in enumerate(vectors):
            sims = [_cosine(vec, c) for c in centroids]
            best = max(range(len(centroids)), key=lambda j: sims[j])
            if assignments[i] != best:
                assignments[i] = best
                changed = True

        for j in range(len(centroids)):
            members = [vectors[i] for i in range(n) if assignments[i] == j]
            if members:
                centroids[j] = _mean(members)

        if not changed:
            break

    clusters: list[Cluster] = []
    for j in range(len(centroids)):
        members = [i for i in range(n) if assignments[i] == j]
        if not members:
            continue
        clusters.append(
            Cluster(
                id=len(clusters),
                member_indices=members,
                centroid=centroids[j],
            )
        )
    # 大簇优先，便于展示
    clusters.sort(key=lambda c: len(c.member_indices), reverse=True)
    for new_id, cluster in enumerate(clusters):
        cluster.id = new_id
    return clusters


def label_clusters_by_keywords(
    clusters: list[Cluster], keyword_lists: list[list[str]], top_n: int = 3
) -> None:
    """用簇内高频关键词给簇命名（就地修改 cluster.label）。

    keyword_lists 按文献索引对齐，元素是该文献的关键术语列表。
    """
    for cluster in clusters:
        counts: dict[str, int] = {}
        for idx in cluster.member_indices:
            if idx >= len(keyword_lists):
                continue
            for term in keyword_lists[idx]:
                key = term.strip()
                if key:
                    counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
        cluster.label = " / ".join(term for term, _ in top)
