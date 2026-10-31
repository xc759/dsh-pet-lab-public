# -*- coding: utf-8 -*-
"""养成表现档位（``pet.growth.presence``）：只算档位，不许编数字。"""
from __future__ import annotations

from datetime import date, datetime

from pet.growth import presence
from pet.growth.dashboard import build_dashboard

MOMENT = datetime(2026, 10, 30, 20, 0, tzinfo=datetime.now().astimezone().tzinfo)


def _archive(*, minutes_today=0, weekly=700, streak=0, mood=None, days_back=(0,)):
    daily = []
    for offset in days_back:
        day = date(2026, 10, 30).fromordinal(date(2026, 10, 30).toordinal() - offset)
        row = {"date": day.isoformat(), "focus_minutes": minutes_today if offset == 0 else 60}
        if offset == 0 and mood is not None:
            row["mood"] = mood
        daily.append(row)
    return {
        "profile": {"learning": {"weekly_minutes": weekly}},
        "emotion": {"streak_days": streak, "daily": daily},
    }


def test_empty_archive_is_unknown():
    assert presence.grade({}, MOMENT)["tier"] == "unknown"


def test_today_progress_makes_sprint():
    # 每日目标 = 700/7 = 100 分钟，做 90 分钟 → 90%
    info = presence.grade(_archive(minutes_today=90, days_back=(0, 1)), MOMENT)
    assert info["tier"] == "sprint"
    assert info["label"] == presence.TIERS["sprint"]["label"]
    assert info["action"] == "speak"
    assert 0.8 <= info["progress"] <= 1.0


def test_partial_day_is_steady():
    info = presence.grade(_archive(minutes_today=30, days_back=(0,)), MOMENT)
    assert info["tier"] == "steady"
    assert info["progress"] < 0.8


def test_three_idle_days_means_rest():
    # 最后一次记录在 4 天前 → 今天往回连续 4 天是空的
    info = presence.grade(_archive(minutes_today=0, days_back=(4,)), MOMENT)
    assert info["tier"] == "rest"
    assert "连续 4 天" in info["reason"]


def test_low_mood_means_rest_even_if_worked():
    info = presence.grade(_archive(minutes_today=90, days_back=(0, 1), mood=-2), MOMENT)
    assert info["tier"] == "rest"
    assert "情绪" in info["reason"]


def test_streak_alone_can_be_sprint():
    info = presence.grade(_archive(minutes_today=0, days_back=(0, 1), streak=21), MOMENT)
    assert info["tier"] == "sprint"


def test_no_weekly_target_does_not_divide_by_zero():
    info = presence.grade(_archive(minutes_today=60, weekly=0, days_back=(0,)), MOMENT)
    assert info["progress"] == 0.0
    assert info["tier"] == "steady"


def test_presence_never_raises_on_junk_archive():
    junk = {"profile": {"learning": {"daily_log": "不是列表", "weekly_minutes": "x"}},
            "emotion": {"daily": [{"date": "2026-10-30", "focus_minutes": "很多"}, "坏行"]}}
    info = presence.grade(junk, MOMENT)
    assert info["tier"] in presence.TIERS


def test_dashboard_carries_presence():
    data = build_dashboard(_archive(minutes_today=90, days_back=(0,)), MOMENT)
    assert data["presence"]["tier"] == "sprint"
