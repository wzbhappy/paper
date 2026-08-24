"""导出与引用一致性校验测试。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest

from app.services.export import (
    AI_DISCLOSURE,
    Manuscript,
    ManuscriptPart,
    Reference,
    check_citations,
    escape_latex,
    to_bibtex,
    to_docx_bytes,
    to_latex,
    to_markdown,
)

REFS = [
    Reference(
        paper_id="p1",
        title="Graph Neural Networks",
        authors="Alice Chen, Bob Smith",
        year=2022,
        venue="ICML",
        doi="10.1/gnn",
    ),
    Reference(paper_id="p2", title="Citation Analysis", authors="Carol Wang", year=2019),
]


def sample() -> Manuscript:
    return Manuscript(
        title="引文网络研究",
        parts=[
            ManuscriptPart("引言", 1, "已有研究表明该问题重要 [1]。", ["p1"]),
            # 第二节的 [1] 指向 p2，全局应为 [2]
            ManuscriptPart("相关工作", 1, "早期工作奠定基础 [1]。", ["p2"], ai_generated=True),
        ],
        references=REFS,
    )


# ---------- Markdown ----------


def test_markdown_renumbers_citations_globally():
    md = to_markdown(sample())
    intro = md.split("## 相关工作")[0]
    related = md.split("## 相关工作")[1].split("## 参考文献")[0]
    assert "[1]" in intro
    assert "[2]" in related


def test_markdown_includes_reference_list():
    md = to_markdown(sample())
    assert "## 参考文献" in md
    assert "[1] Alice Chen, Bob Smith. Graph Neural Networks. ICML. 2022. DOI: 10.1/gnn." in md
    assert "[2] Carol Wang. Citation Analysis. 2019." in md


def test_markdown_adds_ai_disclosure_when_needed():
    assert AI_DISCLOSURE in to_markdown(sample())


def test_markdown_omits_disclosure_without_ai_content():
    manuscript = sample()
    for part in manuscript.parts:
        part.ai_generated = False
    assert AI_DISCLOSURE not in to_markdown(manuscript)


def test_markdown_disclosure_can_be_disabled():
    assert AI_DISCLOSURE not in to_markdown(sample(), include_disclosure=False)


def test_markdown_heading_levels():
    manuscript = Manuscript(
        title="T",
        parts=[
            ManuscriptPart("章", 1, "内容"),
            ManuscriptPart("节", 2, "内容"),
            ManuscriptPart("小节", 3, "内容"),
        ],
    )
    md = to_markdown(manuscript)
    assert "## 章" in md
    assert "### 节" in md
    assert "#### 小节" in md


def test_markdown_empty_manuscript():
    md = to_markdown(Manuscript(title="空稿"))
    assert "# 空稿" in md
    assert "参考文献" not in md


def test_markdown_strips_unmappable_citation():
    # 节内声明只有 1 篇文献，[5] 无法映射，应被移除
    manuscript = Manuscript(
        title="T",
        parts=[ManuscriptPart("节", 1, "论断 [1] 与 [5]。", ["p1"])],
        references=[REFS[0]],
    )
    md = to_markdown(manuscript)
    assert "[1]" in md
    assert "[5]" not in md


# ---------- 引用校验 ----------


def test_check_citations_clean_manuscript():
    assert check_citations(sample()) == []


def test_check_citations_detects_out_of_range():
    manuscript = Manuscript(
        title="T",
        parts=[ManuscriptPart("节", 1, "论断 [3]。", ["p1"])],
        references=[REFS[0]],
    )
    issues = check_citations(manuscript)
    kinds = {i.kind for i in issues}
    assert "out_of_range" in kinds


def test_check_citations_detects_unused_reference():
    manuscript = Manuscript(
        title="T",
        parts=[ManuscriptPart("节", 1, "只引用第一篇 [1]。", ["p1"])],
        references=REFS,
    )
    issues = [i for i in check_citations(manuscript) if i.kind == "unused_reference"]
    assert len(issues) == 1
    assert "Citation Analysis" in issues[0].detail


def test_check_citations_flags_empty_chapter():
    manuscript = Manuscript(
        title="T", parts=[ManuscriptPart("空章节", 1, "   ")], references=[]
    )
    issues = check_citations(manuscript)
    assert any(i.kind == "empty_section" for i in issues)


def test_check_citations_ignores_empty_subsection():
    manuscript = Manuscript(
        title="T", parts=[ManuscriptPart("空子节", 3, "")], references=[]
    )
    assert check_citations(manuscript) == []


# ---------- LaTeX ----------


def test_escape_latex_handles_special_chars():
    assert escape_latex("a_b") == r"a\_b"
    assert escape_latex("100%") == r"100\%"
    assert escape_latex("a&b") == r"a\&b"
    assert escape_latex("$x$") == r"\$x\$"


def test_escape_latex_backslash_not_double_escaped():
    out = escape_latex("a\\b")
    assert out == r"a\textbackslash{}b"


def test_latex_has_compilable_skeleton():
    tex = to_latex(sample())
    assert r"\documentclass" in tex
    assert r"\begin{document}" in tex
    assert r"\end{document}" in tex
    assert tex.index(r"\begin{document}") < tex.index(r"\end{document}")


def test_latex_converts_citations_to_cite_commands():
    tex = to_latex(sample())
    assert "\\cite{chen2022graph}" in tex
    assert "\\cite{wang2019citation}" in tex
    # 不应残留 Markdown 风格引用
    assert "[1]" not in tex.split(r"\begin{thebibliography}")[0]


def test_latex_bibliography_entries():
    tex = to_latex(sample())
    assert r"\begin{thebibliography}" in tex
    assert r"\bibitem{chen2022graph}" in tex


def test_latex_section_commands_by_level():
    manuscript = Manuscript(
        title="T",
        parts=[
            ManuscriptPart("章", 1, "内容"),
            ManuscriptPart("节", 2, "内容"),
            ManuscriptPart("小节", 3, "内容"),
        ],
    )
    tex = to_latex(manuscript)
    assert r"\section{章}" in tex
    assert r"\subsection{节}" in tex
    assert r"\subsubsection{小节}" in tex


def test_latex_escapes_title_and_headings():
    manuscript = Manuscript(
        title="A_B & C", parts=[ManuscriptPart("50%_节", 1, "内容")]
    )
    tex = to_latex(manuscript)
    assert r"A\_B \& C" in tex
    assert r"50\%\_节" in tex


def test_latex_includes_disclosure():
    assert "人工智能辅助" in to_latex(sample())


# ---------- BibTeX ----------


def test_bibtex_entries_and_keys():
    bib = to_bibtex(sample())
    assert "@article{chen2022graph," in bib
    assert "author = {Alice Chen, Bob Smith}" in bib
    assert "doi = {10.1/gnn}" in bib


def test_bibtex_empty_references():
    assert to_bibtex(Manuscript(title="T")) == ""


def test_citation_key_handles_missing_fields():
    ref = Reference(paper_id="x", title=None, authors=None, year=None)
    assert ref.citation_key() == "unknownndUntitled".replace("Untitled", "untitled")


def test_citation_keys_differ_across_references():
    keys = {r.citation_key() for r in REFS}
    assert len(keys) == len(REFS)


# ---------- Word ----------


def test_docx_export_produces_valid_zip():
    data = to_docx_bytes(sample())
    # docx 是 zip 容器，magic number 为 PK
    assert data[:2] == b"PK"
    assert len(data) > 1000


def test_docx_contains_title_and_references():
    import io
    import zipfile

    data = to_docx_bytes(sample())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "引文网络研究" in xml
    assert "参考文献" in xml
    assert "Graph Neural Networks" in xml


def test_docx_empty_manuscript():
    data = to_docx_bytes(Manuscript(title="空稿"))
    assert data[:2] == b"PK"
