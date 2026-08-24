"""PDF → Markdown 提取。

主实现用 pymupdf：按字号推断标题层级，输出 Markdown。
marker-pdf 质量更好但依赖重（torch），作为可选后端，安装后自动优先使用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


class ParseError(RuntimeError):
    """PDF 解析失败。"""


@dataclass
class ParsedDocument:
    markdown: str
    page_count: int
    metadata: dict[str, str] = field(default_factory=dict)
    backend: str = "pymupdf"


_WS = re.compile(r"[ \t]+")
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
# 行尾连字符断词，如 "represen-\ntation" → "representation"
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def _clean(text: str) -> str:
    for src, dst in _LIGATURES.items():
        text = text.replace(src, dst)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _WS.sub(" ", text)
    return text.strip()


def _heading_level(size: float, body_size: float) -> int | None:
    """按字号相对正文的比例推断标题层级；非标题返回 None。"""
    if body_size <= 0:
        return None
    ratio = size / body_size
    if ratio >= 1.6:
        return 1
    if ratio >= 1.3:
        return 2
    if ratio >= 1.15:
        return 3
    return None


def _extract_with_pymupdf(path: Path) -> ParsedDocument:
    try:
        import fitz  # pymupdf
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的保护
        raise ParseError("pymupdf (fitz) not installed") from exc

    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ParseError(f"cannot open PDF {path.name}: {exc}") from exc

    with doc:
        # 先统计字号分布，取最常见字号作为正文基准。
        size_counts: dict[float, int] = {}
        pages: list[list[dict]] = []
        for page in doc:
            blocks = page.get_text("dict").get("blocks", [])
            pages.append(blocks)
            for block in blocks:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if not text:
                            continue
                        size = round(span.get("size", 0), 1)
                        size_counts[size] = size_counts.get(size, 0) + len(text)

        body_size = max(size_counts, key=size_counts.get) if size_counts else 0.0

        lines_out: list[str] = []
        for blocks in pages:
            for block in blocks:
                block_lines = block.get("lines", [])
                if not block_lines:
                    continue
                # 一个 block 视为一段
                para_parts: list[str] = []
                max_size = 0.0
                for line in block_lines:
                    spans = line.get("spans", [])
                    para_parts.append("".join(s.get("text", "") for s in spans))
                    for span in spans:
                        if span.get("text", "").strip():
                            max_size = max(max_size, span.get("size", 0))

                para = _clean("\n".join(para_parts))
                if not para:
                    continue

                level = _heading_level(max_size, body_size)
                # 标题通常较短，长段落即使字号偏大也按正文处理
                if level and len(para) <= 200:
                    lines_out.append(f"\n{'#' * level} {para}\n")
                else:
                    lines_out.append(para)

        metadata = {
            k: v
            for k, v in (doc.metadata or {}).items()
            if isinstance(v, str) and v.strip()
        }
        page_count = doc.page_count

    markdown = "\n\n".join(lines_out)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    return ParsedDocument(
        markdown=markdown,
        page_count=page_count,
        metadata=metadata,
        backend="pymupdf",
    )


def _extract_with_marker(path: Path) -> ParsedDocument:  # pragma: no cover - 可选依赖
    from marker.convert import convert_single_pdf
    from marker.models import load_all_models

    text, _, out_meta = convert_single_pdf(str(path), load_all_models())
    return ParsedDocument(
        markdown=text.strip(),
        page_count=int(out_meta.get("pages", 0) or 0),
        metadata={k: str(v) for k, v in (out_meta or {}).items()},
        backend="marker",
    )


def extract_markdown(path: str | Path, prefer_marker: bool = True) -> ParsedDocument:
    """把 PDF 转成 Markdown。marker 可用时优先，失败自动回退 pymupdf。"""
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise ParseError(f"file not found: {pdf_path}")

    if prefer_marker:
        try:
            return _extract_with_marker(pdf_path)
        except ImportError:
            logger.debug("marker-pdf unavailable, using pymupdf")
        except Exception as exc:  # pragma: no cover
            logger.warning("marker-pdf failed ({}), falling back to pymupdf", exc)

    return _extract_with_pymupdf(pdf_path)
