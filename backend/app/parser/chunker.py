"""Markdown 逻辑块切分：按标题分节，超长节按 token 预算再切，保留章节路径。

切块是 RAG 质量的关键：块太大检索不精准，太小丢上下文。
这里按「标题层级 → 段落」两级切分，并在块间保留重叠。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    """一个可检索的文献片段。"""

    text: str
    index: int
    section: str = ""
    """所属章节路径，如 "3 方法 > 3.1 模型结构"。"""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)


def _approx_tokens(text: str) -> int:
    """粗略 token 估算：中文按字符计，英文按 ~4 字符/token。

    避免引入 tiktoken 依赖，用于切分阈值判断足够。
    """
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + other // 4


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _pack(
    paragraphs: list[str],
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """把段落打包成不超过 max_tokens 的块，块间带重叠。"""
    packed: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _approx_tokens(para)

        # 单段就超限，先冲掉当前块，再把该段按句切开
        if para_tokens > max_tokens:
            if current:
                packed.append("\n\n".join(current))
                current, current_tokens = [], 0
            packed.extend(_split_long_paragraph(para, max_tokens))
            continue

        if current_tokens + para_tokens > max_tokens and current:
            packed.append("\n\n".join(current))
            # 用尾部段落做重叠，保持上下文连续
            overlap: list[str] = []
            overlap_total = 0
            for prev in reversed(current):
                prev_tokens = _approx_tokens(prev)
                if overlap_total + prev_tokens > overlap_tokens:
                    break
                overlap.insert(0, prev)
                overlap_total += prev_tokens
            current = [*overlap, para]
            current_tokens = overlap_total + para_tokens
        else:
            current.append(para)
            current_tokens += para_tokens

    if current:
        packed.append("\n\n".join(current))
    return packed


def _split_long_paragraph(para: str, max_tokens: int) -> list[str]:
    """超长段落按句边界切分（中英句号/问号/叹号/分号）。"""
    sentences = re.split(r"(?<=[。！？；.!?])\s*", para)
    sentences = [s for s in sentences if s.strip()]
    if not sentences:
        return [para]

    out: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for sentence in sentences:
        sentence_tokens = _approx_tokens(sentence)
        if buf_tokens + sentence_tokens > max_tokens and buf:
            out.append(" ".join(buf))
            buf, buf_tokens = [], 0
        buf.append(sentence)
        buf_tokens += sentence_tokens
    if buf:
        out.append(" ".join(buf))
    return out


def split_markdown(
    markdown: str,
    max_tokens: int = 500,
    overlap_tokens: int = 60,
    min_chars: int = 40,
) -> list[Chunk]:
    """按标题分节后打包成块。

    min_chars 用于丢弃「只有标题没内容」这类噪声块。
    """
    if not markdown.strip():
        return []

    # 先按标题行切成 (章节路径, 正文) 的序列
    sections: list[tuple[str, list[str]]] = []
    heading_stack: list[tuple[int, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            path = " > ".join(title for _, title in heading_stack)
            sections.append((path, buffer.copy()))
            buffer.clear()

    for line in markdown.splitlines():
        match = HEADING.match(line.strip())
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
        else:
            buffer.append(line)

    flush()

    chunks: list[Chunk] = []
    for section_path, lines in sections:
        paragraphs = _split_paragraphs("\n".join(lines))
        if not paragraphs:
            continue
        for piece in _pack(paragraphs, max_tokens, overlap_tokens):
            if len(piece.strip()) < min_chars:
                continue
            chunks.append(
                Chunk(
                    text=piece.strip(),
                    index=len(chunks),
                    section=section_path,
                )
            )

    return chunks
