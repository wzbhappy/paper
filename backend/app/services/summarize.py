"""结构化摘要服务：把解析后的论文正文交给 LLM，抽取成固定 schema。

关键设计：
- 长文本先按「摘要 + 引言 + 方法 + 结论」优先级裁剪，控制 token 成本。
- 输出走 complete_json，schema 校验失败会自动重试。
"""

from __future__ import annotations

import re

from loguru import logger
from pydantic import BaseModel, Field

from app.llm import LLMClient, LLMRequest, Message, Role, TaskType, get_llm
from app.llm.prompts import render


class PaperSummary(BaseModel):
    """论文结构化摘要，对应 papers.summary JSONB 字段。"""

    one_line: str | None = None
    problem: str | None = None
    method: str | None = None
    dataset: str | None = None
    metrics: dict[str, str] = Field(default_factory=dict)
    conclusion: str | None = None
    limitations: list[str] = Field(default_factory=list)
    future_work: list[str] = Field(default_factory=list)
    key_terms: list[str] = Field(default_factory=list)


# 按重要性给章节排优先级：正文过长时保留高优先级章节。
SECTION_PRIORITY = [
    (r"abstract|摘\s*要", 0),
    (r"conclusion|结\s*论|总\s*结", 1),
    (r"introduction|引\s*言", 2),
    (r"method|approach|方\s*法|模\s*型", 3),
    (r"experiment|result|实\s*验|结\s*果", 4),
    (r"discussion|讨\s*论", 5),
    (r"related\s*work|相关工作", 6),
    (r"reference|参考文献|appendix|附\s*录", 99),
]

HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def _section_priority(title: str) -> int:
    lowered = title.lower()
    for pattern, priority in SECTION_PRIORITY:
        if re.search(pattern, lowered):
            return priority
    return 50


def _approx_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def prepare_content(markdown: str, max_tokens: int = 6000) -> str:
    """按章节重要性裁剪正文，控制单次调用成本。

    未超预算时原样返回；超出时按优先级取章节，参考文献等低价值内容先丢。
    """
    if _approx_tokens(markdown) <= max_tokens:
        return markdown

    matches = list(HEADING.finditer(markdown))
    if not matches:
        # 无标题结构，直接截断头部（摘要与引言通常在前）
        budget_chars = max_tokens * 3
        return markdown[:budget_chars]

    sections: list[tuple[int, str, str]] = []
    # 首个标题之前的内容（题目/作者/摘要）优先级最高
    preamble = markdown[: matches[0].start()].strip()
    if preamble:
        sections.append((-1, "", preamble))

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        title = match.group(2).strip()
        body = markdown[match.start() : end].strip()
        sections.append((_section_priority(title), title, body))

    kept: list[tuple[int, str]] = []
    used = 0
    # 按优先级挑选，同优先级保持原文顺序
    for order, (priority, _title, body) in sorted(
        enumerate(sections), key=lambda item: (item[1][0], item[0])
    ):
        if priority >= 99:
            continue
        tokens = _approx_tokens(body)
        if used + tokens > max_tokens:
            continue
        kept.append((order, body))
        used += tokens

    if not kept:
        return markdown[: max_tokens * 3]

    kept.sort(key=lambda item: item[0])
    logger.debug("prepare_content kept {}/{} sections", len(kept), len(sections))
    return "\n\n".join(body for _order, body in kept)


async def summarize_paper(
    markdown: str,
    title: str | None = None,
    discipline: str | None = None,
    language: str = "中文",
    client: LLMClient | None = None,
) -> PaperSummary:
    """生成结构化摘要。正文为空时返回空摘要，不浪费调用。"""
    content = prepare_content(markdown)
    if not content.strip():
        logger.warning("summarize_paper: empty content, skip LLM call")
        return PaperSummary()

    prompt = render(
        "summarize",
        discipline=discipline,
        content=content,
        title=title,
        language=language,
    )
    llm = client or get_llm()
    req = LLMRequest(
        messages=[Message(role=Role.USER, content=prompt)],
        task=TaskType.SUMMARIZE,
        temperature=0.2,
    )
    return await llm.complete_json(req, PaperSummary, retries=1)
