"""PDF 解析流水线测试：切分器与元数据抽取用纯文本，PDF 提取用生成的真实 PDF。"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.parser import extract_metadata, split_markdown
from app.parser.chunker import _approx_tokens
from app.parser.pdf import ParseError, extract_markdown

SAMPLE_MD = """# Deep Learning for Citation Graphs

Alice Chen, Bob Smith, Carol Wang

## Abstract

We study citation graph representation learning under sparse supervision.
Our method improves accuracy on three benchmarks. arXiv:2401.01234

## 1 Introduction

Citation networks are widely used. Prior work focused on dense settings.

## 2 Method

We propose a two-stage encoder. The first stage encodes local structure.

The second stage aggregates global signals. doi:10.1145/3394486.3403087
"""


def test_split_markdown_tracks_section_path():
    chunks = split_markdown(SAMPLE_MD, max_tokens=100, min_chars=20)
    assert chunks
    sections = {c.section for c in chunks}
    assert any("Method" in s for s in sections)
    assert any("Introduction" in s for s in sections)
    # 章节路径应包含顶层标题
    assert all(s.startswith("Deep Learning for Citation Graphs") for s in sections if s)


def test_split_markdown_indexes_are_sequential():
    chunks = split_markdown(SAMPLE_MD, max_tokens=50, min_chars=10)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_split_markdown_respects_token_budget():
    long_md = "# T\n\n" + "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(20))
    chunks = split_markdown(long_md, max_tokens=120, overlap_tokens=20)
    assert len(chunks) > 1
    # 允许少量超出（重叠与单段不可分割导致）
    assert all(_approx_tokens(c.text) <= 200 for c in chunks)


def test_split_markdown_splits_oversized_paragraph():
    huge = "# T\n\n" + "This is a sentence. " * 400
    chunks = split_markdown(huge, max_tokens=100)
    assert len(chunks) > 1


def test_split_markdown_empty_input():
    assert split_markdown("") == []
    assert split_markdown("   \n  \n ") == []


def test_split_markdown_drops_tiny_chunks():
    chunks = split_markdown("# A\n\nhi\n\n## B\n\nyo", min_chars=40)
    assert chunks == []


def test_extract_metadata_from_markdown():
    meta = extract_metadata(SAMPLE_MD)
    assert meta.title == "Deep Learning for Citation Graphs"
    assert meta.authors is not None
    assert "Alice Chen" in meta.authors
    assert meta.abstract is not None
    assert "citation graph representation" in meta.abstract
    assert meta.arxiv_id == "2401.01234"
    assert meta.doi == "10.1145/3394486.3403087"


def test_extract_metadata_prefers_embedded_title():
    meta = extract_metadata(SAMPLE_MD, {"title": "A Much Better Embedded Title"})
    assert meta.title == "A Much Better Embedded Title"


def test_extract_metadata_ignores_filename_title():
    meta = extract_metadata(SAMPLE_MD, {"title": "paper_final_v3.pdf"})
    assert meta.title == "Deep Learning for Citation Graphs"


def test_extract_metadata_chinese_abstract():
    md = "# 图神经网络研究\n\n张三, 李四\n\n## 摘要\n\n" + "本文研究图神经网络在稀疏监督下的表示学习问题。" * 3
    meta = extract_metadata(md)
    assert meta.title == "图神经网络研究"
    assert meta.abstract is not None
    assert "图神经网络" in meta.abstract


def test_extract_metadata_handles_missing_fields():
    meta = extract_metadata("short")
    assert meta.abstract is None
    assert meta.doi is None


def test_extract_markdown_missing_file():
    with pytest.raises(ParseError):
        extract_markdown("does_not_exist_12345.pdf")


def _make_pdf(path: Path) -> bool:
    """用 pymupdf 生成一个带大小标题的测试 PDF；不可用则跳过。"""
    try:
        import fitz
    except ImportError:
        return False

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Graph Neural Network Survey", fontsize=22)
    page.insert_text((72, 140), "Alice Chen, Bob Smith", fontsize=11)
    page.insert_text((72, 180), "Abstract", fontsize=15)
    body = (
        "We survey graph neural networks and their applications to citation "
        "analysis under sparse supervision settings."
    )
    page.insert_text((72, 210), body, fontsize=11)
    doc.save(path)
    doc.close()
    return True


def test_extract_markdown_from_real_pdf(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    if not _make_pdf(pdf_path):
        pytest.skip("pymupdf not installed")

    parsed = extract_markdown(pdf_path, prefer_marker=False)
    assert parsed.backend == "pymupdf"
    assert parsed.page_count == 1
    assert "Graph Neural Network Survey" in parsed.markdown
    # 大字号标题应被识别为 Markdown 标题
    assert re.search(r"#+\s+Graph Neural Network Survey", parsed.markdown)


def test_pipeline_pdf_to_chunks(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    if not _make_pdf(pdf_path):
        pytest.skip("pymupdf not installed")

    parsed = extract_markdown(pdf_path, prefer_marker=False)
    meta = extract_metadata(parsed.markdown, parsed.metadata)
    chunks = split_markdown(parsed.markdown, max_tokens=200, min_chars=20)

    assert meta.title is not None
    assert chunks
    assert all(c.text.strip() for c in chunks)
