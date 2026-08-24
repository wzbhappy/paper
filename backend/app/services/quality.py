"""稿件质量检查：术语一致性、格式、重复片段、语言问题。

全部为确定性规则检查，不调用 LLM——规则检查可解释、可重复、零成本，
适合作为写作过程中的高频反馈。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.services.export import Manuscript, ManuscriptPart
from app.services.review import CITATION_RE

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

# 中英混排时常见的术语不一致：同一概念的不同写法
TERM_VARIANTS: list[tuple[str, list[str]]] = [
    ("图神经网络", ["图神经网路", "graph neural network", "gnn"]),
    ("数据集", ["数据集合", "dataset"]),
    ("准确率", ["精确率", "accuracy"]),
]

# 学术写作中应避免的口语化表达
INFORMAL_PATTERNS = [
    (r"很棒|挺好|超级|特别棒", "口语化评价，建议改为客观表述"),
    (r"我们觉得|我感觉|我认为", "第一人称主观表述，建议改为「本文认为」或客观陈述"),
    (r"非常非常|真的很", "程度副词堆叠"),
    (r"等等等|。。。", "标点不规范"),
]

SENTENCE_SPLIT = re.compile(r"(?<=[。！？；.!?])\s*")
MAX_SENTENCE_CHARS = 120
MIN_DUP_SENTENCE_CHARS = 20


@dataclass
class QualityIssue:
    section: str
    kind: str
    detail: str
    severity: str = "warning"
    suggestion: str | None = None


@dataclass
class QualityReport:
    issues: list[QualityIssue] = field(default_factory=list)
    word_count: int = 0
    section_count: int = 0
    empty_sections: int = 0
    reference_count: int = 0
    ai_generated_sections: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def sorted_issues(self) -> list[QualityIssue]:
        return sorted(
            self.issues, key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.section)
        )


def _normalize_sentence(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", text))


def check_terminology(parts: list[ManuscriptPart]) -> list[QualityIssue]:
    """检查术语一致性：同一概念混用多种写法时告警。"""
    issues: list[QualityIssue] = []
    full_text = "\n".join(p.content for p in parts).lower()

    for canonical, variants in TERM_VARIANTS:
        present = [v for v in variants if v.lower() in full_text]
        canonical_present = canonical.lower() in full_text
        # 同时出现规范写法与变体，或出现两种以上变体
        if (canonical_present and present) or len(present) >= 2:
            used = ([canonical] if canonical_present else []) + present
            issues.append(
                QualityIssue(
                    section="全文",
                    kind="term_inconsistency",
                    detail=f"同一概念存在多种写法：{'、'.join(used)}",
                    severity="warning",
                    suggestion=f"建议统一为「{canonical}」",
                )
            )
    return issues


def check_duplicates(parts: list[ManuscriptPart]) -> list[QualityIssue]:
    """检查重复句：跨章节出现完全相同的长句通常是复制粘贴遗留。"""
    issues: list[QualityIssue] = []
    occurrences: dict[str, list[str]] = defaultdict(list)

    for part in parts:
        for sentence in SENTENCE_SPLIT.split(part.content):
            normalized = _normalize_sentence(sentence)
            if len(normalized) >= MIN_DUP_SENTENCE_CHARS:
                occurrences[normalized].append(part.title)

    for normalized, sections in occurrences.items():
        if len(sections) > 1:
            preview = normalized[:40]
            issues.append(
                QualityIssue(
                    section="、".join(dict.fromkeys(sections)),
                    kind="duplicate_sentence",
                    detail=f"重复出现的句子：「{preview}…」",
                    severity="warning",
                    suggestion="检查是否为复制粘贴遗留，或改写其中一处",
                )
            )
    return issues


def check_language(parts: list[ManuscriptPart]) -> list[QualityIssue]:
    """检查语言问题：口语化表达、超长句。"""
    issues: list[QualityIssue] = []

    for part in parts:
        if not part.content.strip():
            continue

        for pattern, description in INFORMAL_PATTERNS:
            match = re.search(pattern, part.content)
            if match:
                issues.append(
                    QualityIssue(
                        section=part.title,
                        kind="informal_language",
                        detail=f"{description}：「{match.group(0)}」",
                        severity="warning",
                    )
                )

        long_sentences = [
            s
            for s in SENTENCE_SPLIT.split(part.content)
            if len(_normalize_sentence(s)) > MAX_SENTENCE_CHARS
        ]
        if long_sentences:
            issues.append(
                QualityIssue(
                    section=part.title,
                    kind="long_sentence",
                    detail=f"存在 {len(long_sentences)} 个超过 {MAX_SENTENCE_CHARS} 字的长句",
                    severity="info",
                    suggestion="建议拆分以提升可读性",
                )
            )
    return issues


def check_format(parts: list[ManuscriptPart]) -> list[QualityIssue]:
    """检查格式：标题层级跳跃、图表编号连续性。"""
    issues: list[QualityIssue] = []

    previous_level = 0
    for part in parts:
        # 层级不能从 1 直接跳到 3
        if previous_level and part.level > previous_level + 1:
            issues.append(
                QualityIssue(
                    section=part.title,
                    kind="heading_jump",
                    detail=f"标题层级从 {previous_level} 跳到 {part.level}",
                    severity="warning",
                    suggestion="补充中间层级或调整该节层级",
                )
            )
        previous_level = part.level

    full_text = "\n".join(p.content for p in parts)
    for label, pattern in (("图", r"图\s*(\d+)"), ("表", r"表\s*(\d+)")):
        numbers = sorted({int(m) for m in re.findall(pattern, full_text)})
        if not numbers:
            continue
        if numbers[0] != 1:
            issues.append(
                QualityIssue(
                    section="全文",
                    kind="numbering_start",
                    detail=f"{label}编号从 {numbers[0]} 开始",
                    severity="info",
                    suggestion=f"{label}编号通常从 1 开始",
                )
            )
        missing = [n for n in range(numbers[0], numbers[-1] + 1) if n not in numbers]
        if missing:
            issues.append(
                QualityIssue(
                    section="全文",
                    kind="numbering_gap",
                    detail=f"{label}编号不连续，缺少 {missing}",
                    severity="warning",
                )
            )
    return issues


def check_citation_density(parts: list[ManuscriptPart]) -> list[QualityIssue]:
    """检查引用密度：相关工作类章节缺引用是明显问题。"""
    issues: list[QualityIssue] = []
    citation_expected = ("相关工作", "研究现状", "文献综述", "背景", "引言", "绪论")

    for part in parts:
        content = part.content.strip()
        if not content:
            continue
        citation_count = len(CITATION_RE.findall(content))
        needs_citation = any(key in part.title for key in citation_expected)
        if needs_citation and citation_count == 0:
            issues.append(
                QualityIssue(
                    section=part.title,
                    kind="missing_citation",
                    detail="该章节讨论已有研究但没有任何引用",
                    severity="error",
                    suggestion="补充文献引用以支撑论断",
                )
            )
    return issues


def check_manuscript(manuscript: Manuscript) -> QualityReport:
    """运行全部规则检查，汇总成报告。"""
    from app.services.export import check_citations

    parts = manuscript.parts
    report = QualityReport(
        word_count=manuscript.word_count,
        section_count=len(parts),
        empty_sections=sum(1 for p in parts if not p.content.strip()),
        reference_count=len(manuscript.references),
        ai_generated_sections=sum(1 for p in parts if p.ai_generated),
    )

    # 引用一致性（复用导出模块的实现）
    for issue in check_citations(manuscript):
        severity = "error" if issue.kind == "out_of_range" else "warning"
        report.issues.append(
            QualityIssue(
                section=issue.section,
                kind=issue.kind,
                detail=issue.detail,
                severity=severity,
            )
        )

    report.issues.extend(check_citation_density(parts))
    report.issues.extend(check_terminology(parts))
    report.issues.extend(check_duplicates(parts))
    report.issues.extend(check_language(parts))
    report.issues.extend(check_format(parts))
    return report


def summarize_kinds(report: QualityReport) -> dict[str, int]:
    """按问题类型统计，便于前端分组展示。"""
    return dict(Counter(issue.kind for issue in report.issues))
