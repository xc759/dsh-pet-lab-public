"""主动提醒的「该不该开口、开口说什么」（阶段 B2）。

阶段 B 的主动性决策在这里落成代码：

* **A（学习提醒）开**：今天一次学习都没记录，就在白天轻轻提一句；
* **C（周复盘）开**：周日晚上提醒看一眼周复盘；
* **B（社交型搭话）关**：本模块不产生"没事找话"的提醒，一条路径都不留。

两条自我约束（比"能不能提醒"更重要）：

1. **23:30–07:30 静默**，可由 ``profile.life.sleep_at / wake_at`` 覆盖；
2. **宁少不多**：一天最多 2 次、间隔 ≥150 分钟；当天心情已经很低（mood ≤ -2）
   时一次都不提——那种时候需要的是陪着，不是催。

本模块是纯逻辑（无 Qt、无 IO），时间由调用方传入，便于把"半夜别说话"这类
规则写成断言而不是靠手测。
"""
from __future__ import annotations

from datetime import datetime, time as dtime

from .schema import now_iso

#: 默认静默窗口（含起点，不含终点：23:30 开始，07:30 结束）
DEFAULT_QUIET_START = "23:30"
DEFAULT_QUIET_END = "07:30"

#: 学习提醒的可说话时段
STUDY_WINDOW = ("09:00", "21:30")
#: 周复盘提醒的可说话时段（周日）
WEEKLY_WINDOW = ("20:00", "22:30")

#: 一天最多提几次学习提醒、两次之间至少隔多久
MAX_STUDY_NUDGES_PER_DAY = 2
MIN_STUDY_GAP_MINUTES = 150

#: 当日心情低于等于此值时完全不催
LOW_MOOD_SKIP = -2

#: 非静默期最多多久回来看一眼（定时器节拍，不是提醒频率）
MAX_CHECK_INTERVAL_SECONDS = 30 * 60

#: 本模块可能产出的提醒种类（阶段B3 的三类开关按此过滤）
ALLOWED_KINDS = frozenset({"study", "weekly"})

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def parse_hhmm(text: str, fallback: dtime) -> dtime:
    """``HH:MM`` → ``time``；解析不了用兜底值（档案可能被手改坏）。"""
    raw = str(text or "").strip()
    if len(raw) != 5 or raw[2] != ":":
        return fallback
    hour, _, minute = raw.partition(":")
    if not (hour.isdigit() and minute.isdigit()):
        return fallback
    h, m = int(hour), int(minute)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return fallback
    return dtime(h, m)


def in_quiet_window(moment: datetime, sleep_at: str = "", wake_at: str = "") -> bool:
    """是否处在静默窗口。窗口允许跨零点（23:30→07:30）。"""
    start = parse_hhmm(sleep_at, parse_hhmm(DEFAULT_QUIET_START, dtime(23, 30)))
    end = parse_hhmm(wake_at, parse_hhmm(DEFAULT_QUIET_END, dtime(7, 30)))
    now = moment.time()
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def in_window(moment: datetime, window: tuple[str, str]) -> bool:
    start = parse_hhmm(window[0], dtime(0, 0))
    end = parse_hhmm(window[1], dtime(23, 59))
    return start <= moment.time() <= end


def _today_row(archive: dict, day: str) -> dict:
    rows = (archive.get("emotion") or {}).get("daily") or []
    for row in rows:
        if row.get("date") == day:
            return row
    return {}


def _minutes_since(last_iso: str, moment: datetime) -> float | None:
    if not last_iso:
        return None
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.astimezone()
    return (moment - last).total_seconds() / 60.0


def _days_to_exam(archive: dict, moment: datetime) -> int | None:
    exam = str(((archive.get("profile") or {}).get("learning") or {}).get("exam_date") or "")
    if not exam:
        return None
    try:
        target = datetime.fromisoformat(exam[:10]).date()
    except ValueError:
        return None
    return (target - moment.date()).days


def plan(
    archive: dict,
    moment: datetime | None = None,
    *,
    allow: frozenset[str] | set[str] | None = None,
) -> dict | None:
    """返回 ``{"kind","text"}`` 或 ``None``（不该说话）。

    ``allow`` 限定本次允许哪几类提醒（``"study"`` / ``"weekly"``），缺省全开，
    与 B2 行为逐位一致；三类开关的判定在 :mod:`petwisp.growth.proactive`。
    """
    moment = moment or datetime.now().astimezone()
    archive = archive or {}
    if allow is None:
        allow = ALLOWED_KINDS
    else:
        allow = frozenset(allow)
    life = (archive.get("profile") or {}).get("life") or {}
    if in_quiet_window(moment, life.get("sleep_at", ""), life.get("wake_at", "")):
        return None

    nudges = archive.get("nudges") or {}
    day = moment.date().isoformat()
    same_day = nudges.get("date") == day
    today_count = int(nudges.get("count") or 0) if same_day else 0

    # 阶段B3：哪类提醒可以开口由上层（growth/proactive.py）的三类开关决定。
    # 被关掉的分支要在**这里**跳过、而不是事后过滤结果——否则高优先级的复盘被
    # 关掉时会把同一次机会整段吞掉，催事跟着一起不响。
    if "weekly" in allow:
        weekly = _plan_weekly(archive, moment, nudges)
        if weekly is not None:
            return weekly

    if "study" not in allow:
        return None
    if not in_window(moment, STUDY_WINDOW):
        return None
    if today_count >= MAX_STUDY_NUDGES_PER_DAY:
        return None
    gap = _minutes_since(nudges.get("last_at", ""), moment)
    if gap is not None and gap < MIN_STUDY_GAP_MINUTES:
        return None
    row = _today_row(archive, day)
    if int(row.get("focus_minutes") or 0) > 0:
        return None
    if int(row.get("mood") or 0) <= LOW_MOOD_SKIP:
        return None

    learning = (archive.get("profile") or {}).get("learning") or {}
    if not (learning.get("goal") or learning.get("exam_date") or learning.get("subjects")):
        # 连目标都还没填，先别催学习——那是问卷该干的事，不是提醒。
        return None

    days = _days_to_exam(archive, moment)
    if days is not None and days > 0:
        text = f"距考试还有 {days} 天。今天还没记录学习，先来 25 分钟？"
    else:
        text = "今天还没记录学习，先来 25 分钟？"
    return {"kind": "study", "text": text}


def _week_facts(archive: dict, moment: datetime) -> dict:
    """周复盘的三个附加事实：错题卡数、话题数、本周完成率。

    **只数档案里已有的东西**，一个没有就不提，绝不为了句子好看编数字。
    """
    reviews = archive.get("reviews") or {}
    cards = [c for c in (reviews.get("cards") or []) if isinstance(c, dict)]
    topics = (archive.get("knowledge") or {}).get("topics") or {}
    learning = (archive.get("profile") or {}).get("learning") or {}
    try:
        target = max(0, int(learning.get("weekly_minutes") or 0))
    except (TypeError, ValueError):
        target = 0
    week_minutes = 0
    start_day = moment.date().toordinal() - 6
    for row in (archive.get("emotion") or {}).get("daily") or []:
        if not isinstance(row, dict):
            continue
        try:
            ordinal = datetime.fromisoformat(str(row.get("date"))).date().toordinal()
        except ValueError:
            continue
        if start_day <= ordinal <= moment.date().toordinal():
            try:
                week_minutes += max(0, int(row.get("focus_minutes") or 0))
            except (TypeError, ValueError):
                continue
    due = 0
    for card in cards:
        raw = str(card.get("due") or "").strip()
        if not raw:
            continue
        try:
            due_at = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if due_at.tzinfo is None:
            due_at = due_at.astimezone()
        if due_at <= moment:
            due += 1
    return {
        "cards": len(cards),
        "due": due,
        "topics": len(topics),
        "top_topic": (max(topics.items(), key=lambda kv: kv[1])[0] if topics else ""),
        "week_minutes": week_minutes,
        "target": target,
    }


def _week_tip(facts: dict) -> str:
    """下周建议：按"有没有欠账 → 目标差多少"排优先级，给一句能执行的话。"""
    if facts["due"] > 0:
        return "下周先把到期的错题清掉"
    if facts["target"] and facts["week_minutes"] < facts["target"] * 0.6:
        return "下周把每周目标往上顶一点"
    if facts["target"] and facts["week_minutes"] >= facts["target"]:
        return "下周保持这个节奏就行"
    return "下周挑一两件具体的事定下来"


def _plan_weekly(archive: dict, moment: datetime, nudges: dict) -> dict | None:
    """周日晚上提醒看周复盘；同一周期只提一次。"""
    if moment.weekday() != 6 or not in_window(moment, WEEKLY_WINDOW):
        return None
    from .snapshot import snapshot_period

    period = snapshot_period(moment)
    if nudges.get("weekly_period") == period:
        return None
    facts = _week_facts(archive, moment)
    week_minutes = facts["week_minutes"]
    bits: list[str] = []
    if week_minutes > 0:
        bits.append(f"这周记录了 {week_minutes} 分钟专注")
    else:
        bits.append("这周还没记录专注时间")
    if facts["cards"]:
        tail = f"{facts['cards']} 张错题卡"
        if facts["due"]:
            tail += f"（{facts['due']} 张已到期）"
        bits.append(tail)
    if facts["topics"]:
        bits.append(f"{facts['topics']} 个话题在跟")
    text = "，".join(bits) + "。"
    text += _week_tip(facts) + "，要不要花两分钟看下周复盘？"
    return {"kind": "weekly", "text": text}


def mark(archive: dict, plan_result: dict, moment: datetime | None = None) -> None:
    """把"提过了"写回档案（原地改，调用方负责落盘）。"""
    moment = moment or datetime.now().astimezone()
    nudges = archive.setdefault("nudges", {})
    day = moment.date().isoformat()
    if nudges.get("date") != day:
        nudges["date"] = day
        nudges["count"] = 0
    nudges["count"] = int(nudges.get("count") or 0) + 1
    nudges["last_at"] = now_iso(moment)
    if plan_result.get("kind") == "weekly":
        from .snapshot import snapshot_period

        nudges["weekly_period"] = snapshot_period(moment)


def next_delay_seconds(moment: datetime | None = None, archive: dict | None = None) -> int:
    """下一次"要不要开口"的检查间隔（秒）——给调用方排**单次**定时。

    桌宠是常驻进程，秒级轮询是白烧 CPU（阶段 A 红线 3%）。这里把"下次什么时候
    值得看一眼"算成一次延时：处在静默窗口就一觉睡到起床时刻，否则最多半小时
    回来一次。间隔只是**检查节拍**，真正的开口频率由 :func:`plan` 的节流决定。
    """
    moment = moment or datetime.now().astimezone()
    life = ((archive or {}).get("profile") or {}).get("life") or {}
    sleep_at = str(life.get("sleep_at") or "")
    wake_at = str(life.get("wake_at") or "")
    if not in_quiet_window(moment, sleep_at, wake_at):
        return MAX_CHECK_INTERVAL_SECONDS
    wake = parse_hhmm(wake_at, parse_hhmm(DEFAULT_QUIET_END, dtime(7, 30)))
    target = moment.replace(hour=wake.hour, minute=wake.minute, second=0, microsecond=0)
    if target <= moment:
        from datetime import timedelta

        target = target + timedelta(days=1)
    return max(60, int((target - moment).total_seconds()))


def weekday_name(moment: datetime) -> str:
    return _WEEKDAYS[moment.weekday()]
