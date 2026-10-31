# -*- coding: utf-8 -*-
"""周快照：把成长档案压成**只有计数**的摘要，供对外同步。

脱敏红线（阶段 B 决策）：快照里**不允许**出现目标文本、院校名、笔记正文、
错题内容、对话原文。只允许出现：周期、计数、布尔「是否已设置」、状态枚举。
这条边界由 ``build_summary`` 的白名单结构保证——它不复制档案里的任何自由文本，
只从自由文本**推导**长度/布尔值。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config_write_lock as cwl
from .schema import (
    SCHEMA_VERSION,
    SNAPSHOT_DIRNAME,
    archive_path,
    growth_dir,
    now_iso,
)

logger = logging.getLogger(__name__)


def snapshot_period(moment: datetime | None = None) -> str:
    """ISO 周标识，例如 ``2026-W44``（周一为一周起点）。"""
    moment = moment or datetime.now()
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def _week_bounds(moment: datetime) -> tuple[date, date]:
    day = moment.date()
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def build_summary(archive: dict, moment: datetime | None = None) -> dict:
    """档案 → 脱敏周摘要（纯计数，不含任何自由文本）。"""
    moment = moment or datetime.now().astimezone()
    learning = archive.get("profile", {}).get("learning", {}) or {}
    life = archive.get("profile", {}).get("life", {}) or {}
    emotion = archive.get("emotion", {}) or {}
    reviews = archive.get("reviews", {}) or {}
    knowledge = archive.get("knowledge", {}) or {}
    sessions = archive.get("sessions", {}) or {}

    start, end = _week_bounds(moment)
    week_rows = [
        row for row in (emotion.get("daily") or [])
        if isinstance(row, dict) and start.isoformat() <= str(row.get("date", "")) <= end.isoformat()
    ]
    week_focus = sum(int(row.get("focus_minutes", 0) or 0) for row in week_rows)
    week_distractions = sum(int(row.get("distractions", 0) or 0) for row in week_rows)

    cards = [c for c in (reviews.get("cards") or []) if isinstance(c, dict)]
    today = moment.date().isoformat()
    due_today = sum(1 for c in cards if str(c.get("due") or "")[:10] and str(c["due"])[:10] <= today)

    return {
        "schema_version": SCHEMA_VERSION,
        "period": snapshot_period(moment),
        "generated_at": now_iso(moment),
        "learning": {
            "goal_set": bool(str(learning.get("goal") or "").strip()),
            "exam_date_set": bool(str(learning.get("exam_date") or "").strip()),
            "target_school_count": len(learning.get("target_schools") or []),
            "subject_count": len(learning.get("subjects") or []),
            "weekly_minutes_target": int(learning.get("weekly_minutes", 0) or 0),
            "milestone_total": len(learning.get("milestones") or []),
            "milestone_done": sum(1 for m in (learning.get("milestones") or [])
                                  if isinstance(m, dict) and m.get("done")),
        },
        "life": {
            "sleep_set": bool(str(life.get("sleep_at") or "").strip()),
            "wake_set": bool(str(life.get("wake_at") or "").strip()),
        },
        "emotion": {
            "state": str(emotion.get("state") or "calm"),
            "energy": int(emotion.get("energy", 0) or 0),
            "streak_days": int(emotion.get("streak_days", 0) or 0),
            "week_focus_minutes": week_focus,
            "week_distractions": week_distractions,
        },
        "reviews": {
            "card_total": len(cards),
            "done_total": int((reviews.get("stats") or {}).get("done", 0) or 0),
            "again_total": int((reviews.get("stats") or {}).get("again", 0) or 0),
            "due_today": due_today,
        },
        "knowledge": {"increment_total": len(knowledge.get("increments") or [])},
        "sessions": {
            "total": int(sessions.get("total", 0) or 0),
            "character_count": len(sessions.get("by_character") or {}),
        },
    }


def should_emit(archive: dict, moment: datetime | None = None) -> bool:
    """本周还没出过快照 → True。"""
    return str((archive.get("snapshot") or {}).get("last_period") or "") != snapshot_period(moment)


def write_snapshot(config_dir, archive: dict, moment: datetime | None = None, *,
                   force: bool = False) -> Path | None:
    """写 ``<config>/growth/snapshots/<period>.json``；同周期已有且未 force 则跳过。

    返回写入（或已存在）的快照路径；写盘失败返回 None（不抛——快照是旁路动作，
    不能因为它把主循环打断）。
    """
    moment = moment or datetime.now().astimezone()
    period = snapshot_period(moment)
    target_dir = growth_dir(config_dir) / SNAPSHOT_DIRNAME
    target = target_dir / f"{period}.json"
    if target.exists() and not force:
        return target
    summary = build_summary(archive, moment)
    try:
        cwl.atomic_write_json(target, summary)
    except cwl.ConfigWriteError:
        logger.warning("周快照写盘失败: %s", target, exc_info=True)
        return None
    return target


def mark_emitted(store, moment: datetime | None = None) -> dict:
    """在档案里记下"本周快照已出"。"""
    period = snapshot_period(moment)
    stamp = now_iso(moment)

    def _mutate(archive: dict) -> None:
        archive.setdefault("snapshot", {})
        archive["snapshot"]["last_period"] = period
        archive["snapshot"]["last_at"] = stamp

    return store.update(_mutate)


def emit_if_due(store, moment: datetime | None = None) -> Path | None:
    """面板/每日检查调用：到点就出快照并记账。"""
    moment = moment or datetime.now().astimezone()
    archive = store.load()
    if not should_emit(archive, moment):
        return None
    path = write_snapshot(store.config_dir, archive, moment)
    if path is not None:
        mark_emitted(store, moment)
    return path


def list_snapshots(config_dir) -> list[Path]:
    target_dir = growth_dir(config_dir) / SNAPSHOT_DIRNAME
    if not target_dir.exists():
        return []
    return sorted(target_dir.glob("*.json"), key=lambda p: p.name)


def archive_path_of(config_dir, instance_id: str = "") -> Path:
    """便捷转出：供外部拼路径用。"""
    return archive_path(config_dir, instance_id)
