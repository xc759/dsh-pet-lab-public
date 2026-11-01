# -*- coding: utf-8 -*-
"""养成表现：把档案 metrics 折成一个"状态档位"。

阶段 B 的红线是**不新增美术资源、不改阶段 A 的物理与渲染**，所以这一层只做两件事：

1. 算出"今天这人是在冲刺 / 稳步 / 该歇口气"——纯函数，可单测；
2. 给调用方一个 ``action`` 提示（复用既有动作名 ``idle`` / ``speak``），
   由界面层决定要不要真触发，逻辑层不碰 Qt。

它不产生任何新数字：分钟、连续天数、情绪全部来自 ``dashboard``，口径完全一致，
避免"面板说 45 分钟、状态说没学习"这种自相矛盾。
"""
from __future__ import annotations

from datetime import date, datetime

from .dashboard import daily_rows, today_row

#: 档位 → 文案与语气。语气只影响气泡/对话用词，不新增素材。
TIERS = {
    "sprint": {"label": "冲刺中", "tone": "celebrate", "action": "speak"},
    "steady": {"label": "稳步推进", "tone": "steady", "action": "idle"},
    "rest": {"label": "该歇口气", "tone": "gentle", "action": "idle"},
    "unknown": {"label": "还没开始记录", "tone": "steady", "action": "idle"},
}

#: 连续多少天没有学习记录算"该歇口气"
IDLE_DAYS = 3


def _recent_idle_days(archive: dict, today) -> int:
    """从今天往回数，连续多少天没有专注分钟（只看每日记录，不猜）。"""
    by_date = {}
    for row in daily_rows(archive):
        key = str(row.get("date") or "")[:10]
        if key:
            try:
                by_date[key] = int(row.get("focus_minutes") or 0)
            except (TypeError, ValueError):
                by_date[key] = 0
    idle = 0
    offset = 0
    while offset < 30:
        day = date.fromordinal(today.toordinal() - offset).isoformat()
        if by_date.get(day, 0) > 0:
            break
        idle += 1
        offset += 1
    return idle


def _recent_mood(archive: dict) -> int | None:
    """最近一条情绪值；没有就 ``None``（不拿 0 冒充"情绪一般"）。"""
    daily = (archive.get("emotion") or {}).get("daily") or []
    for row in reversed(daily):
        if isinstance(row, dict) and isinstance(row.get("mood"), (int, float)):
            return int(row["mood"])
    return None


def grade(archive, moment: datetime | None = None) -> dict:
    """返回 ``{"tier", "label", "tone", "action", "progress", "reason"}``。

    ``progress`` 是今日目标完成度（0~1，未设目标时为 0）；``reason`` 是给日志/面板
    用的一句话依据，方便日后排查"为什么它今天不吭声"。
    """
    moment = moment or datetime.now().astimezone()
    archive = archive if isinstance(archive, dict) else {}
    today_minutes = int(today_row(archive, moment.date()).get("focus_minutes") or 0)
    learning = (archive.get("profile") or {}).get("learning") or {}
    try:
        weekly_target = max(0, int(learning.get("weekly_minutes") or 0))
    except (TypeError, ValueError):
        weekly_target = 0
    daily_target = weekly_target / 7 if weekly_target else 0
    progress = round(min(1.0, today_minutes / daily_target), 3) if daily_target else 0.0
    try:
        streak = max(0, int((archive.get("emotion") or {}).get("streak_days") or 0))
    except (TypeError, ValueError):
        streak = 0
    idle_days = _recent_idle_days(archive, moment.date())
    mood = _recent_mood(archive)

    has_log = bool(daily_rows(archive))
    if not has_log and streak <= 0:
        tier = "unknown"
        reason = "档案里还没有学习记录"
    elif idle_days >= IDLE_DAYS or (mood is not None and mood <= -1):
        tier = "rest"
        reason = f"连续 {idle_days} 天没记录" if idle_days >= IDLE_DAYS else f"最近情绪 {mood}"
    elif progress >= 0.8 or streak >= 21:
        tier = "sprint"
        reason = f"今日完成 {int(progress * 100)}%" if progress >= 0.8 else f"连续 {streak} 天"
    elif today_minutes > 0 or streak > 0:
        tier = "steady"
        reason = f"今日 {today_minutes} 分钟"
    else:
        tier = "steady"
        reason = "今天还没记"

    spec = TIERS[tier]
    return {
        "tier": tier,
        "label": spec["label"],
        "tone": spec["tone"],
        "action": spec["action"],
        "progress": progress,
        "reason": reason,
    }
