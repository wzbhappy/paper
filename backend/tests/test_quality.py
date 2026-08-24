"""质量检查规则测试。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

from app.services.export import Manuscript, ManuscriptPart, Reference
from app.services.quality import (
    check_citation_density,
    check_duplicates,
    check_format,
    check_language,
    check_manuscript,
    check_terminology,
    summarize_kinds,
)


def part(title: str, content: str, level: int = 1, paper_ids=None, ai=False):
    return ManuscriptPart(
        title=title,
        level=level,
        content=content,
        paper_ids=paper_ids or [],
        ai_generated=ai,
    )


# ---------- 术语一致性 ----------


def test_terminology_detects_mixed_usage():
    parts = [part("方法", "我们用图神经网络建模，随后 graph neural network 被扩展。")]
    issues = check_terminology(parts)
    assert issues
    assert issues[0].kind == "term_inconsistency"
    assert "图神经网络" in issues[0].suggestion


def test_terminology_clean_when_consistent():
    parts = [part("方法", "本文使用图神经网络进行建模，图神经网络具有良好表达能力。")]
    assert check_terminology(parts) == []


def test_terminology_checks_across_sections():
    parts = [
        part("第一节", "使用图神经网络。"),
        part("第二节", "采用 GNN 方法。"),
    ]
    assert check_terminology(parts)


# ---------- 重复句 ----------


def test_duplicates_detected_across_sections():
    sentence = "本文提出的两阶段编码器在稀疏监督场景下具有显著优势。"
    parts = [part("第一节", sentence), part("第二节", sentence)]
    issues = check_duplicates(parts)
    assert len(issues) == 1
    assert issues[0].kind == "duplicate_sentence"


def test_duplicates_ignores_short_sentences():
    parts = [part("A", "很好。"), part("B", "很好。")]
    assert check_duplicates(parts) == []


def test_duplicates_ignores_citation_differences():
    """只有引用编号不同的相同句子仍应视为重复。"""
    parts = [
        part("A", "该方法在三个基准数据集上均取得了显著的性能提升 [1]。"),
        part("B", "该方法在三个基准数据集上均取得了显著的性能提升 [2]。"),
    ]
    assert check_duplicates(parts)


def test_duplicates_clean_when_distinct():
    parts = [
        part("A", "第一节讨论了图神经网络在引文分析中的应用与局限性问题。"),
        part("B", "第二节转向对比学习的预训练策略及其收敛性质分析。"),
    ]
    assert check_duplicates(parts) == []


# ---------- 语言 ----------


def test_language_detects_informal_expressions():
    parts = [part("讨论", "这个方法真的很不错，我们觉得效果挺好。")]
    issues = check_language(parts)
    kinds = {i.kind for i in issues}
    assert "informal_language" in kinds


def test_language_detects_long_sentence():
    long_text = "本文" + "详细论述该方法的设计动机与实现细节" * 12 + "。"
    issues = check_language([part("方法", long_text)])
    assert any(i.kind == "long_sentence" for i in issues)


def test_language_clean_for_academic_text():
    parts = [part("方法", "本文提出一种两阶段编码器。实验结果表明其有效。")]
    assert check_language(parts) == []


def test_language_skips_empty_sections():
    assert check_language([part("空节", "   ")]) == []


# ---------- 格式 ----------


def test_format_detects_heading_jump():
    parts = [part("章", "内容", level=1), part("小小节", "内容", level=3)]
    issues = check_format(parts)
    assert any(i.kind == "heading_jump" for i in issues)


def test_format_allows_sequential_levels():
    parts = [
        part("章", "内容", level=1),
        part("节", "内容", level=2),
        part("小节", "内容", level=3),
    ]
    assert not any(i.kind == "heading_jump" for i in check_format(parts))


def test_format_detects_figure_numbering_gap():
    parts = [part("实验", "如图 1 所示，另见图 3 的对比结果。")]
    issues = check_format(parts)
    gap = next(i for i in issues if i.kind == "numbering_gap")
    assert "2" in gap.detail


def test_format_detects_table_numbering_start():
    parts = [part("实验", "表 2 给出了主要结果。表 3 是消融实验。")]
    issues = check_format(parts)
    assert any(i.kind == "numbering_start" for i in issues)


def test_format_clean_with_sequential_figures():
    parts = [part("实验", "见图 1、图 2 与图 3。")]
    assert not any(
        i.kind in ("numbering_gap", "numbering_start") for i in check_format(parts)
    )


# ---------- 引用密度 ----------


def test_citation_density_flags_related_work_without_citations():
    parts = [part("相关工作", "已有大量研究探讨了该问题的不同侧面。")]
    issues = check_citation_density(parts)
    assert issues
    assert issues[0].kind == "missing_citation"
    assert issues[0].severity == "error"


def test_citation_density_ok_with_citations():
    parts = [part("相关工作", "已有研究探讨了该问题 [1]。", paper_ids=["p1"])]
    assert check_citation_density(parts) == []


def test_citation_density_ignores_method_section():
    parts = [part("方法", "本文提出一种新的编码器结构。")]
    assert check_citation_density(parts) == []


def test_citation_density_skips_empty():
    assert check_citation_density([part("引言", "")]) == []


# ---------- 汇总 ----------


def test_check_manuscript_aggregates_all_rules():
    manuscript = Manuscript(
        title="测试稿件",
        parts=[
            part("引言", "已有研究讨论了这个问题，我们觉得挺好。", level=1),
            part("方法", "本文使用图神经网络。又提到 GNN 方法。", level=1, ai=True),
            part("空章节", "", level=1),
        ],
        references=[Reference(paper_id="p1", title="未被引用的文献")],
    )
    report = check_manuscript(manuscript)
    kinds = summarize_kinds(report)

    assert report.section_count == 3
    assert report.empty_sections == 1
    assert report.ai_generated_sections == 1
    assert "missing_citation" in kinds
    assert "informal_language" in kinds
    assert "term_inconsistency" in kinds
    assert "unused_reference" in kinds
    assert "empty_section" in kinds
    assert report.error_count >= 1


def test_check_manuscript_clean_document():
    manuscript = Manuscript(
        title="干净稿件",
        parts=[
            part("引言", "已有研究表明该问题具有重要价值 [1]。", level=1, paper_ids=["p1"]),
            part("方法", "本文提出一种两阶段编码器结构。", level=1),
        ],
        references=[Reference(paper_id="p1", title="被引用的文献")],
    )
    report = check_manuscript(manuscript)
    assert report.error_count == 0


def test_sorted_issues_puts_errors_first():
    manuscript = Manuscript(
        title="T",
        parts=[
            part("引言", "讨论已有研究但无引用。", level=1),
            part("方法", "本文" + "论述细节内容" * 25 + "。", level=1),
        ],
    )
    report = check_manuscript(manuscript)
    severities = [i.severity for i in report.sorted_issues()]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warning": 1, "info": 2}[s])


def test_empty_manuscript_report():
    report = check_manuscript(Manuscript(title="空"))
    assert report.section_count == 0
    assert report.word_count == 0
    assert report.issues == []
