"""主动性调度器：三类触发 + 三条独立开关（阶段 B3）。

阶段B 把"主动性"拆成三类，各自独立开关、独立持久化（config.json）：

===========  ==========  =============================  ==========
类别          开关键       默认                            实际行为
===========  ==========  =============================  ==========
催事 chore    ``growth_proactive_chore_enabled``   开    今天没记录专注，白天提一句
复盘 review   ``growth_proactive_review_enabled``  开    周日晚上提一句看周复盘
关怀 care     ``growth_proactive_care_enabled``   关    当天心情很低时，说一句陪着
===========  ==========  =============================  ==========

三条约束（比"能不能提醒"更重要）：

1. **23:30–07:30 硬静默**——在 :func:`plan` 的第一行就判，任何开关都翻不过去；
2. **宁少不多**——一天总打扰上限、两次最小间隔沿用 :mod:`pet.growth.nudge`，
   关怀另外限成一天最多一次；
3. **关掉的类不占机会**——判定发生在被关类之前，不是事后过滤结果（见
   ``nudge.plan(allow=...)``），否则周日晚关掉复盘会连催事一起吞掉。

本模块是纯逻辑：不碰 Qt、不读文件，开关由调用方从 config 读进来，时间由调用方
传入——"半夜别说话""关了就别响"这类规则才好写成断言而不是靠手测。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import nudge
from .nudge import in_quiet_window

#: 三类触发 → 开关键
KEY_CHORE = "growth_proactive_chore_enabled"
KEY_REVIEW = "growth_proactive_review_enabled"
KEY_CARE = "growth_proactive_care_enabled"

KIND_CHORE = "chore"
KIND_REVIEW = "review"
KIND_CARE = "care"

KIND_KEYS = {KIND_CHORE: KEY_CHORE, KIND_REVIEW: KEY_REVIEW, KIND_CARE: KEY_CARE}
KEY_DEFAULTS = {KEY_CHORE: True, KEY_REVIEW: True, KEY_CARE: False}
KEY_LABELS = {KIND_CHORE: "催事", KIND_REVIEW: "复盘", KIND_CARE: "状态关怀"}

#: 关怀的可说话时段（比催事窄：这类话只适合人的清醒时段）
CARE_WINDOW = ("09:00", "22:00")
#: 关怀一天最多一次，且与上一条提醒至少隔这么久
MAX_CARE_PER_DAY = 1
CARE_MIN_GAP_MINUTES = 240


def _as_bool(value, default: bool) -> bool:
    """与 ``config._bool_or_default`` 同口径：防 ``bool("false") is True``。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
    return default


@dataclass(frozen=True)
class Switches:
    """三类触发的开关快照。"""

    chore: bool = True
    review: bool = True
    care: bool = False

    @classmethod
    def from_config(cls, config) -> "Switches":
        """从配置读三类开关；``config`` 为 None 或取值异常时用默认值。"""

        def flag(key: str) -> bool:
            default = KEY_DEFAULTS[key]
            if config is None:
                return default
            try:
                return _as_bool(config.get(key, default), default)
            except Exception:  # noqa: BLE001 - 配置对象异常不该让提醒整条链路炸掉
                return default

        return cls(chore=flag(KEY_CHORE), review=flag(KEY_REVIEW), care=flag(KEY_CARE))

    def enabled(self, kind: str) -> bool:
        if kind == KIND_CHORE:
            return bool(self.chore)
        if kind == KIND_REVIEW:
            return bool(self.review)
        if kind == KIND_CARE:
            return bool(self.care)
        raise KeyError(kind)

    def as_dict(self) -> dict:
        return {KIND_CHORE: self.chore, KIND_REVIEW: self.review, KIND_CARE: self.care}


def set_switch(config, kind: str, enabled: bool) -> bool:
    """把某一类开关写进配置并落盘；未知 kind 抛 ``KeyError``。

    返回写入后的布尔值。``config`` 为空时直接抛 ``TypeError``——宁可让调用方
    当场知道开关没落盘，也不要静默成功。
    """
    key = KIND_KEYS[kind]
    if config is None:
        raise TypeError("config 不可为空：开关必须持久化")
    config.set(key, bool(enabled))
    try:
        config.save()
    except Exception:  # noqa: BLE001 - set() 内部可能已落盘，保存失败交给上层日志
        pass
    return bool(enabled)


def _care_allowed(archive: dict, moment: datetime, today_count: int) -> bool:
    """关怀的额外节流：时段、一天一次、与上一条提醒间隔。"""
    if not nudge.in_window(moment, CARE_WINDOW):
        return False
    if today_count >= MAX_CARE_PER_DAY:
        return False
    gap = nudge._minutes_since(  # noqa: SLF001 - 同包私有工具，避免复制一份时间解析
        (archive.get("nudges") or {}).get("last_at", ""), moment
    )
    if gap is not None and gap < CARE_MIN_GAP_MINUTES:
        return False
    return True


def _plan_care(archive: dict, moment: datetime, today_count: int) -> dict | None:
    """当天心情很低时的一句陪伴（不催、不布置任务）。"""
    row = nudge._today_row(archive, moment.date().isoformat())  # noqa: SLF001
    if int(row.get("mood") or 0) > nudge.LOW_MOOD_SKIP:
        # 心情没到阈值就闭嘴：这类话只在真的低落时才有意义，平时说显得假。
        return None
    if not _care_allowed(archive, moment, today_count):
        return None
    text = "今天状态不太好就别硬撑了，先起来走两步、喝口水，我在这儿陪着。"
    days = nudge._days_to_exam(archive, moment)  # noqa: SLF001
    if days is not None and days > 0:
        text = f"离考试还有 {days} 天，不急这一晚。今天先照顾好自己，进度我帮你记着。"
    return {"kind": KIND_CARE, "text": text}


def plan(
    archive: dict,
    switches: Switches | None = None,
    moment: datetime | None = None,
) -> dict | None:
    """返回 ``{"kind","text"}``（kind ∈ study/weekly/care）或 ``None``。

    kind 沿用 B2 的 ``study`` / ``weekly``（不在此层改名，免得下游气泡与既有测试
    按 kind 分支的代码跟着变），新增的只有 ``care``。

    ``switches`` 缺省取默认值（催事/复盘开、关怀关），与阶段B决策一致。
    """
    moment = moment or datetime.now().astimezone()
    archive = archive or {}
    switches = switches if switches is not None else Switches()

    life = (archive.get("profile") or {}).get("life") or {}
    if in_quiet_window(moment, life.get("sleep_at", ""), life.get("wake_at", "")):
        # 硬静默：先于任何开关判定，开关开到天上去也不在这段时间说话。
        return None

    allow = set()
    if switches.chore:
        allow.add("study")
    if switches.review:
        allow.add("weekly")
    base = nudge.plan(archive, moment, allow=allow)
    if base is not None:
        return base

    if not switches.care:
        return None
    nudges = archive.get("nudges") or {}
    same_day = nudges.get("date") == moment.date().isoformat()
    today_count = int(nudges.get("count") or 0) if same_day else 0
    if today_count >= nudge.MAX_STUDY_NUDGES_PER_DAY:
        # 一天总打扰上限对三类一体生效：关怀不额外开后门。
        return None
    return _plan_care(archive, moment, today_count)


def describe(switches: Switches) -> str:
    """给设置页/日志看的一行摘要。"""
    parts = []
    for kind in (KIND_CHORE, KIND_REVIEW, KIND_CARE):
        parts.append(f"{KEY_LABELS[kind]}{'开' if switches.enabled(kind) else '关'}")
    return " / ".join(parts)
