# -*- coding: utf-8 -*-
"""成长档案的进程级服务：桌宠启动时装配一次，其余模块通过它读写。

设计要点：

* **不轮询**。桌面宠物是常驻进程，任何 ``QTimer`` 轮询都会吃掉 CPU 预算。
  本服务只在两种时机动作：启动装配一次；外部（提醒服务 / 面板打开 / 对话开始）
  调用 :meth:`poke` 时做一次"日期变了没有"的廉价判断。
* **无 Qt 依赖**。整层是纯 Python，方便在测试里直接实例化，无须 QApplication。
* **写盘失败不致命**。返回 ``None`` 并记日志，调用方自行决定要不要提示用户。
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime
from typing import Callable

from . import migrate as migrate_mod
from . import nudge as nudge_mod
from . import perceive
from . import extract as extract_mod
from . import proactive as proactive_mod
from . import review as review_mod
from .schema import (
    DEFAULT_EMOTION_STATE,
    EMOTION_STATES,
    MAX_DAILY_ROWS,
    MAX_MILESTONES,
    MAX_REVIEW_CARDS,
    MAX_TOPICS,
    _clean_subjects,
    _iso_date,
    new_id,
    now_iso,
)
from .snapshot import emit_if_due, build_summary
from . import presence as presence_mod
from . import snapshot as snapshot_mod
from . import sync as sync_mod
from .store import GrowthStore

logger = logging.getLogger(__name__)


class GrowthService:
    """成长档案的门面。"""

    def __init__(self, config=None, *, store: GrowthStore | None = None) -> None:
        self.config = config
        if store is not None:
            self.store = store
        else:
            config_dir = getattr(config, "dir", None)
            if config_dir is None:
                raise ValueError("GrowthService 需要 config（带 .dir）或显式传入 store")
            self.store = GrowthStore(config_dir, getattr(config, "instance_id", ""))
        self._last_poke_date: str = ""
        self._listeners: list[Callable[[dict], None]] = []
        # 注入文本短缓存：一次发送可能被构建多次（重试/预览），别每次都读盘。
        self._injection_cache: tuple[float, str, int] | None = None
        # 文本感知去重：{sha1 前 16 位: 上报时刻}
        self._recent_texts: dict[str, float] = {}

    # ------------------------------------------------------------ 生命周期
    def start(self) -> dict:
        """启动装配：确保档案存在 → 需要时迁移 → 顺手检查周快照。"""
        archive = self.store.ensure()
        try:
            if migrate_mod.needs_migration(self.store):
                archive = migrate_mod.migrate(self.store, config=self.config)
        except Exception:  # noqa: BLE001 - 迁移失败不能拦住桌宠启动
            logger.warning("成长档案迁移失败，继续用现有档案", exc_info=True)
            archive = self.store.load()
        self._last_poke_date = date.today().isoformat()
        self._emit_due_snapshot()
        return archive

    def poke(self) -> None:
        """外部轻量心跳（提醒服务每次 tick 调一次即可，不需要自己的定时器）。"""
        today = date.today().isoformat()
        if today == self._last_poke_date:
            return
        self._last_poke_date = today
        self._emit_due_snapshot()

    def _emit_due_snapshot(self) -> None:
        path = None
        try:
            path = emit_if_due(self.store)
        except Exception:  # noqa: BLE001 - 快照是旁路
            logger.warning("周快照生成失败", exc_info=True)
        if path is not None:
            self.sync_snapshot()

    def sync_snapshot(self, moment: datetime | None = None) -> dict:
        """把快照同步到独立私有仓（未配置就是 no-op）。

        只在**本周新出了快照**或上次推送失败时才值得跑；这里不额外记账，
        因为 ``sync`` 自身会先比对文件内容——没改动就 ``nothing``，不会空提交。
        """
        repo_dir = str(self.config.get("growth_snapshot_repo", "") or "")
        if not repo_dir:
            return {"status": "skipped", "detail": "未配置"}
        try:
            period = snapshot_mod.snapshot_period(moment or datetime.now().astimezone())
            result = sync_mod.sync(self.config.dir, repo_dir, period)
        except Exception:  # noqa: BLE001 - 外发是旁路，失败不阻塞
            logger.warning("快照同步失败", exc_info=True)
            return {"status": "failed", "detail": "异常"}
        if result.get("status") not in ("pushed", "nothing", "skipped"):
            logger.info("快照同步未完成: %s", result)
        return result

    # ------------------------------------------------------------ 监听
    def add_listener(self, callback: Callable[[dict], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict], None]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _notify(self, archive: dict) -> None:
        for callback in list(self._listeners):
            try:
                callback(archive)
            except Exception:  # noqa: BLE001
                logger.debug("成长档案监听器异常", exc_info=True)

    # ------------------------------------------------------------ 读
    def load(self) -> dict:
        return self.store.load()

    def summary(self) -> dict:
        return build_summary(self.store.load())

    def exam_countdown(self, moment: datetime | None = None) -> int | None:
        """距考试日天数；未设置/已过期 → None。"""
        learning = (self.store.load().get("profile", {}) or {}).get("learning", {}) or {}
        text = str(learning.get("exam_date") or "").strip()
        if not text:
            return None
        try:
            exam = date.fromisoformat(text[:10])
        except ValueError:
            return None
        today = (moment or datetime.now()).date()
        days = (exam - today).days
        return days if days >= 0 else None

    def injection_text(self, moment: datetime | None = None) -> str:
        """给对话 system 末尾的一小段成长背景。

        **只输出用户自己填过的结构化事实**（目标/考试日/本周专注/连续天数），
        不臆造任何内容；无内容时返回空串，调用方据此跳过注入。

        3 秒内重复调用走缓存：提示词构建可能在一轮里跑多次，档案是几 KB 的
        JSON，读频繁了不值当（也避免与后台写盘抢同一把文件锁）。
        缓存同时以文件 mtime 为准 —— 面板/另开实例直接改档案时，缓存立刻失效，
        「改完马上聊」不会读到旧背景。
        """
        try:
            stamp = self.store.path.stat().st_mtime_ns
        except OSError:
            stamp = 0
        cached = self._injection_cache
        if (cached is not None and cached[2] == stamp
                and (time.monotonic() - cached[0]) < 3.0):
            return cached[1]
        text = self._build_injection_text(moment)
        self._injection_cache = (time.monotonic(), text, stamp)
        return text

    def _build_injection_text(self, moment: datetime | None = None) -> str:
        archive = self.store.load()
        learning = (archive.get("profile", {}) or {}).get("learning", {}) or {}
        emotion = archive.get("emotion", {}) or {}
        lines: list[str] = []

        goal = str(learning.get("goal") or "").strip()
        if goal:
            lines.append(f"当前阶段目标：{goal}")
        countdown = self.exam_countdown(moment)
        if countdown is not None:
            lines.append(f"距考试还有 {countdown} 天")
        subjects = [s.get("name") for s in (learning.get("subjects") or []) if s.get("name")]
        if subjects:
            lines.append("备考科目：" + "、".join(str(x) for x in subjects[:8]))
        summary = build_summary(archive, moment)
        focus = summary["emotion"]["week_focus_minutes"]
        if focus:
            lines.append(f"本周已专注 {focus} 分钟")
        streak = int(emotion.get("streak_days") or 0)
        if streak >= 2:
            lines.append(f"已连续使用 {streak} 天")
        state = str(emotion.get("state") or DEFAULT_EMOTION_STATE)
        if state != DEFAULT_EMOTION_STATE:
            lines.append(f"当前情绪状态：{state}")
        presence = presence_mod.grade(archive, moment)
        if presence["tier"] != "unknown":
            lines.append(f"当前节奏：{presence['label']}")
        return "\n".join(lines)

    # ------------------------------------------------------------ 写
    def note_user_text(self, text: str, character_id: str = "",
                       moment: datetime | None = None) -> dict | None:
        """感知一条用户消息：话题计数 + 情绪信号 + 当日心情。

        **不保存原句**（原句归上游 ChatMemory），只把"聊了哪些科目""情绪往哪偏"
        折成计数。判不出东西时直接返回 ``None``，一次盘都不写。

        同一段文本 60 秒内只记一次：提示词构建可能因重试被调两遍，不能把一次
        发言算成两次（去重表只留最近 32 条摘要）。
        """
        info = perceive.classify(text)
        topics = info["topics"]
        state = info["emotion"]
        if not topics and not state:
            return None

        digest = hashlib.sha1(str(text or "").encode("utf-8", "ignore")).hexdigest()[:16]
        now = time.monotonic()
        self._recent_texts = {k: v for k, v in self._recent_texts.items()
                              if now - v < 60.0}
        if digest in self._recent_texts:
            return None
        if len(self._recent_texts) >= 32:
            oldest = min(self._recent_texts, key=self._recent_texts.get)
            self._recent_texts.pop(oldest, None)
        self._recent_texts[digest] = now

        stamp = now_iso(moment)
        day = (moment or datetime.now()).date().isoformat()
        energy_delta, mood_delta = perceive.EMOTION_IMPACT.get(state, (0, 0))
        if not state and topics:
            mood_delta = perceive.STUDY_MOOD_BONUS

        def _mutate(archive: dict) -> None:
            knowledge = archive.setdefault("knowledge", {})
            topic_map = knowledge.setdefault("topics", {})
            for name in topics:
                topic_map[name] = int(topic_map.get(name) or 0) + 1
            if len(topic_map) > MAX_TOPICS:
                keep = sorted(topic_map.items(), key=lambda kv: kv[1], reverse=True)[:MAX_TOPICS]
                knowledge["topics"] = dict(keep)

            emotion = archive.setdefault("emotion", {})
            if state:
                hits = emotion.setdefault("mood_hits", {})
                hits[state] = int(hits.get(state) or 0) + 1
                emotion["state"] = state
                emotion["last_state_at"] = stamp
                energy = int(emotion.get("energy") or 70) + energy_delta
                emotion["energy"] = max(0, min(100, energy))
            if mood_delta:
                rows = emotion.setdefault("daily", [])
                row = next((r for r in rows if r.get("date") == day), None)
                if row is None:
                    row = {"date": day, "focus_minutes": 0, "distractions": 0, "mood": 0}
                    rows.append(row)
                row["mood"] = max(-5, min(5, int(row.get("mood") or 0) + mood_delta))
                del rows[:-MAX_DAILY_ROWS]

        return self._safe_update(_mutate)

    def nudge_if_due(self, moment: datetime | None = None) -> dict | None:
        """该提醒就返回 ``{"kind","text"}`` 并把"提过了"落盘；不该提醒返回 None。

        阶段B3 起由 :mod:`pet.growth.proactive` 统一排程：三类开关（催事/复盘/
        关怀）逐类判定，23:30–07:30 硬静默，开关关掉的类不占当日额度。

        节流状态写在档案里而不是内存：重启后不能把"今天已经念过两次"忘掉，
        否则一天被念四遍，比不提醒更烦人。
        """
        moment = moment or datetime.now().astimezone()
        # 总闸在这里再判一次：面板把开关关掉后必须立刻闭嘴，不能等下一次
        # 外部节拍，也不能依赖调用方（app 层）记得检查。
        if not self.nudge_enabled():
            return None
        try:
            archive = self.store.load()
        except Exception:  # noqa: BLE001
            logger.warning("读取成长档案失败，跳过本次提醒判定", exc_info=True)
            return None
        switches = proactive_mod.Switches.from_config(self.config)
        plan = proactive_mod.plan(archive, switches, moment)
        if plan is None:
            return None
        if self._safe_update(lambda a: nudge_mod.mark(a, plan, moment)) is None:
            return None
        logger.info("成长提醒：%s", plan.get("kind"))
        return plan

    # ---------------------------------------------------- 主动性开关（B3）
    def proactive_switches(self) -> "proactive_mod.Switches":
        """当前三类开关快照（只读，不落盘）。"""
        return proactive_mod.Switches.from_config(self.config)

    def set_proactive_switch(self, kind: str, enabled: bool) -> bool:
        """切换某一类提醒（chore/review/care）并持久化到 config.json。"""
        return proactive_mod.set_switch(self.config, kind, enabled)

    # ---------------------------------------------------- 主动性总闸（B3）
    def nudge_enabled(self) -> bool:
        """主动提醒总闸当前值；没配置时按默认开。"""
        config = self.config
        if config is None:
            return True
        try:
            return bool(config.get("growth_nudge_enabled", True))
        except Exception:  # noqa: BLE001
            logger.warning("读取成长提醒总闸失败，按开启处理", exc_info=True)
            return True

    def set_nudge_enabled(self, enabled: bool) -> bool:
        """写总闸并落盘。

        与 :func:`proactive.set_switch` 同口径：``config`` 为空直接抛
        ``TypeError``，宁可让调用方知道没落盘，也不静默成功。
        """
        config = self.config
        if config is None:
            raise TypeError("config 不可为空：开关必须持久化")
        config.set("growth_nudge_enabled", bool(enabled))
        try:
            config.save()
        except Exception:  # noqa: BLE001 - 保存失败留给上层日志，不影响本次勾选
            logger.warning("成长提醒总闸保存失败", exc_info=True)
        return bool(enabled)

    def nudge_delay_seconds(self, moment: datetime | None = None) -> int:
        """下一次提醒检查的间隔（秒）。读档失败按默认节拍走，不抛。"""
        try:
            archive = self.store.load()
        except Exception:  # noqa: BLE001
            logger.warning("读取成长档案失败，按默认节拍排下一次提醒检查", exc_info=True)
            archive = None
        return nudge_mod.next_delay_seconds(moment, archive)

    def record_session(self, character_id: str = "") -> dict | None:
        """记一次对话/互动会话。按角色分别累计。"""
        char = str(character_id or "").strip()[:60]
        stamp = now_iso()

        def _mutate(archive: dict) -> None:
            sessions = archive.setdefault("sessions", {})
            sessions["total"] = int(sessions.get("total") or 0) + 1
            sessions["last_at"] = stamp
            if char:
                bucket = sessions.setdefault("by_character", {})
                bucket[char] = int(bucket.get(char) or 0) + 1

        return self._safe_update(_mutate)

    def set_emotion(self, state: str, *, energy: int | None = None) -> dict | None:
        """设置情绪状态（非法值回落到 calm，不抛）。"""
        clean = state if state in EMOTION_STATES else DEFAULT_EMOTION_STATE
        stamp = now_iso()

        def _mutate(archive: dict) -> None:
            emotion = archive.setdefault("emotion", {})
            emotion["state"] = clean
            emotion["last_state_at"] = stamp
            if energy is not None:
                emotion["energy"] = max(0, min(100, int(energy)))

        return self._safe_update(_mutate)

    def record_day(self, *, focus_minutes: int = 0, distractions: int = 0,
                   mood: int = 0, moment: datetime | None = None) -> dict | None:
        """累计当天的专注/分心/心情（同一天累加，跨天新增一行）。"""
        key = (moment or datetime.now()).date().isoformat()

        def _mutate(archive: dict) -> None:
            emotion = archive.setdefault("emotion", {})
            rows = emotion.setdefault("daily", [])
            row = next((r for r in rows if r.get("date") == key), None)
            if row is None:
                row = {"date": key, "focus_minutes": 0, "distractions": 0, "mood": 0}
                rows.append(row)
            row["focus_minutes"] = int(row.get("focus_minutes") or 0) + max(0, int(focus_minutes))
            row["distractions"] = int(row.get("distractions") or 0) + max(0, int(distractions))
            if mood:
                row["mood"] = max(-5, min(5, int(row.get("mood") or 0) + int(mood)))
            del rows[:-400]

        return self._safe_update(_mutate)

    # ---------------------------------------------------- 学习档案编辑（B3）
    def update_learning(self, *, goal=None, exam_date=None, weekly_minutes=None,
                        subjects=None, target_schools=None,
                        moment: datetime | None = None) -> dict | None:
        """编辑学习档案。``None`` 一律表示"这一项不动"。

        面板是唯一的写入口，所以清洗放在这里做一遍：考试日只认 ``YYYY-MM-DD``
        （填错就当没填，绝不写进档案再让倒计时算出负数），科目表交给
        ``schema._clean_subjects`` 与读档时同一套规则，避免面板写进去的东西
        下次读档被静默改掉。
        """
        stamp = now_iso(moment)

        def _mutate(archive: dict) -> None:
            profile = archive.setdefault("profile", {})
            learning = profile.setdefault("learning", {})
            if goal is not None:
                learning["goal"] = str(goal).strip()[:2000]
            if exam_date is not None:
                clean_date = _iso_date(exam_date)
                if clean_date:
                    learning["exam_date"] = clean_date
                elif not str(exam_date).strip():
                    learning["exam_date"] = ""
            if weekly_minutes is not None:
                try:
                    minutes = int(weekly_minutes)
                except (TypeError, ValueError):
                    minutes = 0
                learning["weekly_minutes"] = max(0, min(10080, minutes))
            if subjects is not None:
                learning["subjects"] = _clean_subjects(subjects)
            if target_schools is not None:
                learning["target_schools"] = [str(x).strip()[:60]
                                              for x in list(target_schools)[:10]
                                              if str(x).strip()]
            if not learning.get("started_at"):
                learning["started_at"] = stamp
            learning["updated_at"] = stamp
            profile["updated_at"] = stamp

        return self._safe_update(_mutate)

    def add_milestone(self, title: str, due: str = "",
                      moment: datetime | None = None) -> dict | None:
        """加一个里程碑（重复标题不拦——同一天可以有两个不同阶段，靠 id 区分）。"""
        clean_title = str(title or "").strip()[:120]
        if not clean_title:
            return None
        item = {"id": new_id("ms-"), "title": clean_title,
                "due": _iso_date(due), "done": False}
        stamp = now_iso(moment)

        def _mutate(archive: dict) -> None:
            learning = archive.setdefault("profile", {}).setdefault("learning", {})
            rows = learning.setdefault("milestones", [])
            rows.append(item)
            if len(rows) > MAX_MILESTONES:
                del rows[:-MAX_MILESTONES]
            learning["updated_at"] = stamp

        return self._safe_update(_mutate)

    def toggle_milestone(self, milestone_id: str,
                         moment: datetime | None = None) -> dict | None:
        """勾选/取消勾选里程碑；id 不存在时返回 ``None``（不写盘）。"""
        target = str(milestone_id or "").strip()
        if not target:
            return None
        found = {"hit": False}
        stamp = now_iso(moment)

        def _mutate(archive: dict) -> None:
            learning = archive.setdefault("profile", {}).setdefault("learning", {})
            for row in learning.get("milestones", []) or []:
                if isinstance(row, dict) and str(row.get("id") or "") == target:
                    row["done"] = not bool(row.get("done"))
                    found["hit"] = True
                    break
            if found["hit"]:
                learning["updated_at"] = stamp

        archive = self._safe_update(_mutate)
        return archive if found["hit"] else None

    def remove_milestone(self, milestone_id: str,
                         moment: datetime | None = None) -> dict | None:
        """删掉一个里程碑；id 不存在时返回 ``None``。"""
        target = str(milestone_id or "").strip()
        if not target:
            return None
        found = {"hit": False}
        stamp = now_iso(moment)

        def _mutate(archive: dict) -> None:
            learning = archive.setdefault("profile", {}).setdefault("learning", {})
            rows = learning.get("milestones", []) or []
            keep = [r for r in rows if not (isinstance(r, dict)
                                            and str(r.get("id") or "") == target)]
            if len(keep) != len(rows):
                found["hit"] = True
                learning["milestones"] = keep
                learning["updated_at"] = stamp

        archive = self._safe_update(_mutate)
        return archive if found["hit"] else None

    # ---------------------------------------------------- 错题本（B3）
    def add_review_card(self, front: str, back: str = "", tags=None,
                        moment: datetime | None = None) -> dict | None:
        """加一张错题卡。刚建时没有 ``due``——按"立刻就复习"处理。"""
        clean_front = str(front or "").strip()[:500]
        if not clean_front:
            return None
        stamp = now_iso(moment)
        card = {
            "id": new_id("rc-"),
            "front": clean_front,
            "back": str(back or "").strip()[:2000],
            "tags": [str(t).strip()[:24] for t in list(tags or [])[:8] if str(t).strip()],
            "state": 1,
            "step": 0,
            "stability": 0.0,
            "difficulty": 0.0,
            "reps": 0,
            "lapses": 0,
            "due": None,
            "last_review": None,
            "created_at": stamp,
        }

        def _mutate(archive: dict) -> None:
            reviews = archive.setdefault("reviews", {})
            cards = reviews.setdefault("cards", [])
            cards.append(card)
            if len(cards) > MAX_REVIEW_CARDS:
                del cards[:-MAX_REVIEW_CARDS]

        return self._safe_update(_mutate)

    def review_cards(self) -> list[dict]:
        """全部错题卡（只读快照，不写盘）。"""
        try:
            return list(self.store.load().get("reviews", {}).get("cards") or [])
        except Exception:  # noqa: BLE001
            logger.warning("读取错题本失败", exc_info=True)
            return []

    def due_review_cards(self, limit: int = 20,
                         moment: datetime | None = None) -> list[dict]:
        """到期该复习的卡（最该复习的在前）。"""
        return review_mod.due_rows(self.review_cards(), limit=limit, moment=moment)

    def grade_review(self, card_id: str, grade: str,
                     moment: datetime | None = None) -> dict | None:
        """评一张卡：算新排期 → 写回档案 → 累计统计。卡不存在返回 ``None``。"""
        target = str(card_id or "").strip()
        if not target:
            return None
        found = {"hit": False, "again": False}
        stamp = now_iso(moment)

        def _mutate(archive: dict) -> None:
            reviews = archive.setdefault("reviews", {})
            cards = reviews.setdefault("cards", [])
            for row in cards:
                if not isinstance(row, dict) or str(row.get("id") or "") != target:
                    continue
                updates = review_mod.schedule(row, grade, moment)
                if not updates:
                    return
                row.update(updates)
                found["hit"] = True
                found["again"] = str(grade).strip().lower() == "again"
                break
            if not found["hit"]:
                return
            stats = reviews.setdefault("stats", {"done": 0, "again": 0})
            stats["done"] = int(stats.get("done") or 0) + 1
            if found["again"]:
                stats["again"] = int(stats.get("again") or 0) + 1
            reviews["last_review_at"] = stamp

        archive = self._safe_update(_mutate)
        return archive if found["hit"] else None

    def review_stats(self, moment: datetime | None = None) -> dict:
        """面板顶部那行：待复习数、总卡数、累计完成、是否真 FSRS。"""
        cards = self.review_cards()
        try:
            total = int(self.store.load().get("reviews", {}).get("stats", {}).get("done") or 0)
        except Exception:  # noqa: BLE001
            total = 0
        return {
            "due": review_mod.due_count(cards, moment),
            "cards": len(cards),
            "done": total,
            "text": review_mod.next_due_text(cards, moment),
            "fsrs": review_mod.available(),
        }

    # ---------------------------------------------------- 知识提炼（B3）
    def harvest_knowledge(self, entries, moment: datetime | None = None) -> dict | None:
        """把记忆条目的关键词并进档案：话题计数 + 关键词摘要，**不搬原句**。

        传进来的 ``entries`` 必须是 ``ChatMemory.load()`` 的结果（已含 keywords）。
        没抽出词、或全是已记过的组合时返回 ``None``——调用方据此判断"这轮没新东西"。
        """
        rows = list(entries or [])
        if not rows:
            return None
        found = {"topics": {}, "added": 0}

        def _mutate(archive: dict) -> None:
            knowledge = archive.setdefault("knowledge", {})
            existing = knowledge.get("increments") or []
            fresh = extract_mod.distill(rows, existing, moment=moment)
            found["topics"] = extract_mod.harvest_topics(rows, knowledge.get("topics") or {})
            if not fresh and not found["topics"]:
                return
            if fresh:
                knowledge["increments"] = extract_mod.merge_increments(existing, fresh)
                found["added"] = len(fresh)
            if found["topics"]:
                knowledge["topics"] = found["topics"]
            knowledge["last_extract_at"] = now_iso(moment)

        archive = self._safe_update(_mutate)
        if archive is None or (not found["topics"] and not found["added"]):
            return None
        return archive

    def top_topics(self, limit: int = 5) -> list[tuple[str, int]]:
        """最常聊的几件事（只读，供面板/注入用）。"""
        try:
            topics = self.store.load().get("knowledge", {}).get("topics") or {}
        except Exception:  # noqa: BLE001
            logger.warning("读取话题失败", exc_info=True)
            return []
        rows = [(str(k), int(v)) for k, v in topics.items() if isinstance(v, (int, float))]
        rows.sort(key=lambda kv: kv[1], reverse=True)
        return rows[:limit]

    def _safe_update(self, mutate) -> dict | None:
        try:
            archive = self.store.update(mutate)
        except Exception:  # noqa: BLE001 - 写盘失败只记日志
            logger.warning("成长档案写入失败", exc_info=True)
            return None
        self._injection_cache = None  # 写完立刻让注入文本重算
        self._notify(archive)
        return archive

    # ------------------------------------------------------------ 诊断
    def debug_state(self) -> dict:
        """面板/日志：档案健康信息 + 摘要。"""
        return {"health": self.store.health(), "summary": self.summary()}
