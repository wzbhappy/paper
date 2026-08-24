"""阶段状态机与进展汇总测试。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

from app.services.progress import (
    STAGES,
    ProgressSignals,
    build_progress,
    evaluate_stages,
)


def test_empty_project_suggests_discovery():
    report = build_progress(ProgressSignals(), "discovery")
    assert report.suggested_stage == "discovery"
    assert report.completion == 0.0
    assert "检索" in report.next_action or "上传" in report.next_action


def test_stage_keys_match_canonical_list():
    stages = evaluate_stages(ProgressSignals())
    keys = [s.key for s in stages]
    # 除 done 外每个阶段都应被评估
    assert keys == [s for s in STAGES if s != "done"]


def test_papers_collected_advances_to_search():
    report = build_progress(ProgressSignals(paper_count=2), "discovery")
    assert report.suggested_stage == "search"
    assert report.completion > 0


def test_enough_papers_advances_to_reading():
    report = build_progress(ProgressSignals(paper_count=6), "search")
    assert report.suggested_stage == "reading"


def test_summaries_advance_to_direction():
    report = build_progress(
        ProgressSignals(paper_count=6, summarized_count=4), "reading"
    )
    assert report.suggested_stage == "direction"


def test_generated_but_unselected_direction_still_pending():
    signals = ProgressSignals(
        paper_count=6, summarized_count=4, direction_count=3, has_selected_direction=False
    )
    report = build_progress(signals, "direction")
    assert report.suggested_stage == "direction"
    direction_stage = next(s for s in report.stages if s.key == "direction")
    assert "尚未采纳" in direction_stage.detail


def test_selected_direction_advances_to_review():
    signals = ProgressSignals(
        paper_count=6, summarized_count=4, direction_count=3, has_selected_direction=True
    )
    report = build_progress(signals, "direction")
    assert report.suggested_stage == "review"


def test_review_advances_to_outline():
    signals = ProgressSignals(
        paper_count=6,
        summarized_count=4,
        has_selected_direction=True,
        review_count=1,
    )
    assert build_progress(signals, "review").suggested_stage == "outline"


def test_outline_advances_to_writing():
    signals = ProgressSignals(
        paper_count=6,
        summarized_count=4,
        has_selected_direction=True,
        review_count=1,
        outline_section_count=10,
    )
    assert build_progress(signals, "outline").suggested_stage == "writing"


def test_writing_requires_half_sections():
    base = dict(
        paper_count=6,
        summarized_count=4,
        has_selected_direction=True,
        review_count=1,
        outline_section_count=10,
    )
    partial = build_progress(ProgressSignals(**base, written_section_count=3), "writing")
    assert partial.suggested_stage == "writing"
    writing_stage = next(s for s in partial.stages if s.key == "writing")
    assert writing_stage.done is False

    enough = build_progress(
        ProgressSignals(**base, written_section_count=5, total_word_count=5000), "writing"
    )
    writing_stage = next(s for s in enough.stages if s.key == "writing")
    assert writing_stage.done is True
    assert enough.suggested_stage != "writing"


def test_review_check_pending_when_errors_exist():
    signals = ProgressSignals(
        paper_count=6,
        summarized_count=4,
        has_selected_direction=True,
        review_count=1,
        outline_section_count=10,
        written_section_count=8,
        total_word_count=5000,
        quality_error_count=2,
    )
    assert build_progress(signals, "writing").suggested_stage == "review_check"


def test_quality_errors_block_completion():
    signals = ProgressSignals(
        paper_count=6,
        summarized_count=4,
        has_selected_direction=True,
        review_count=1,
        outline_section_count=10,
        written_section_count=8,
        total_word_count=8000,
        quality_error_count=3,
    )
    report = build_progress(signals, "review_check")
    assert report.suggested_stage == "review_check"
    stage = next(s for s in report.stages if s.key == "review_check")
    assert "3 个严重问题" in stage.detail


def test_full_completion():
    signals = ProgressSignals(
        paper_count=12,
        parsed_paper_count=10,
        summarized_count=10,
        direction_count=3,
        has_selected_direction=True,
        review_count=1,
        outline_section_count=12,
        written_section_count=12,
        total_word_count=20000,
        quality_error_count=0,
    )
    report = build_progress(signals, "review_check")
    assert report.suggested_stage == "done"
    assert report.completion == 1.0
    assert all(s.done for s in report.stages)


def test_current_stage_is_preserved_independently():
    """current_stage 反映用户所处位置，不被建议阶段覆写。"""
    report = build_progress(ProgressSignals(paper_count=1), "writing")
    assert report.current_stage == "writing"
    assert report.suggested_stage != "writing"


def test_stage_details_are_informative():
    signals = ProgressSignals(paper_count=3, summarized_count=2)
    report = build_progress(signals, "reading")
    details = {s.key: s.detail for s in report.stages}
    assert "3" in details["discovery"]
    assert "2" in details["reading"]
