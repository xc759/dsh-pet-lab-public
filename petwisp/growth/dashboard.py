# -*- coding: utf-8 -*-
"""面板要显示的那点东西——**纯函数，不带 Qt**。

为什么单独一层：面板是唯一"人看得见"的地方，数字错一个用户立刻就会发现
（倒计时少一天、周合计把上周算进来）。把计算从控件里剥出来，就能在
pytest 里用固定档案把边界钉死，而不是靠肉眼点面板。

口径（与阶段B决策一致）：

* **倒计时按自然日差**，不含当天：今天 10-30、考试 10-31 → "还有 1 天"；
  当天显示"就是今天"；已过去显示"已过 N 天"，绝不出现负数糊在脸上。
* **周合计按周一起算**，与提醒里的"本周复盘"同一口径。
* **配比只在科目自报了权重时才算比例**；都没填就返回空比例，面板显示"未设置"，
  不拿"每周分钟数"去瞎换算，避免编数据。
"""
from __future__ import annotations

from datetime import date, datetime

from . import presence as presence_mod
from . import review as review_mod
from .schema import MAX_TEXT


def _parse_date(value) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def days_until(exam_date, today: date | None = None) -> int | None:
    """距考试日的自然日差；没填/填错返回 ``None``。"""
    target = _parse_date(exam_date)
    if target is None:
        return None
    base = today or date.today()
    return (target - base).days


def countdown_text(exam_date, today: date | None = None) -> str:
    """给人看的一句话倒计时。"""
    days = days_until(exam_date, today)
    if days is None:
        return "还没设置考试日"
    if days > 0:
        return f"还有 {days} 天"
    if days == 0:
        return "就是今天"
    return f"已过 {abs(days)} 天"


def daily_rows(archive) -> list[dict]:
    """``emotion.daily`` 里那些合法的每日记录（脏数据直接跳过）。"""
    emotion = archive.get("emotion") if isinstance(archive, dict) else None
    rows = (emotion or {}).get("daily") if isinstance(emotion, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict)]


#: 兼容旧调用点（本模块内部与已有测试都用过这个私有名）
_daily_rows = daily_rows


def _week_bounds(today: date) -> tuple[date, date]:
    start = date.fromordinal(today.toordinal() - today.weekday())
    return start, date.fromordinal(start.toordinal() + 6)


def week_focus_minutes(archive, today: date | None = None) -> int:
    """本周（周一~周日）累计专注分钟。"""
    base = today or date.today()
    start, end = _week_bounds(base)
    total = 0
    for row in _daily_rows(archive):
        day = _parse_date(row.get("date"))
        if day is None or day < start or day > end:
            continue
        try:
            total += max(0, int(row.get("focus_minutes") or 0))
        except (TypeError, ValueError):
            continue
    return total


def _as_int(value) -> int:
    """脏档案兜底：非数字/坏字符串一律当 0，别让面板整页挂掉。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def today_row(archive, today: date | None = None) -> dict:
    """今天的专注/分心/心情；没有记录时返回零行（不写盘）。"""
    key = (today or date.today()).isoformat()
    for row in _daily_rows(archive):
        if str(row.get("date") or "")[:10] == key:
            return {
                "focus_minutes": _as_int(row.get("focus_minutes")),
                "distractions": _as_int(row.get("distractions")),
                "mood": _as_int(row.get("mood")),
            }
    return {"focus_minutes": 0, "distractions": 0, "mood": 0}


def subject_ratios(subjects) -> list[dict]:
    """科目配比：优先用自报 weight；全为 0 时比例留空。"""
    rows = []
    for item in subjects or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:MAX_TEXT]
        if not name:
            continue
        rows.append({
            "name": name,
            "weekly_minutes": max(0, int(item.get("weekly_minutes") or 0)),
            "weight": max(0, int(item.get("weight") or 0)),
            "ratio": 0.0,
        })
    total = sum(r["weight"] for r in rows)
    if total > 0:
        for row in rows:
            row["ratio"] = round(row["weight"] / total, 4)
    return rows


def milestone_rows(archive, today: date | None = None) -> list[dict]:
    """里程碑：未完成在前，再按截止日（空的排后面），逾期单独标。"""
    base = today or date.today()
    learning = (archive.get("profile") or {}).get("learning") if isinstance(archive, dict) else {}
    rows = []
    for item in (learning or {}).get("milestones") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not item_id or not title:
            continue
        due = _parse_date(item.get("due"))
        rows.append({
            "id": item_id,
            "title": title[:120],
            "due": due.isoformat() if due else "",
            "done": bool(item.get("done")),
            "overdue": bool(due and not item.get("done") and due < base),
            "days_left": (due - base).days if due else None,
        })
    rows.sort(key=lambda r: (r["done"], r["due"] == "", r["due"] or "9999-12-31"))
    return rows


def _review_row(card: dict, moment: datetime) -> dict:
    """面板要显示的一张到期卡：题面 + 答案摘要 + 到期说法。

    档案里的 ``due`` 是本地带偏移的 ISO；但手工写档/老数据可能是 naive，
    这里一律先归到本地时区再比较，别让"naive 对 aware"把面板整个打挂。
    """
    now_local = moment if moment.tzinfo else moment.astimezone()
    due_text = "待复习"
    raw = str(card.get("due") or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            due_text = f"到期 {raw[:16].replace('T', ' ')}"
        else:
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            parsed = parsed.astimezone(now_local.tzinfo)
            if parsed.date() == now_local.date():
                due_text = "已到期" if parsed <= now_local else "今天到期"
            elif parsed < now_local:
                due_text = f"逾期 {parsed.strftime('%m-%d')}"
            else:
                due_text = f"到期 {parsed.strftime('%m-%d %H:%M')}"
    return {
        "id": str(card.get("id") or ""),
        "front": str(card.get("front") or "")[:120],
        "back": str(card.get("back") or "")[:80],
        "tags": [str(t) for t in (card.get("tags") or [])][:4],
        "due_text": due_text,
        "reps": max(0, int(card.get("reps") or 0)),
    }


def build_dashboard(archive, moment: datetime | None = None) -> dict:
    """一次性算出面板需要的全部数字。档案为空也能跑（返回零值，不抛）。"""
    moment = moment or datetime.now().astimezone()
    today = moment.date()
    archive = archive if isinstance(archive, dict) else {}
    profile = archive.get("profile") if isinstance(archive.get("profile"), dict) else {}
    learning = profile.get("learning") if isinstance(profile.get("learning"), dict) else {}
    emotion = archive.get("emotion") if isinstance(archive.get("emotion"), dict) else {}
    reviews = archive.get("reviews") if isinstance(archive.get("reviews"), dict) else {}
    knowledge = archive.get("knowledge") if isinstance(archive.get("knowledge"), dict) else {}

    stats = reviews.get("stats") if isinstance(reviews.get("stats"), dict) else {}
    cards = [c for c in (reviews.get("cards") or []) if isinstance(c, dict)]
    # 到期判定交给 review 层（精确到时刻）：这里再自己比一遍日期，
    # 会出现"今天上午答对了、排到今晚，面板却说还欠着"的错。
    due_list = review_mod.due_rows(cards, moment=moment)

    topics = [(str(k), int(v)) for k, v in (knowledge.get("topics") or {}).items()
              if isinstance(v, (int, float))]
    topics.sort(key=lambda kv: kv[1], reverse=True)

    weekly_target = max(0, int(learning.get("weekly_minutes") or 0))
    week_done = week_focus_minutes(archive, today)
    week_ratio = round(week_done / weekly_target, 4) if weekly_target else 0.0

    exam_date = str(learning.get("exam_date") or "")[:10]
    return {
        "today": today.isoformat(),
        "goal": str(learning.get("goal") or "").strip()[:MAX_TEXT],
        "exam_date": exam_date,
        "days_left": days_until(exam_date, today),
        "countdown": countdown_text(exam_date, today),
        "target_schools": [str(x) for x in (learning.get("target_schools") or [])][:10],
        "today_stats": today_row(archive, today),
        "week_focus": week_done,
        "weekly_target": weekly_target,
        "week_ratio": min(1.0, week_ratio),
        "streak_days": max(0, int(emotion.get("streak_days") or 0)),
        "energy": max(0, min(100, int(emotion.get("energy") or 0))),
        "emotion_state": str(emotion.get("state") or ""),
        "subjects": subject_ratios(learning.get("subjects")),
        "milestones": milestone_rows(archive, today),
        "reviews": {
            "total": len(cards),
            "due": len(due_list),
            "done": max(0, int(stats.get("done") or 0)),
            "again": max(0, int(stats.get("again") or 0)),
            "last_at": reviews.get("last_review_at"),
            "text": review_mod.next_due_text(cards, moment),
            "fsrs": review_mod.available(),
            "cards": [_review_row(c, moment) for c in due_list[:6]],
        },
        "topics": topics[:8],
        "presence": presence_mod.grade(archive, moment),
    }
