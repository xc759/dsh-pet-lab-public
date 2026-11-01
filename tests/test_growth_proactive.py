# -*- coding: utf-8 -*-
"""主动性调度（``petwisp.growth.proactive``）：三类开关 + 硬静默。

覆盖阶段B3 的验收口径：

* 三类开关各自开合生效（关掉的类不占当日额度、不吞掉其它类）；
* 23:30–07:30 静默对三类一体生效，任何开关都翻不过去；
* 开关落 config.json 后重读仍在（独立持久化）。
"""
from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petwisp.config import Config
from petwisp.growth import nudge, proactive
from petwisp.growth import service as service_mod
from petwisp.growth.service import GrowthService
from petwisp.growth.store import GrowthStore


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _archive(mood: int | None = None, focus: int | None = None) -> dict:
    """目标已填、今天还没学习的档案；可指定当天心情/专注分钟。"""
    row = {"date": "2026-10-30"}
    if mood is not None:
        row["mood"] = mood
    if focus is not None:
        row["focus_minutes"] = focus
    return {
        "profile": {
            "learning": {"goal": "上岸", "exam_date": "2027-12-25", "subjects": ["数学一"]},
            "life": {"sleep_at": "23:30", "wake_at": "07:30"},
        },
        "emotion": {"daily": [row]},
        "nudges": {},
    }


ALL_ON = proactive.Switches(chore=True, review=True, care=True)
ALL_OFF = proactive.Switches(chore=False, review=False, care=False)


# ------------------------------------------------------------------ 默认与开关
def test_default_switches_match_stage_b_decision():
    switches = proactive.Switches()
    assert switches.chore is True
    assert switches.review is True
    assert switches.care is False


def test_switches_read_from_config(tmp_path):
    config = Config(base=tmp_path)
    assert proactive.Switches.from_config(config) == proactive.Switches()

    config.set(proactive.KEY_CHORE, False)
    config.set(proactive.KEY_CARE, True)
    switches = proactive.Switches.from_config(config)
    assert switches.chore is False
    assert switches.review is True
    assert switches.care is True


def test_switches_tolerate_bad_values():
    """手改配置成字符串/垃圾值不该让整条提醒链路炸掉。"""
    data = {
        proactive.KEY_CHORE: "false",
        proactive.KEY_REVIEW: "垃圾",
        proactive.KEY_CARE: 1,
    }
    config = SimpleNamespace(get=lambda key, default=None: data.get(key, default))
    switches = proactive.Switches.from_config(config)
    assert switches.chore is False  # "false" 不能被 bool() 误判成 True
    assert switches.review is True  # 垃圾值回默认（复盘默认开）
    assert switches.care is True


def test_switch_persisted_to_config_file(tmp_path):
    config = Config(base=tmp_path)
    assert proactive.set_switch(config, proactive.KIND_CARE, True) is True

    reloaded = Config(base=tmp_path)
    assert reloaded.get(proactive.KEY_CARE) is True


def test_set_switch_rejects_unknown_kind(tmp_path):
    config = Config(base=tmp_path)
    with pytest.raises(KeyError):
        proactive.set_switch(config, "unknown", True)
    with pytest.raises(TypeError):
        proactive.set_switch(None, proactive.KIND_CHORE, True)


# ------------------------------------------------------------- 催事（chore）
def test_chore_off_silences_study_nudge():
    plan = proactive.plan(
        _archive(), proactive.Switches(chore=False, review=True, care=True),
        _moment("2026-10-30T10:00:00+08:00"),
    )
    assert plan is None


def test_chore_on_speaks_when_no_study_recorded():
    plan = proactive.plan(_archive(), ALL_ON, _moment("2026-10-30T10:00:00+08:00"))
    assert plan is not None
    assert plan["kind"] == "study"


def test_chore_silent_when_study_recorded():
    plan = proactive.plan(
        _archive(focus=25), ALL_ON, _moment("2026-10-30T10:00:00+08:00")
    )
    assert plan is None


# ------------------------------------------------------------- 复盘（review）
def test_review_on_triggers_on_sunday_evening():
    plan = proactive.plan(_archive(), ALL_ON, _moment("2026-11-01T21:00:00+08:00"))
    assert plan is not None
    assert plan["kind"] == "weekly"


def test_review_off_does_not_swallow_chore():
    """周日晚关掉复盘：催事仍要能开口（这正是"事后过滤"会踩的坑）。"""
    plan = proactive.plan(
        _archive(), proactive.Switches(chore=True, review=False, care=False),
        _moment("2026-11-01T21:00:00+08:00"),
    )
    assert plan is not None
    assert plan["kind"] == "study"


def test_both_off_stays_silent_on_sunday():
    plan = proactive.plan(_archive(), ALL_OFF, _moment("2026-11-01T21:00:00+08:00"))
    assert plan is None


# -------------------------------------------------------------- 关怀（care）
def test_care_default_off_silences_low_mood():
    switches = proactive.Switches()  # 关怀默认关
    assert switches.care is False
    plan = proactive.plan(_archive(mood=-3), switches, _moment("2026-10-30T15:00:00+08:00"))
    assert plan is None


def test_care_on_speaks_when_mood_is_low():
    plan = proactive.plan(
        _archive(mood=-3),
        proactive.Switches(chore=True, review=True, care=True),
        _moment("2026-10-30T15:00:00+08:00"),
    )
    assert plan is not None
    assert plan["kind"] == "care"
    assert "照顾好自己" in plan["text"]


def test_care_silent_when_mood_is_fine():
    plan = proactive.plan(
        _archive(mood=1), proactive.Switches(chore=False, review=False, care=True),
        _moment("2026-10-30T15:00:00+08:00"),
    )
    assert plan is None


def test_care_respects_its_own_window():
    switches = proactive.Switches(chore=False, review=False, care=True)
    assert proactive.plan(_archive(mood=-3), switches, _moment("2026-10-30T22:30:00+08:00")) is None
    assert proactive.plan(_archive(mood=-3), switches, _moment("2026-10-30T08:00:00+08:00")) is None
    assert proactive.plan(_archive(mood=-3), switches, _moment("2026-10-30T09:00:00+08:00")) is not None


def test_care_limited_to_once_per_day():
    archive = _archive(mood=-3)
    archive["nudges"] = {"date": "2026-10-30", "count": 1, "last_at": "2026-10-30T10:00:00+08:00"}
    switches = proactive.Switches(chore=False, review=False, care=True)
    assert proactive.plan(archive, switches, _moment("2026-10-30T15:00:00+08:00")) is None


def test_daily_cap_applies_to_all_kinds():
    archive = _archive(mood=-3)
    archive["nudges"] = {"date": "2026-10-30", "count": 2, "last_at": "2026-10-30T09:00:00+08:00"}
    assert proactive.plan(archive, ALL_ON, _moment("2026-10-30T15:00:00+08:00")) is None


# ------------------------------------------------------------------ 硬静默
@pytest.mark.parametrize(
    "moment_text",
    [
        "2026-10-30T23:30:00+08:00",  # 含起点
        "2026-10-30T23:45:00+08:00",
        "2026-10-30T02:00:00+08:00",
        "2026-10-30T07:29:00+08:00",
    ],
)
def test_quiet_window_blocks_all_kinds_even_if_all_on(moment_text):
    assert proactive.plan(_archive(mood=-3), ALL_ON, _moment(moment_text)) is None


def test_quiet_window_end_allows_speaking():
    # 07:30 已出静默窗口；学习提醒有自己的 09:00 起时段，所以取 09:00 应能开口。
    assert proactive.plan(_archive(), ALL_ON, _moment("2026-10-30T07:30:00+08:00")) is None
    plan = proactive.plan(_archive(), ALL_ON, _moment("2026-10-30T09:00:00+08:00"))
    assert plan is not None


def test_quiet_window_follows_profile_override():
    archive = _archive(mood=-3)
    archive["profile"]["life"] = {"sleep_at": "01:00", "wake_at": "09:00"}
    assert proactive.plan(archive, ALL_ON, _moment("2026-10-30T02:00:00+08:00")) is None


# --------------------------------------------------------- service 层接线
def _service(tmp_path):
    config = Config(base=tmp_path)
    service = GrowthService(config)
    service.start()
    return service, config


def test_service_uses_switches(tmp_path):
    service, config = _service(tmp_path)
    moment = _moment("2026-10-30T10:00:00+08:00")

    config.set(proactive.KEY_CHORE, False)
    assert service.nudge_if_due(moment) is None

    config.set(proactive.KEY_CHORE, True)
    assert service.proactive_switches().chore is True


def test_service_toggle_persists(tmp_path):
    service, config = _service(tmp_path)
    service.set_proactive_switch(proactive.KIND_REVIEW, False)
    assert Config(base=tmp_path).get(proactive.KEY_REVIEW) is False
    assert service.proactive_switches().review is False


# --------------------------------------------------------- 总闸（B3 收尾）
def test_service_nudge_master_defaults_on(tmp_path):
    service, _config = _service(tmp_path)
    assert service.nudge_enabled() is True


def test_service_nudge_master_persists(tmp_path):
    service, _config = _service(tmp_path)
    service.set_nudge_enabled(False)
    assert Config(base=tmp_path).get("growth_nudge_enabled") is False
    assert service.nudge_enabled() is False


def test_service_nudge_master_off_silences_every_kind(tmp_path, monkeypatch):
    """总闸关了就不许开工：连 plan 都不该被算一次。

    用假的 plan 计数，避免依赖真实节流与档案内容——真实档案在 10:00 本来
    就可能不触发，靠"返回 None"分不清是总闸拦的还是别的规则拦的。
    """
    service, _config = _service(tmp_path)
    calls = []

    def fake_plan(archive, switches, moment):
        calls.append(moment)
        return {"kind": proactive.KIND_CHORE, "text": "该记一笔了"}

    monkeypatch.setattr(service_mod.proactive_mod, "plan", fake_plan)
    moment = _moment("2026-10-30T10:00:00+08:00")

    service.set_nudge_enabled(False)
    assert service.nudge_if_due(moment) is None
    assert calls == []

    service.set_nudge_enabled(True)
    assert service.nudge_if_due(moment) is not None
    assert len(calls) == 1


def test_service_nudge_master_requires_config(tmp_path):
    service = GrowthService(None, store=GrowthStore(tmp_path))
    with pytest.raises(TypeError):
        service.set_nudge_enabled(False)
    # 没有 config 时按"开着"处理，不能因为读不到就静默关掉用户的提醒
    assert service.nudge_enabled() is True
