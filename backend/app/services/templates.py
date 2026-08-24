"""论文大纲模板库。

模板决定章节骨架，LLM 只负责填充每节要点，避免结构漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TemplateNode:
    """模板中的一个章节节点。"""

    title: str
    type: str = "section"
    """chapter / section / subsection"""
    hint: str = ""
    """给 LLM 的写作提示，说明该节应包含什么。"""
    children: list["TemplateNode"] = field(default_factory=list)


@dataclass
class OutlineTemplate:
    key: str
    name: str
    description: str
    nodes: list[TemplateNode]


IMRAD = OutlineTemplate(
    key="imrad",
    name="IMRaD 实证研究",
    description="适用于有实验/数据验证的研究论文，是自然科学与工程领域的主流结构。",
    nodes=[
        TemplateNode(
            "引言",
            "chapter",
            "交代研究背景与重要性，指出现有工作的不足，给出本文的研究问题与贡献清单。",
            [
                TemplateNode("研究背景与意义", "section", "说明该问题为何重要，应用价值何在。"),
                TemplateNode("研究现状与不足", "section", "概述已有方法，明确指出其局限。"),
                TemplateNode("本文贡献", "section", "分条列出本文的具体贡献，每条可验证。"),
            ],
        ),
        TemplateNode(
            "相关工作",
            "chapter",
            "按技术路线分类梳理已有研究，与本文方法作对比，不要简单罗列。",
        ),
        TemplateNode(
            "方法",
            "chapter",
            "完整描述所提方法，保证可复现：形式化定义、模型结构、算法流程、复杂度分析。",
            [
                TemplateNode("问题定义", "section", "形式化描述输入输出与优化目标。"),
                TemplateNode("整体框架", "section", "给出方法总览，说明各模块如何协作。"),
                TemplateNode("关键模块设计", "section", "详述核心创新点的设计与动机。"),
            ],
        ),
        TemplateNode(
            "实验",
            "chapter",
            "说明实验设置并给出结果，需包含与基线的对比和消融实验。",
            [
                TemplateNode("实验设置", "section", "数据集、评价指标、基线方法、实现细节与超参数。"),
                TemplateNode("主要结果", "section", "与基线对比，分析性能差异的原因。"),
                TemplateNode("消融实验", "section", "逐一验证各模块的必要性。"),
                TemplateNode("分析与讨论", "section", "讨论失败案例、敏感性、适用边界。"),
            ],
        ),
        TemplateNode(
            "结论",
            "chapter",
            "总结贡献与发现，坦诚说明局限，给出未来工作方向。",
        ),
    ],
)

REVIEW = OutlineTemplate(
    key="review",
    name="文献综述",
    description="适用于梳理某一领域研究脉络的综述论文。",
    nodes=[
        TemplateNode(
            "引言",
            "chapter",
            "界定综述范围，说明为何此时需要这篇综述，交代文献筛选标准。",
        ),
        TemplateNode(
            "背景与基本概念",
            "chapter",
            "统一术语体系，介绍读者理解后文所需的基础知识。",
        ),
        TemplateNode(
            "研究方法分类",
            "chapter",
            "按技术路线或问题维度建立分类体系，是综述的核心组织框架。",
            [
                TemplateNode("分类体系", "section", "说明分类维度的选取依据。"),
                TemplateNode("各类方法评述", "section", "逐类梳理代表工作，对比优劣。"),
            ],
        ),
        TemplateNode(
            "数据集与评价基准",
            "chapter",
            "汇总常用数据集与指标，指出评测口径不一致带来的可比性问题。",
        ),
        TemplateNode(
            "挑战与未来方向",
            "chapter",
            "提炼领域共性难题，指出尚未解决的问题与有前景的方向。",
        ),
        TemplateNode("结论", "chapter", "总结领域现状与本文的主要判断。"),
    ],
)

ENGINEERING = OutlineTemplate(
    key="engineering",
    name="工科实验研究",
    description="适用于以系统实现与工程验证为主的论文，实验部分更详细。",
    nodes=[
        TemplateNode("引言", "chapter", "工程背景、实际需求、本文要解决的技术难点。"),
        TemplateNode("相关技术综述", "chapter", "现有技术方案及其在本场景下的不足。"),
        TemplateNode(
            "系统设计",
            "chapter",
            "系统总体架构与各子模块设计，说明设计权衡。",
            [
                TemplateNode("需求分析", "section", "功能性与非功能性需求。"),
                TemplateNode("总体架构", "section", "架构图与数据流。"),
                TemplateNode("关键技术实现", "section", "核心难点的解决方案。"),
            ],
        ),
        TemplateNode(
            "实验与验证",
            "chapter",
            "实验平台、测试方案、性能数据与对比分析。",
            [
                TemplateNode("实验平台与方案", "section", "硬件软件环境、测试用例设计。"),
                TemplateNode("功能验证", "section", "验证系统满足功能需求。"),
                TemplateNode("性能测试", "section", "吞吐、延迟、资源占用等量化指标。"),
                TemplateNode("对比分析", "section", "与现有方案的定量对比。"),
            ],
        ),
        TemplateNode("总结与展望", "chapter", "工作总结、不足之处、后续改进方向。"),
    ],
)

THESIS = OutlineTemplate(
    key="thesis",
    name="学位论文",
    description="适用于硕博学位论文，章节更完整，含绪论与全文总结。",
    nodes=[
        TemplateNode(
            "绪论",
            "chapter",
            "研究背景、意义、国内外研究现状、本文研究内容与组织结构。",
            [
                TemplateNode("研究背景与意义", "section", "宏观背景与研究价值。"),
                TemplateNode("国内外研究现状", "section", "分主题梳理已有工作。"),
                TemplateNode("本文主要工作", "section", "研究内容与创新点。"),
                TemplateNode("论文组织结构", "section", "逐章说明全文安排。"),
            ],
        ),
        TemplateNode("相关理论与技术基础", "chapter", "后续章节所需的理论工具与技术背景。"),
        TemplateNode(
            "研究方法",
            "chapter",
            "核心方法的完整论述，通常是学位论文的主体章节。",
            [
                TemplateNode("问题建模", "section", "形式化定义。"),
                TemplateNode("方法设计", "section", "方法细节与理论分析。"),
                TemplateNode("算法实现", "section", "实现要点与复杂度。"),
            ],
        ),
        TemplateNode(
            "实验与结果分析",
            "chapter",
            "实验设置、结果、消融与讨论。",
            [
                TemplateNode("实验设置", "section", "数据、指标、基线、环境。"),
                TemplateNode("结果与分析", "section", "定量结果与原因分析。"),
                TemplateNode("消融实验", "section", "各模块贡献度验证。"),
            ],
        ),
        TemplateNode("总结与展望", "chapter", "全文总结、创新点归纳、局限与未来工作。"),
    ],
)

TEMPLATES: dict[str, OutlineTemplate] = {
    t.key: t for t in (IMRAD, REVIEW, ENGINEERING, THESIS)
}

DEFAULT_TEMPLATE = "imrad"


def get_template(key: str | None) -> OutlineTemplate:
    """按 key 取模板，未知 key 回退到默认模板。"""
    return TEMPLATES.get(key or DEFAULT_TEMPLATE, TEMPLATES[DEFAULT_TEMPLATE])


def list_templates() -> list[dict[str, str]]:
    return [
        {"key": t.key, "name": t.name, "description": t.description}
        for t in TEMPLATES.values()
    ]


def flatten(
    nodes: list[TemplateNode], parent_path: str = ""
) -> list[tuple[str, TemplateNode]]:
    """展开成 (路径, 节点) 列表，路径形如 "引言 > 研究背景与意义"。"""
    out: list[tuple[str, TemplateNode]] = []
    for node in nodes:
        path = f"{parent_path} > {node.title}" if parent_path else node.title
        out.append((path, node))
        out.extend(flatten(node.children, path))
    return out
