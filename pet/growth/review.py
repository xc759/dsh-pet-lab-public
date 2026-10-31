# -*- coding: utf-8 -*-
"""错题本排期：FSRS-6（``fsrs`` 包）与档案字段之间的转换层。

为什么单独一层：

* 档案里存的是**可读的 ISO 字符串与浮点数**，FSRS 要的是带时区的 ``datetime``
  与 ``Card`` 对象。转换只在这一个文件里做，别的地方不碰 ``fsrs`` 的类型。
* ``fsrs`` 是可选依赖（no-chat 打包变体、别人的开发环境都可能没装）。
  没装就退化成固定阶梯（0/1/3/7/15/30/60 天），**功能降级但不崩**——
  错题本照样能记、能复习，只是排期没那么聪明。
* 关闭随机抖动（``enable_fuzzing=False``）：桌宠的复习计划不该每次算出不同
  的下次到期日，否则测试不可复现，用户也会觉得"怎么今天又冒出来了"。

评分档位沿用 FSRS 四档：``again`` / ``hard`` / ``good`` / ``easy``。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: 四档评分（对外用字符串，内部映射到 ``fsrs.Rating``）
GRADES = ("again", "hard", "good", "easy")

#: 降级阶梯（天）：没装 fsrs 时按"答对就往后挪一格、答错回到起点"走
FALLBACK_STEPS = (0, 1, 3, 7, 15, 30, 60)

_scheduler = None
_scheduler_failed = False


def _get_scheduler():
    """懒加载 Scheduler；导入失败只试一次，避免每张卡都抛一次异常。"""
    global _scheduler, _scheduler_failed
    if _scheduler is not None or _scheduler_failed:
        return _scheduler
    try:
        from fsrs import Scheduler

        _scheduler = Scheduler(enable_fuzzing=False)
    except Exception:  # noqa: BLE001 - 缺依赖/版本不兼容都走降级
        logger.warning("fsrs 不可用，错题本排期降级为固定阶梯", exc_info=True)
        _scheduler_failed = True
        _scheduler = None
    return _scheduler


def available() -> bool:
    """是否在用真正的 FSRS 排期（面板上用来标注"简化排期"）。"""
    return _get_scheduler() is not None


def _to_utc(value) -> datetime | None:
    """档案里的本地 ISO 串 → UTC ``datetime``；空/坏值回 ``None``。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc)


def _to_local_iso(value: datetime | None) -> str | None:
    """UTC ``datetime`` → 本地带偏移 ISO（与 :func:`schema.now_iso` 同格式）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().replace(microsecond=0).isoformat()


def _now_utc(moment: datetime | None = None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc)


def _due_index(row) -> int:
    """降级模式用的阶梯下标（存在 ``step`` 里，Review 态借 ``reps`` 推）。"""
    try:
        step = int(row.get("step"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        step = 0
    return max(0, min(len(FALLBACK_STEPS) - 1, step))


def _fallback_schedule(row: dict, grade: str, moment: datetime | None) -> dict:
    now = moment or datetime.now()
    index = _due_index(row)
    if grade == "again":
        index = 0
        row["lapses"] = int(row.get("lapses") or 0) + 1
    elif grade == "hard":
        index = max(0, index)  # 原地踏步（间隔不缩短也不放大）
    else:
        index = min(len(FALLBACK_STEPS) - 1, index + 1)
    days = FALLBACK_STEPS[index]
    # 阶梯首级是 0 天：直接 due=now 会让卡片永远"到期"，所以首级按 10 分钟后算。
    due_at = now + timedelta(days=days) if days else now + timedelta(minutes=10)
    return {
        "state": 2 if (grade != "again" and int(row.get("reps") or 0) >= 1) else 1,
        "step": index,
        "stability": 0.0,
        "difficulty": 0.0,
        "reps": int(row.get("reps") or 0) + 1,
        "lapses": int(row.get("lapses") or 0) + (1 if grade == "again" else 0),
        "due": due_at.replace(microsecond=0).isoformat(),
        "last_review": now.replace(microsecond=0).isoformat(),
    }


def schedule(row: dict, grade: str, moment: datetime | None = None) -> dict | None:
    """对一张卡评一次分，返回需要写回档案的字段；评分非四档时返回 ``None``。"""
    key = str(grade or "").strip().lower()
    if key not in GRADES:
        return None
    scheduler = _get_scheduler()
    card_row = dict(row or {})
    if scheduler is None:
        return _fallback_schedule(card_row, key, moment)

    from fsrs import Card, Rating
    from fsrs.state import State

    review_at = _now_utc(moment)
    state_value = int(card_row.get("state") or 1)
    try:
        state = State(state_value)
    except ValueError:
        state = State.Learning
    step = card_row.get("step")
    try:
        step = int(step) if step is not None else None
    except (TypeError, ValueError):
        step = None
    stability = float(card_row.get("stability") or 0.0) or None
    difficulty = float(card_row.get("difficulty") or 0.0) or None
    last_review = _to_utc(card_row.get("last_review"))
    card = Card(
        state=state,
        step=step if state != State.Review else None,
        stability=stability,
        difficulty=difficulty,
        due=_to_utc(card_row.get("due")) or review_at,
        last_review=last_review,
    )
    try:
        updated, _log = scheduler.review_card(
            card, Rating[{"again": "Again", "hard": "Hard",
                          "good": "Good", "easy": "Easy"}[key]],
            review_datetime=review_at,
        )
    except Exception:  # noqa: BLE001 - 单张卡算崩不该带崩整个面板
        logger.warning("FSRS 排期失败，该卡退回固定阶梯", exc_info=True)
        return _fallback_schedule(card_row, key, moment)

    reps = int(card_row.get("reps") or 0) + 1
    lapses = int(card_row.get("lapses") or 0) + (1 if key == "again" else 0)
    return {
        "state": int(updated.state),
        "step": updated.step,
        "stability": round(float(updated.stability or 0.0), 4),
        "difficulty": round(float(updated.difficulty or 0.0), 4),
        "reps": reps,
        "lapses": lapses,
        "due": _to_local_iso(updated.due),
        "last_review": _to_local_iso(updated.last_review),
    }


def is_due(row, moment: datetime | None = None) -> bool:
    """到期没到期。没填 ``due`` 的卡（刚建、没评过）视为到期。"""
    due = _to_utc((row or {}).get("due"))
    if due is None:
        return True
    return due <= _now_utc(moment)


def due_rows(cards, limit: int | None = None, moment: datetime | None = None) -> list[dict]:
    """到期的卡，按到期时间从早到晚（最该复习的排前面）。"""
    rows = [r for r in (cards or []) if isinstance(r, dict) and is_due(r, moment)]
    rows.sort(key=lambda r: str(r.get("due") or ""))
    return rows[:limit] if limit else rows


def due_count(cards, moment: datetime | None = None) -> int:
    return len(due_rows(cards, moment=moment))


def next_due_text(cards, moment: datetime | None = None) -> str:
    """面板顶部那句话：今天几张待复习 / 下一张什么时候。"""
    pending = due_count(cards, moment)
    if pending:
        return f"今天 {pending} 张待复习"
    upcoming = []
    for row in cards or []:
        due = _to_utc((row or {}).get("due"))
        if due is not None:
            upcoming.append(due)
    if not upcoming:
        return "还没有错题"
    soonest = min(upcoming).astimezone()
    return "下次复习 " + soonest.strftime("%m-%d %H:%M")
