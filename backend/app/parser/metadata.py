"""从 PDF/Markdown 首部启发式抽取题目、作者、摘要、DOI、年份。

这是零成本的兜底方案；LLM 摘要阶段会用更可靠的方式补全缺失字段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
ABSTRACT_RE = re.compile(
    r"^\s*#{0,6}\s*(abstract|摘\s*要)\b[:：]?\s*", re.IGNORECASE | re.MULTILINE
)
# 作者行首字符特征：大写字母或中日韩字符。配合分隔符与长度判断使用。
AUTHOR_HINT = re.compile(r"^[A-Z\u4e00-\u9fff]")

MIN_TITLE_CHARS = 8
MIN_TITLE_CJK = 4
MAX_TITLE_CHARS = 300


@dataclass
class PaperMetadata:
    title: str | None = None
    authors: str | None = None
    abstract: str | None = None
    doi: str | None = None
    year: int | None = None
    arxiv_id: str | None = None


def _strip_heading(line: str) -> str:
    return re.sub(r"^#{1,6}\s*", "", line).strip()


def _first_meaningful_lines(markdown: str, limit: int = 40) -> list[str]:
    lines = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def _plausible_title(text: str) -> bool:
    """标题长度下限对中文更宽松：中文标题字符数天然更少。"""
    if len(text) > MAX_TITLE_CHARS:
        return False
    if _cjk_count(text) >= MIN_TITLE_CJK:
        return True
    return len(text) >= MIN_TITLE_CHARS


def extract_metadata(
    markdown: str, pdf_metadata: dict[str, str] | None = None
) -> PaperMetadata:
    """启发式抽取元数据。PDF 内嵌 metadata 优先用于标题。"""
    meta = PaperMetadata()
    pdf_metadata = pdf_metadata or {}
    head = _first_meaningful_lines(markdown)
    head_text = "\n".join(head)

    # --- 标题 ---
    embedded_title = (pdf_metadata.get("title") or "").strip()
    # PDF 内嵌标题常是文件名或空，做基本合理性检查
    if _plausible_title(embedded_title) and not embedded_title.lower().endswith(".pdf"):
        meta.title = embedded_title
    else:
        # 优先取第一个 Markdown 标题行，避免误取正文段落
        heading_lines = [line for line in head if line.startswith("#")]
        for line in (*heading_lines, *head):
            candidate = _strip_heading(line)
            if ABSTRACT_RE.match(line):
                continue
            if _plausible_title(candidate):
                meta.title = candidate
                break

    # --- 作者 ---
    if meta.title:
        try:
            title_pos = next(
                i for i, line in enumerate(head) if meta.title in _strip_heading(line)
            )
        except StopIteration:
            title_pos = 0
        for line in head[title_pos + 1 : title_pos + 6]:
            candidate = _strip_heading(line)
            if ABSTRACT_RE.match(line) or len(candidate) < 3:
                continue
            has_separator = "," in candidate or "，" in candidate or " and " in candidate
            if has_separator and len(candidate) <= 400 and AUTHOR_HINT.match(candidate):
                # 去掉上标标记与邮箱
                cleaned = re.sub(r"[\d*†‡§¶]+", "", candidate)
                cleaned = re.sub(r"\S+@\S+", "", cleaned)
                meta.authors = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;，")
                break

    # --- 摘要 ---
    match = ABSTRACT_RE.search(markdown)
    if match:
        rest = markdown[match.end() :]
        # 摘要止于下一个标题或 Introduction/关键词
        stop = re.search(
            r"\n\s*#{1,6}\s|\n\s*(1\s*[.、]?\s*)?(introduction|引\s*言|关键词|keywords)\b",
            rest,
            re.IGNORECASE,
        )
        abstract = rest[: stop.start()] if stop else rest[:3000]
        abstract = re.sub(r"\s+", " ", abstract).strip()
        if len(abstract) >= 40:
            meta.abstract = abstract[:5000]

    # --- DOI / arXiv / 年份 ---
    doi_match = DOI_RE.search(markdown[:8000])
    if doi_match:
        meta.doi = doi_match.group(0).rstrip(".,;)")

    arxiv_match = ARXIV_RE.search(markdown[:8000])
    if arxiv_match:
        meta.arxiv_id = arxiv_match.group(1)

    for source in (head_text, pdf_metadata.get("creationDate", "")):
        year_match = YEAR_RE.search(source)
        if year_match:
            year = int(year_match.group(0))
            if 1900 <= year <= 2100:
                meta.year = year
                break

    return meta
