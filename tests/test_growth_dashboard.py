# -*- coding: utf-8 -*-
"""成长面板的取数层：倒计时、周合计、配比、里程碑排序。

这一层不做 Qt，纯函数，边界全部用固定档案 + 固定"今天"钉死，避免测试
随真实日期漂移（跨周跑就会红的用例等于没有用例）。
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from petwisp.growth import dashboard as dash
from petwisp.growth.schema import default_archive


def _moment(y, m, d, hh=10, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm).astimezone()


# ---------------------------------------------------------------- 倒计时
@pytest.mark.parametrize("exam,today,expected", [
    ("2027-12-25", date(2027, 12, 24), 1),
    ("2027-12-25", date(2027, 12, 25), 0),
    ("2027-12-25", date(2027, 12, 26), -1),
    ("", date(2027, 12, 25), None),
    ("2027-13-01", date(2027, 12, 25), None),
    ("not-a-date", date(2027, 12, 25), None),
])
def test_days_until(exam, today, expected):
    assert dash.days_until(exam, today) == expected


@pytest.mark.parametrize("exam,expected", [
    ("2027-12-25", "还有 1 天"),
    ("", "还没设置考试日"),
])
def test_countdown_text(exam, expected):
    assert dash.countdown_text(exam, date(2027, 12, 24)) == expected


def test_countdown_text_same_day_and_past():
    assert dash.countdown_text("2027-12-24", date(2027, 12, 24)) == "就是今天"
    assert dash.countdown_text("2027-12-20", date(2027, 12, 24)) == "已过 4 天"


# ---------------------------------------------------------------- 周合计
def _archive_with_daily(rows):
    archive = default_archive()
    archive["emotion"]["daily"] = rows
    return archive


def test_week_focus_minutes_only_counts_current_week():
    # 2026-10-30 是周五，本周为 10-26(一) ~ 11-01(日)
    archive = _archive_with_daily([
        {"date": "2026-10-25", "focus_minutes": 999},   # 上周日，不算
        {"date": "2026-10-26", "focus_minutes": 60},
        {"date": "2026-10-30", "focus_minutes": 45},
        {"date": "2026-11-01", "focus_minutes": 15},    # 本周末，算
    ])
    assert dash.week_focus_minutes(archive, date(2026, 10, 30)) == 120


def test_week_focus_minutes_tolerates_dirty_rows():
    archive = _archive_with_daily([
        {"date": "2026-10-30", "focus_minutes": "abc"},
        {"date": "bad-date", "focus_minutes": 100},
        {"date": "2026-10-30", "focus_minutes": -20},
    ])
    assert dash.week_focus_minutes(archive, date(2026, 10, 30)) == 0


def test_today_row_defaults_to_zero():
    archive = _archive_with_daily([{"date": "2026-10-29", "focus_minutes": 30}])
    assert dash.today_row(archive, date(2026, 10, 30)) == {
        "focus_minutes": 0, "distractions": 0, "mood": 0}


def test_today_row_reads_existing_row():
    archive = _archive_with_daily([
        {"date": "2026-10-30", "focus_minutes": 50, "distractions": 2, "mood": -1}])
    assert dash.today_row(archive, date(2026, 10, 30))["focus_minutes"] == 50


# ---------------------------------------------------------------- 配比
def test_subject_ratios_normalize_weights():
    rows = dash.subject_ratios([
        {"name": "数学一", "weekly_minutes": 300, "weight": 40},
        {"name": "408", "weekly_minutes": 300, "weight": 40},
        {"name": "英语一", "weekly_minutes": 120, "weight": 20},
        {"name": "", "weight": 10},          # 无名字丢弃
    ])
    assert [r["name"] for r in rows] == ["数学一", "408", "英语一"]
    assert sum(r["ratio"] for r in rows) == pytest.approx(1.0)


def test_subject_ratios_without_weight_keeps_zero():
    rows = dash.subject_ratios([{"name": "政治", "weekly_minutes": 60, "weight": 0}])
    assert rows[0]["ratio"] == 0.0
    assert rows[0]["weight"] == 0


def test_subject_ratios_handles_garbage():
    assert dash.subject_ratios(None) == []
    assert dash.subject_ratios(["数学", 3]) == []


# ---------------------------------------------------------------- 里程碑
def test_milestone_rows_sorting_and_overdue():
    archive = default_archive()
    archive["profile"]["learning"]["milestones"] = [
        {"id": "ms-done", "title": "做完", "due": "2026-01-01", "done": True},
        {"id": "ms-late", "title": "逾期", "due": "2026-10-01", "done": False},
        {"id": "ms-soon", "title": "下周", "due": "2026-11-05", "done": False},
        {"id": "ms-none", "title": "无截止", "due": "", "done": False},
        {"id": "", "title": "无 id 丢弃", "due": "2026-11-01", "done": False},
    ]
    rows = dash.milestone_rows(archive, date(2026, 10, 30))
    assert [r["id"] for r in rows] == ["ms-late", "ms-soon", "ms-none", "ms-done"]
    assert rows[0]["overdue"] is True
    assert rows[1]["overdue"] is False
    assert rows[2]["days_left"] is None
    assert rows[3]["days_left"] == -302


# ---------------------------------------------------------------- 总览
def test_build_dashboard_empty_archive_is_safe():
    data = dash.build_dashboard({}, _moment(2026, 10, 30))
    assert data["days_left"] is None
    assert data["countdown"] == "还没设置考试日"
    assert data["today_stats"] == {"focus_minutes": 0, "distractions": 0, "mood": 0}
    assert data["week_ratio"] == 0.0
    assert data["subjects"] == []
    assert data["milestones"] == []
    assert data["reviews"]["total"] == 0


def test_build_dashboard_full_archive():
    archive = default_archive()
    learning = archive["profile"]["learning"]
    learning["goal"] = "2028 上岸"
    learning["exam_date"] = "2027-12-25"
    learning["weekly_minutes"] = 600
    learning["subjects"] = [{"name": "408", "weekly_minutes": 300, "weight": 50},
                            {"name": "数学一", "weekly_minutes": 300, "weight": 50}]
    learning["milestones"] = [{"id": "ms-1", "title": "第一轮", "due": "2026-12-01",
                               "done": False}]
    archive["emotion"]["daily"] = [
        {"date": "2026-10-30", "focus_minutes": 120, "distractions": 1, "mood": 2}]
    archive["emotion"]["streak_days"] = 3
    archive["emotion"]["energy"] = 80
    archive["reviews"]["cards"] = [{"id": "c1", "due": "2026-10-30"},
                                   {"id": "c2", "due": "2026-12-01"}]
    archive["reviews"]["stats"] = {"done": 10, "again": 2}
    archive["knowledge"]["topics"] = {"数学": 3, "英语": 1}

    data = dash.build_dashboard(archive, _moment(2026, 10, 30))
    assert data["days_left"] == 421
    assert data["week_focus"] == 120
    assert data["week_ratio"] == pytest.approx(0.2)
    assert data["today_stats"]["mood"] == 2
    assert data["reviews"]["due"] == 1
    assert data["topics"][0] == ("数学", 3)
    assert data["subjects"][0]["ratio"] == pytest.approx(0.5)
