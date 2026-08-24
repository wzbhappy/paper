"""Prompt 模板管理：按 task 组织 .j2 模板，支持学科覆写。

查找顺序（先命中先用）：
  prompts/{discipline}/{task}.j2
  prompts/{task}.j2
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

PROMPT_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PROMPT_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )


def render(task: str, discipline: str | None = None, **variables: object) -> str:
    """渲染 prompt 模板。discipline 存在对应模板时优先使用。

    discipline 既用于模板选择，也作为变量注入模板（可在正文中引用）。
    """
    candidates = []
    if discipline:
        candidates.append(f"{discipline}/{task}.j2")
    candidates.append(f"{task}.j2")

    variables.setdefault("discipline", discipline)

    for candidate in candidates:
        try:
            return _env().get_template(candidate).render(**variables).strip()
        except TemplateNotFound:
            continue

    raise TemplateNotFound(f"no prompt template for task={task!r}")
