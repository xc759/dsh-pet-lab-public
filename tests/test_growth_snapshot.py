# -*- coding: utf-8 -*-
"""周快照（``petwisp.growth.snapshot``）：周期计算 + **脱敏红线**。"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petwisp.growth.schema import default_archive
from petwisp.growth.snapshot import (
    build_summary,
    emit_if_due,
    list_snapshots,
    should_emit,
    snapshot_period,
    write_snapshot,
)
from petwisp.growth.store import GrowthStore


@pytest.fixture()
def moment() -> datetime:
    # 2026-10-30 周五 → ISO 2026-W44；避开周边界，保证断言稳定
    return datetime(2026, 10, 30, 10, 0, 0).astimezone()


@pytest.fixture()
def config_dir(tmp_path):
    target = tmp_path / "cfg"
    target.mkdir()
    return target


def test_snapshot_period_uses_iso_week(moment):
    assert snapshot_period(moment) == "2026-W44"
    assert snapshot_period(datetime(2026, 1, 1).astimezone()) == "2026-W01"


def test_summary_contains_only_counts(moment):
    archive = default_archive()
    learning = archive["profile"]["learning"]
    learning["goal"] = "考上上海交大计算机专硕"
    learning["target_schools"] = ["上海交通大学", "深圳大学"]
    learning["exam_date"] = "2027-12-18"
    learning["subjects"] = [{"name": "数学一"}, {"name": "408"}]
    learning["weekly_minutes"] = 900
    learning["milestones"] = [{"id": "m1", "title": "刷完 408 真题", "done": True}]
    archive["profile"]["life"]["notes"] = "最近失眠严重"
    archive["knowledge"]["increments"] = [{"id": "k1", "text": "用户提到想放弃"}]
    archive["emotion"]["daily"] = [
        {"date": "2026-10-28", "focus_minutes": 120, "distractions": 3, "mood": 1},
        {"date": "2026-10-29", "focus_minutes": 60, "distractions": 2, "mood": 0},
        {"date": "2026-10-01", "focus_minutes": 999, "distractions": 9, "mood": 0},
    ]

    summary = build_summary(archive, moment)
    blob = json.dumps(summary, ensure_ascii=False)

    # 硬红线：任何自由文本都不得出现在快照里
    for secret in ("上海交大", "上海交通大学", "深圳大学", "失眠", "放弃", "刷完 408 真题"):
        assert secret not in blob
    # 只保留计数与布尔
    assert summary["learning"]["goal_set"] is True
    assert summary["learning"]["target_school_count"] == 2
    assert summary["learning"]["subject_count"] == 2
    assert summary["learning"]["weekly_minutes_target"] == 900
    assert summary["learning"]["milestone_done"] == 1
    assert summary["knowledge"]["increment_total"] == 1
    # 只统计本周
    assert summary["emotion"]["week_focus_minutes"] == 180
    assert summary["emotion"]["week_distractions"] == 5


def test_empty_archive_summary_is_all_zero(moment):
    summary = build_summary(default_archive(), moment)
    assert summary["learning"]["goal_set"] is False
    assert summary["emotion"]["week_focus_minutes"] == 0
    assert summary["reviews"]["card_total"] == 0


def test_write_snapshot_creates_file_once(config_dir, moment):
    archive = default_archive()
    path = write_snapshot(config_dir, archive, moment)

    assert path is not None
    assert path.name == "2026-W44.json"
    assert path.parent.name == "snapshots"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["period"] == "2026-W44"
    assert data["schema_version"] == archive["schema_version"]

    # 同周期不重复写（返回同一路径，内容不变）
    again = write_snapshot(config_dir, archive, moment)
    assert again == path


def test_write_snapshot_force_overwrites(config_dir, moment):
    archive = default_archive()
    path = write_snapshot(config_dir, archive, moment)
    archive["emotion"]["daily"] = [
        {"date": "2026-10-30", "focus_minutes": 45, "distractions": 0, "mood": 0}]
    write_snapshot(config_dir, archive, moment, force=True)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["emotion"]["week_focus_minutes"] == 45


def test_should_emit_tracks_period(config_dir, moment):
    store = GrowthStore(config_dir)
    assert should_emit(store.load(), moment) is True

    path = emit_if_due(store, moment)
    assert path is not None
    assert should_emit(store.load(), moment) is False
    # 第二周再次到点
    next_week = datetime(2026, 11, 6, 10, 0, 0).astimezone()
    assert should_emit(store.load(), next_week) is True


def test_emit_if_due_is_idempotent(config_dir, moment):
    store = GrowthStore(config_dir)
    first = emit_if_due(store, moment)
    second = emit_if_due(store, moment)
    assert first is not None
    assert second is None
    assert len(list_snapshots(config_dir)) == 1
