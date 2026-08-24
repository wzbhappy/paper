"""项目阶段状态机与进展汇总。

阶段不强制线性推进，但需要判断「当前应该做什么」以驱动前端引导。
"""

from __future__ import annotations

from dataclasses import dataclass, field

STAGES = [
    "discovery",
    "search",
    "reading",
    "direction",
    "review",
    "outline",
    "writing",
    "review_check",
    "done",
]

STAGE_LABELS = {
    "discovery": "发现问题",
    "search": "检索文献",
    "reading": "阅读管理",
    "direction": "确定方向",
    "review": "撰写综述",
    "outline": "论文大纲",
    "writing": "正文撰写",
    "review_check": "润色检查",
    "done": "已完成",
}


@dataclass
class ProgressSignals:
    """从数据库统计出的客观进展信号。"""

    paper_count: int = 0
    parsed_paper_count: int = 0
    summarized_count: int = 0
    direction_count: int = 0
    has_selected_direction: bool = False
    review_count: int = 0
    outline_section_count: int = 0
    written_section_count: int = 0
    total_word_count: int = 0
    quality_error_count: int = 0


@dataclass
class StageStatus:
    key: str
    label: str
    done: bool
    detail: str


@dataclass
class ProgressReport:
    current_stage: str
    suggested_stage: str
    next_action: str
    completion: float
    """0 到 1 的整体完成度估计。"""
    stages: list[StageStatus] = field(default_factory=list)
    signals: ProgressSignals = field(default_factory=ProgressSignals)


def evaluate_stages(signals: ProgressSignals) -> list[StageStatus]:
    """按客观信号判断各阶段是否达成。"""
    s = signals
    return [
        StageStatus(
            "discovery",
            STAGE_LABELS["discovery"],
            s.paper_count > 0,
            f"已有 {s.paper_count} 篇文献" if s.paper_count else "尚未收集文献",
        ),
        StageStatus(
            "search",
            STAGE_LABELS["search"],
            s.paper_count >= 5,
            f"{s.paper_count} 篇（建议至少 5 篇）",
        ),
        StageStatus(
            "reading",
            STAGE_LABELS["reading"],
            s.summarized_count > 0,
            f"{s.summarized_count} 篇已生成结构化摘要"
            if s.summarized_count
            else "尚无文献完成摘要",
        ),
        StageStatus(
            "direction",
            STAGE_LABELS["direction"],
            s.has_selected_direction,
            "已采纳研究方向"
            if s.has_selected_direction
            else f"已生成 {s.direction_count} 个候选方向，尚未采纳",
        ),
        StageStatus(
            "review",
            STAGE_LABELS["review"],
            s.review_count > 0,
            f"已生成 {s.review_count} 版综述" if s.review_count else "尚无综述草稿",
        ),
        StageStatus(
            "outline",
            STAGE_LABELS["outline"],
            s.outline_section_count > 0,
            f"{s.outline_section_count} 个章节" if s.outline_section_count else "尚无大纲",
        ),
        StageStatus(
            "writing",
            STAGE_LABELS["writing"],
            s.outline_section_count > 0
            and s.written_section_count >= max(1, s.outline_section_count // 2),
            f"{s.written_section_count}/{s.outline_section_count} 章节已撰写，共 {s.total_word_count} 字"
            if s.outline_section_count
            else "尚无大纲，无法开始写作",
        ),
        StageStatus(
            "review_check",
            STAGE_LABELS["review_check"],
            s.total_word_count > 0 and s.quality_error_count == 0,
            "质量检查无严重问题"
            if s.total_word_count and s.quality_error_count == 0
            else f"{s.quality_error_count} 个严重问题待修正",
        ),
    ]


NEXT_ACTION = {
    "discovery": "到「检索文献」标签检索并导入相关文献，或在「文献库」上传 PDF。",
    "search": "继续补充文献，建议积累至少 5 篇以支撑后续分析。",
    "reading": "上传文献 PDF 以生成结构化摘要，摘要是方向分析与写作的基础。",
    "direction": "到「研究方向」标签生成方向建议，并采纳其中一个。",
    "review": "到「文献综述」标签生成综述草稿，梳理研究脉络。",
    "outline": "到「论文大纲」标签选择模板生成大纲。",
    "writing": "到「正文撰写」标签逐节撰写，可用 AI 生成初稿再人工修订。",
    "review_check": "到「正文撰写」标签运行质量检查，修正报告中的问题。",
    "done": "全流程已完成，可导出稿件。",
}


def build_progress(signals: ProgressSignals, current_stage: str) -> ProgressReport:
    """汇总进展。suggested_stage 是第一个未完成的阶段。"""
    stages = evaluate_stages(signals)
    pending = next((s for s in stages if not s.done), None)
    suggested = pending.key if pending else "done"
    completion = sum(1 for s in stages if s.done) / len(stages)

    return ProgressReport(
        current_stage=current_stage,
        suggested_stage=suggested,
        next_action=NEXT_ACTION.get(suggested, NEXT_ACTION["done"]),
        completion=round(completion, 2),
        stages=stages,
        signals=signals,
    )
