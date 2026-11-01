# -*- coding: utf-8 -*-
"""成长档案的结构定义与归一。

设计口径（阶段 B 决策落地）：

* **向前兼容**：``normalize_archive`` 只补齐/修正**已知键**，未知键原样保留——
  旧版本写下的字段不会因为新版本读一遍就消失。
* **类型不可信**：文件可能被手改。每个字段都过一遍清洗，类型不对就回落到默认值，
  绝不让一处脏数据把整个档案读崩。
* **容量有上限**：增量知识、复习卡、里程碑都设硬上限（超出丢最旧），避免
  长期使用后单文件无限膨胀。
* **时间统一带时区**：全部用本地时区的 ISO 字符串，排序即字符串比较。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path

#: 当前 schema 版本。字段含义变更需 +1，并补一条迁移。
SCHEMA_VERSION = 1

#: 档案放在 ``<Config.dir>/growth/`` 下，与 sessions / todo_items 平级。
GROWTH_DIRNAME = "growth"
ARCHIVE_FILENAME = "archive.json"
SNAPSHOT_DIRNAME = "snapshots"

#: 容量上限（超出丢最旧）
MAX_KNOWLEDGE = 400
MAX_REVIEW_CARDS = 5000
MAX_MILESTONES = 60
MAX_DAILY_ROWS = 400
MAX_SESSIONS_BY_CHARACTER = 32
#: 话题计数的词表上限（超出丢计数最小的，避免"什么词都记"撑爆单文件）
MAX_TOPICS = 40

#: 情绪状态机取值。``tired``/``distracted`` 走"轻推"，``low`` 走"只陪不催"。
EMOTION_STATES = ("energized", "calm", "focused", "tired", "distracted", "low")
DEFAULT_EMOTION_STATE = "calm"

#: 单条文本上限，防止一次粘贴把档案撑爆
MAX_TEXT = 500
MAX_LONG_TEXT = 2000


# ---------------------------------------------------------------- 基础工具
def now_iso(moment: datetime | None = None) -> str:
    """本地时区 ISO 秒级时间戳（带偏移，可直接字符串排序）。"""
    moment = moment or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.replace(microsecond=0).isoformat()


def _text(value, limit: int = MAX_TEXT) -> str:
    """任何值 → 去首尾空白的受限字符串；非字符串一律视作空。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:limit]


def _int(value, default: int = 0, *, low: int | None = None,
         high: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if low is not None and number < low:
        number = low
    if high is not None and number > high:
        number = high
    return number


def _bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _iso_or_empty(value) -> str:
    """只接受能被 ``datetime.fromisoformat`` 解析的字符串，其余回空串。"""
    text = _text(value, 64)
    if not text:
        return ""
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return ""
    return text


def _iso_or_none(value) -> str | None:
    text = _iso_or_empty(value)
    return text or None


def _iso_date(value) -> str:
    """接受 ``YYYY-MM-DD``；也接受完整 ISO 时间并截到日期。"""
    text = _text(value, 32)
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def new_id(prefix: str = "") -> str:
    """短 id：时间序 + 随机后缀，足够本地去重，不引入 uuid4 全串噪声。"""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    tail = uuid.uuid4().hex[:6]
    return f"{prefix}{stamp}-{tail}" if prefix else f"{stamp}-{tail}"


# ---------------------------------------------------------------- 路径
def growth_dir(config_dir) -> Path:
    return Path(config_dir) / GROWTH_DIRNAME


def archive_path(config_dir, instance_id: str = "") -> Path:
    """主档案路径：多开实例跟随 ``config-<id>.json`` / ``todo_items-<id>.json`` 惯例。"""
    name = f"archive-{instance_id}.json" if instance_id else ARCHIVE_FILENAME
    return growth_dir(config_dir) / name


# ---------------------------------------------------------------- 各段默认值
def default_learning() -> dict:
    """学习档案：考研模板字段全部预留，未填时表现为"空"，不伪造数据。"""
    return {
        "goal": "",                    # 阶段目标（自由文本，仅本机）
        "exam_date": "",               # 考试日，YYYY-MM-DD，用于倒计时
        "target_schools": [],          # 目标院校（有序，第一志愿在前）
        "subjects": [],                # [{"name","weekly_minutes","weight"}]
        "weekly_minutes": 0,           # 每周目标学习分钟
        "milestones": [],              # [{"id","title","due","done"}]
        "started_at": None,
        "updated_at": None,
    }


def default_life() -> dict:
    return {
        "sleep_at": "",       # HH:MM，静默期参考
        "wake_at": "",
        "notes": "",          # 生活备注（不参与对外同步）
        "updated_at": None,
    }


def default_emotion() -> dict:
    return {
        "state": DEFAULT_EMOTION_STATE,
        "energy": 70,                 # 0~100
        "streak_days": 0,
        "last_state_at": None,
        "daily": [],                  # [{"date","focus_minutes","distractions","mood"}]
        # 情绪信号计数（阶段 B2）：{state: 次数}。**只存计数**，不存触发它的原话。
        "mood_hits": {},
    }


def default_nudges() -> dict:
    """主动提醒的节流状态（阶段 B2）：只记"什么时候提过什么"，不记内容。"""
    return {
        "date": "",            # 最近一次提醒的日期（YYYY-MM-DD）
        "count": 0,            # 当天已提醒次数
        "last_at": None,
        "weekly_period": "",   # 已推送过周复盘的周期（2026-W44）
    }


def default_archive(moment: datetime | None = None) -> dict:
    stamp = now_iso(moment)
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": stamp,
        "updated_at": stamp,
        "device": {"instance_id": ""},
        "identity": {"character_id": "", "nickname": ""},
        "profile": {
            "learning": default_learning(),
            "life": default_life(),
            "updated_at": None,
        },
        "emotion": default_emotion(),
        "sessions": {"total": 0, "last_at": None, "by_character": {}},
        "knowledge": {"increments": [], "last_extract_at": None, "topics": {}},
        "reviews": {"cards": [], "last_review_at": None,
                    "stats": {"done": 0, "again": 0}},
        "snapshot": {"last_period": "", "last_at": None, "last_push_at": None},
        "nudges": default_nudges(),
        "migrations": [],
        "privacy": {"sync_summary_only": True, "sync_dir": SNAPSHOT_DIRNAME},
    }


# ---------------------------------------------------------------- 归一
def _clean_subjects(raw) -> list[dict]:
    out: list[dict] = []
    for item in _list(raw)[:16]:
        row = _dict(item)
        name = _text(row.get("name"), 40)
        if not name:
            continue
        out.append({
            "name": name,
            "weekly_minutes": _int(row.get("weekly_minutes"), 0, low=0, high=10080),
            "weight": _int(row.get("weight"), 0, low=0, high=100),
        })
    return out


def _clean_milestones(raw) -> list[dict]:
    out: list[dict] = []
    for item in _list(raw):
        row = _dict(item)
        title = _text(row.get("title"), 120)
        if not title:
            continue
        out.append({
            "id": _text(row.get("id"), 64) or new_id("ms-"),
            "title": title,
            "due": _iso_date(row.get("due")),
            "done": _bool(row.get("done")),
        })
    return out[-MAX_MILESTONES:]


def _clean_learning(raw) -> dict:
    base = default_learning()
    row = _dict(raw)
    base["goal"] = _text(row.get("goal"), MAX_LONG_TEXT)
    base["exam_date"] = _iso_date(row.get("exam_date"))
    base["target_schools"] = [_text(x, 60) for x in _list(row.get("target_schools"))[:10]
                              if _text(x, 60)]
    base["subjects"] = _clean_subjects(row.get("subjects"))
    base["weekly_minutes"] = _int(row.get("weekly_minutes"), 0, low=0, high=10080)
    base["milestones"] = _clean_milestones(row.get("milestones"))
    base["started_at"] = _iso_or_none(row.get("started_at"))
    base["updated_at"] = _iso_or_none(row.get("updated_at"))
    return base


def _clean_life(raw) -> dict:
    base = default_life()
    row = _dict(raw)
    base["sleep_at"] = _hhmm(row.get("sleep_at"))
    base["wake_at"] = _hhmm(row.get("wake_at"))
    base["notes"] = _text(row.get("notes"), MAX_LONG_TEXT)
    base["updated_at"] = _iso_or_none(row.get("updated_at"))
    return base


def _hhmm(value) -> str:
    text = _text(value, 8)
    if len(text) != 5 or text[2] != ":":
        return ""
    hour, _, minute = text.partition(":")
    if not (hour.isdigit() and minute.isdigit()):
        return ""
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        return ""
    return f"{int(hour):02d}:{int(minute):02d}"


def _clean_emotion(raw) -> dict:
    base = default_emotion()
    row = _dict(raw)
    state = _text(row.get("state"), 24)
    base["state"] = state if state in EMOTION_STATES else DEFAULT_EMOTION_STATE
    base["energy"] = _int(row.get("energy"), 70, low=0, high=100)
    base["streak_days"] = _int(row.get("streak_days"), 0, low=0, high=100000)
    base["last_state_at"] = _iso_or_none(row.get("last_state_at"))
    hits = _dict(row.get("mood_hits"))
    base["mood_hits"] = {state: _int(hits.get(state), 0, low=0, high=10 ** 9)
                        for state in EMOTION_STATES if _int(hits.get(state), 0, low=0) > 0}
    daily: list[dict] = []
    for item in _list(row.get("daily"))[-MAX_DAILY_ROWS:]:
        day = _dict(item)
        key = _iso_date(day.get("date"))
        if not key:
            continue
        daily.append({
            "date": key,
            "focus_minutes": _int(day.get("focus_minutes"), 0, low=0, high=1440),
            "distractions": _int(day.get("distractions"), 0, low=0, high=10000),
            "mood": _int(day.get("mood"), 0, low=-5, high=5),
        })
    base["daily"] = daily
    return base


def _clean_knowledge(raw) -> dict:
    row = _dict(raw)
    increments: list[dict] = []
    for item in _list(row.get("increments"))[-MAX_KNOWLEDGE:]:
        entry = _dict(item)
        text = _text(entry.get("text"), MAX_LONG_TEXT)
        if not text:
            continue
        increments.append({
            "id": _text(entry.get("id"), 64) or new_id("kb-"),
            "text": text,
            "tags": [_text(t, 24) for t in _list(entry.get("tags"))[:8] if _text(t, 24)],
            "source": _text(entry.get("source"), 40) or "chat",
            "created_at": _iso_or_none(entry.get("created_at")) or now_iso(),
        })
    topics = _dict(row.get("topics"))
    cleaned_topics: dict[str, int] = {}
    for name, count in list(topics.items())[:MAX_TOPICS]:
        key = _text(name, 24)
        if not key:
            continue
        value = _int(count, 0, low=0, high=10 ** 9)
        if value > 0:
            cleaned_topics[key] = value
    return {
        "increments": increments,
        "last_extract_at": _iso_or_none(row.get("last_extract_at")),
        # 话题计数（阶段 B2）：{词: 次数}，供"最近常聊"用；**不存原句**。
        "topics": cleaned_topics,
    }


def _clean_cards(raw) -> list[dict]:
    out: list[dict] = []
    for item in _list(raw)[-MAX_REVIEW_CARDS:]:
        card = _dict(item)
        front = _text(card.get("front"), MAX_TEXT)
        if not front:
            continue
        # FSRS 学习状态：1=Learning 2=Review 3=Relearning。Review 态的 step 必须为空
        # （fsrs 的 review_card 在 Review 分支会断言 step is None），这里顺手钳死。
        state_value = _int(card.get("state"), 1, low=1, high=3)
        step_value = None if state_value == 2 else _int(card.get("step"), 0, low=0, high=16)
        out.append({
            "id": _text(card.get("id"), 64) or new_id("rc-"),
            "front": front,
            "back": _text(card.get("back"), MAX_LONG_TEXT),
            "tags": [_text(t, 24) for t in _list(card.get("tags"))[:8] if _text(t, 24)],
            # FSRS 字段（B3 计算，这里只负责存取；坏值一律回落默认）
            "state": state_value,
            "step": step_value,
            "stability": _float(card.get("stability"), 0.0),
            "difficulty": _float(card.get("difficulty"), 0.0),
            "reps": _int(card.get("reps"), 0, low=0, high=100000),
            "lapses": _int(card.get("lapses"), 0, low=0, high=100000),
            "due": _iso_or_none(card.get("due")),
            "last_review": _iso_or_none(card.get("last_review")),
            "created_at": _iso_or_none(card.get("created_at")) or now_iso(),
        })
    return out


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_reviews(raw) -> dict:
    row = _dict(raw)
    stats = _dict(row.get("stats"))
    return {
        "cards": _clean_cards(row.get("cards")),
        "last_review_at": _iso_or_none(row.get("last_review_at")),
        "stats": {
            "done": _int(stats.get("done"), 0, low=0),
            "again": _int(stats.get("again"), 0, low=0),
        },
    }


def _clean_sessions(raw) -> dict:
    row = _dict(raw)
    by_character: dict[str, int] = {}
    for key, value in list(_dict(row.get("by_character")).items())[:MAX_SESSIONS_BY_CHARACTER]:
        name = _text(key, 40)
        if name:
            by_character[name] = _int(value, 0, low=0)
    return {
        "total": _int(row.get("total"), 0, low=0),
        "last_at": _iso_or_none(row.get("last_at")),
        "by_character": by_character,
    }


def _clean_snapshot(raw) -> dict:
    row = _dict(raw)
    return {
        "last_period": _text(row.get("last_period"), 16),
        "last_at": _iso_or_none(row.get("last_at")),
        "last_push_at": _iso_or_none(row.get("last_push_at")),
    }


def _clean_nudges(raw) -> dict:
    row = _dict(raw)
    return {
        "date": _iso_date(row.get("date")),
        "count": _int(row.get("count"), 0, low=0, high=1000),
        "last_at": _iso_or_none(row.get("last_at")),
        "weekly_period": _text(row.get("weekly_period"), 16),
    }


def _clean_migrations(raw) -> list[dict]:
    out: list[dict] = []
    for item in _list(raw)[-32:]:
        row = _dict(item)
        out.append({
            "from": _int(row.get("from"), 0, low=0),
            "to": _int(row.get("to"), 0, low=0),
            "at": _iso_or_none(row.get("at")) or now_iso(),
        })
    return out


def normalize_archive(raw, *, moment: datetime | None = None) -> dict:
    """把盘上读到的任意字典归一成合法档案。

    **保留未知键**（``result = dict(raw)`` 起步），已知键逐个清洗覆盖。
    入参不是 dict 时直接从默认结构开始。
    """
    source = _dict(raw)
    result = dict(source)
    default = default_archive(moment)

    # 版本号保留原值（含 0 = 老文件没有版本字段），迁移才看得见"从哪来"
    version = _int(source.get("schema_version"), SCHEMA_VERSION, low=0, high=SCHEMA_VERSION)
    result["schema_version"] = version
    result["created_at"] = _iso_or_none(source.get("created_at")) or default["created_at"]
    result["updated_at"] = _iso_or_none(source.get("updated_at")) or default["updated_at"]

    device = _dict(source.get("device"))
    cleaned_device = dict(device)
    cleaned_device["instance_id"] = _text(device.get("instance_id"), 40)
    result["device"] = cleaned_device

    identity = _dict(source.get("identity"))
    result["identity"] = {
        "character_id": _text(identity.get("character_id"), 60),
        "nickname": _text(identity.get("nickname"), 40),
    }

    profile = _dict(source.get("profile"))
    result["profile"] = {
        "learning": _clean_learning(profile.get("learning")),
        "life": _clean_life(profile.get("life")),
        "updated_at": _iso_or_none(profile.get("updated_at")),
    }
    # profile 内的未知子键也保留
    for key, value in profile.items():
        if key not in ("learning", "life", "updated_at"):
            result["profile"][key] = value

    result["emotion"] = _clean_emotion(source.get("emotion"))
    result["sessions"] = _clean_sessions(source.get("sessions"))
    result["knowledge"] = _clean_knowledge(source.get("knowledge"))
    result["reviews"] = _clean_reviews(source.get("reviews"))
    result["snapshot"] = _clean_snapshot(source.get("snapshot"))
    result["nudges"] = _clean_nudges(source.get("nudges"))
    result["migrations"] = _clean_migrations(source.get("migrations"))

    privacy = _dict(source.get("privacy"))
    result["privacy"] = {
        "sync_summary_only": _bool(privacy.get("sync_summary_only"), True),
        "sync_dir": _text(privacy.get("sync_dir"), 40) or SNAPSHOT_DIRNAME,
    }
    return result
