# -*- coding: utf-8 -*-
"""主动提醒（``pet.growth.nudge``）与 service 接线。

全是纯逻辑断言：时间由用例传入，不 sleep、不依赖本机时钟，也不需要 Qt。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pet import quiet_mode
from pet.app import AppShell
from pet.growth import nudge
from pet.growth.service import GrowthService


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _archive(**over) -> dict:
    """一份"目标已填、今天还没学习"的档案。"""
    archive = {
        "profile": {
            "learning": {"goal": "上岸", "exam_date": "2027-12-25", "subjects": ["数学一"]},
            "life": {"sleep_at": "23:30", "wake_at": "07:30"},
        },
        "emotion": {"daily": []},
        "nudges": {},
    }
    for key, value in over.items():
        archive[key] = value
    return archive


# ------------------------------------------------------------------ 静默窗口


def test_quiet_window_same_day():
    assert nudge.in_quiet_window(_moment("2026-10-30T02:00:00+08:00"))
    assert not nudge.in_quiet_window(_moment("2026-10-30T12:00:00+08:00"))


def test_quiet_window_crosses_midnight():
    assert nudge.in_quiet_window(_moment("2026-10-30T23:45:00+08:00"))
    assert nudge.in_quiet_window(_moment("2026-10-30T23:30:00+08:00"))  # 含起点
    assert not nudge.in_quiet_window(_moment("2026-10-30T07:30:00+08:00"))  # 不含终点


def test_quiet_window_follows_profile():
    archive = _archive()
    archive["profile"]["life"] = {"sleep_at": "01:00", "wake_at": "09:00"}
    assert nudge.plan(archive, _moment("2026-10-30T02:00:00+08:00")) is None


def test_broken_hhmm_falls_back():
    assert nudge.parse_hhmm("25:99", nudge.parse_hhmm("07:30", None)) is not None
    assert nudge.in_quiet_window(_moment("2026-10-30T23:45:00+08:00"), "坏值", "") is True


# ------------------------------------------------------------------ 学习提醒


def test_study_nudge_with_exam_countdown():
    plan = nudge.plan(_archive(), _moment("2026-10-30T10:00:00+08:00"))
    assert plan["kind"] == "study"
    assert "距考试还有" in plan["text"]
    assert "分钟" in plan["text"]


def test_study_nudge_without_exam_date():
    archive = _archive()
    archive["profile"]["learning"] = {"goal": "上岸"}
    plan = nudge.plan(archive, _moment("2026-10-30T10:00:00+08:00"))
    assert plan["kind"] == "study"
    assert "距考试" not in plan["text"]


def test_no_study_nudge_when_already_studied_today():
    archive = _archive(emotion={"daily": [{"date": "2026-10-30", "focus_minutes": 25, "mood": 0}]})
    assert nudge.plan(archive, _moment("2026-10-30T10:00:00+08:00")) is None


def test_no_study_nudge_when_mood_is_low():
    archive = _archive(emotion={"daily": [{"date": "2026-10-30", "focus_minutes": 0, "mood": -2}]})
    assert nudge.plan(archive, _moment("2026-10-30T10:00:00+08:00")) is None


def test_no_study_nudge_without_any_goal():
    archive = _archive()
    archive["profile"]["learning"] = {}
    assert nudge.plan(archive, _moment("2026-10-30T10:00:00+08:00")) is None


def test_no_study_nudge_outside_window():
    assert nudge.plan(_archive(), _moment("2026-10-30T07:45:00+08:00")) is None
    assert nudge.plan(_archive(), _moment("2026-10-30T22:00:00+08:00")) is None


# ------------------------------------------------------------------ 节流


def test_daily_cap_blocks_third_nudge():
    archive = _archive(nudges={"date": "2026-10-30", "count": nudge.MAX_STUDY_NUDGES_PER_DAY,
                               "last_at": "2026-10-30T09:00:00+08:00"})
    assert nudge.plan(archive, _moment("2026-10-30T18:00:00+08:00")) is None


def test_min_gap_blocks_early_nudge():
    archive = _archive(nudges={"date": "2026-10-30", "count": 1,
                               "last_at": "2026-10-30T10:00:00+08:00"})
    moment = _moment("2026-10-30T10:00:00+08:00") + timedelta(
        minutes=nudge.MIN_STUDY_GAP_MINUTES - 1
    )
    assert nudge.plan(archive, moment) is None
    later = _moment("2026-10-30T10:00:00+08:00") + timedelta(minutes=nudge.MIN_STUDY_GAP_MINUTES)
    assert nudge.plan(archive, later)["kind"] == "study"


def test_previous_day_count_does_not_carry_over():
    archive = _archive(nudges={"date": "2026-10-29", "count": 9,
                               "last_at": "2026-10-29T20:00:00+08:00"})
    assert nudge.plan(archive, _moment("2026-10-30T10:00:00+08:00"))["kind"] == "study"


# ------------------------------------------------------------------ 周复盘


def test_weekly_nudge_on_sunday_evening():
    archive = _archive(emotion={"daily": [{"date": "2026-10-30", "focus_minutes": 150, "mood": 1}]})
    plan = nudge.plan(archive, _moment("2026-11-01T20:30:00+08:00"))  # 周日
    assert plan["kind"] == "weekly"
    assert "周复盘" in plan["text"]


def test_weekly_nudge_only_once_per_period():
    sunday = _moment("2026-11-01T20:30:00+08:00")
    archive = _archive()
    plan = nudge.plan(archive, sunday)
    assert plan["kind"] == "weekly"
    nudge.mark(archive, plan, sunday)
    assert nudge.plan(archive, sunday + timedelta(minutes=30)) is None


def test_weekly_nudge_not_on_weekday():
    # 周五 21:45：非周日（不该出周复盘），也过了学习提醒时段（不该出学习提醒）
    assert nudge.plan(_archive(), _moment("2026-10-30T21:45:00+08:00")) is None


# ------------------------------------------------------------------ mark / 节拍


def test_mark_counts_and_stamps():
    archive = _archive()
    moment = _moment("2026-10-30T10:00:00+08:00")
    plan = {"kind": "study", "text": "x"}
    nudge.mark(archive, plan, moment)
    nudge.mark(archive, plan, moment + timedelta(minutes=200))
    assert archive["nudges"]["date"] == "2026-10-30"
    assert archive["nudges"]["count"] == 2
    assert archive["nudges"]["last_at"]


def test_mark_resets_on_new_day():
    archive = _archive(nudges={"date": "2026-10-28", "count": 5, "last_at": ""})
    nudge.mark(archive, {"kind": "study"}, _moment("2026-10-30T10:00:00+08:00"))
    assert archive["nudges"]["count"] == 1


def test_next_delay_awake_is_capped():
    archive = _archive()
    delay = nudge.next_delay_seconds(_moment("2026-10-30T12:00:00+08:00"), archive)
    assert delay == nudge.MAX_CHECK_INTERVAL_SECONDS


def test_next_delay_sleeps_until_wake_up():
    archive = _archive()
    moment = _moment("2026-10-30T23:40:00+08:00")
    delay = nudge.next_delay_seconds(moment, archive)
    wake = moment.replace(hour=7, minute=30, second=0, microsecond=0) + timedelta(days=1)
    assert delay == int((wake - moment).total_seconds())


# ------------------------------------------------------------------ service 接线


class _FakeConfig:
    def __init__(self, base, **values):
        self.dir = base
        self.instance_id = ""
        self._values = {"character": "dafeiyu", **values}

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture()
def service(tmp_path):
    base = tmp_path / "cfg"
    base.mkdir()
    svc = GrowthService(_FakeConfig(base))
    svc.start()
    return svc


def test_service_nudge_delay_uses_archive_profile(service):
    awake = _moment("2026-10-30T12:00:00+08:00")
    assert service.nudge_delay_seconds(awake) == nudge.MAX_CHECK_INTERVAL_SECONDS


def test_service_nudge_if_due_is_silent_without_goal(service):
    # 新建档案还没填学习目标：这一轮不该催（问卷该干的事，不是提醒）
    assert service.nudge_if_due(_moment("2026-10-30T10:00:00+08:00")) is None



# ------------------------------------------------------------------ app 层闸门


class _Cfg:
    """只实现 ``config.get`` 的最小替身。"""

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _Host:
    """伪气泡宿主：记录每次上屏，便于断言"说了什么、给多久"。"""

    def __init__(self, visible=True):
        self.visible = visible
        self.calls = []

    def isVisible(self):
        return self.visible

    def show_bubble(self, text, duration_ms=4500, **_kwargs):
        self.calls.append((text, duration_ms))
        return True


@pytest.fixture(autouse=True)
def _clean_quiet_state():
    quiet_mode.reset_shared_quiet_state()
    yield
    quiet_mode.reset_shared_quiet_state()


def _fake_app(*, host=None, **config):
    """绕过 ``AppShell.__init__``（那里要起整个 GUI），只装出方法要用到的属性。

    这样 ``_growth_nudge_gate_open`` 之类的实例方法能正常绑定到 self 上，
    比 ``SimpleNamespace`` 更贴近真实调用路径。
    """
    host = host if host is not None else _Host()
    app = AppShell.__new__(AppShell)
    app.config = _Cfg(**config)
    app._overlay_shell = host
    app.instance = None
    app.win = None
    return app, host


def test_gate_needs_a_visible_host():
    app, _ = _fake_app(host=_Host(visible=False))
    assert app._growth_bubble_host() is None
    assert app._growth_nudge_gate_open() is False


def test_gate_falls_back_to_instance_window():
    main_win = _Host()
    app = AppShell.__new__(AppShell)
    app.config = _Cfg()
    app._overlay_shell = None
    app.win = None  # 先设 win：AppShell 的 win 是转发属性，写它会改写 instance.win
    app.instance = SimpleNamespace(win=main_win)
    assert app._growth_bubble_host() is main_win


def test_gate_blocked_by_manual_quiet():
    app, _ = _fake_app()
    quiet_mode.shared_quiet_state().start(30)
    assert app._growth_nudge_gate_open() is False


def test_gate_blocked_by_fullscreen():
    app, _ = _fake_app()  # quiet_also_on_fullscreen 默认开
    quiet_mode.shared_quiet_state().set_fullscreen(True)
    assert app._growth_nudge_gate_open() is False


def test_gate_ignores_fullscreen_when_preference_off():
    app, _ = _fake_app(quiet_also_on_fullscreen=False)
    quiet_mode.shared_quiet_state().set_fullscreen(True)
    assert app._growth_nudge_gate_open() is True


def test_gate_respects_feature_switch():
    app, _ = _fake_app(growth_nudge_enabled=False)
    assert app._growth_nudge_gate_open() is False


def test_maybe_nudge_shows_bubble():
    app, host = _fake_app()
    calls = []

    class _Svc:
        def nudge_if_due(self):
            calls.append(1)
            return {"kind": "study", "text": "今天还没记录学习"}

    app.growth_service = _Svc()
    app._maybe_growth_nudge()
    assert calls == [1]
    assert host.calls and host.calls[0][0] == "今天还没记录学习"


def test_maybe_nudge_skips_when_gate_closed():
    app, host = _fake_app(host=_Host(visible=False))

    class _Svc:
        def nudge_if_due(self):  # pragma: no cover - 走到这里就是失败
            raise AssertionError("闸门关着时不该做提醒判定")

    app.growth_service = _Svc()
    app._maybe_growth_nudge()
    assert host.calls == []


def test_maybe_nudge_ignores_empty_text():
    app, host = _fake_app()

    class _Svc:
        def nudge_if_due(self):
            return {"kind": "study", "text": "   "}

    app.growth_service = _Svc()
    app._maybe_growth_nudge()
    assert host.calls == []


# ------------------------------------------------------------------ 周复盘扩展


def test_weekly_review_carries_cards_and_tip():
    sunday = _moment("2026-11-01T21:00:00+08:00")  # 周日 21:00，落在复盘窗口
    archive = _archive(
        emotion={"daily": [{"date": "2026-11-01", "focus_minutes": 120}]},
        reviews={"cards": [{"due": "2020-01-05T09:00:00+08:00"}]},
        knowledge={"topics": {"408": 3, "数学一": 1}},
        profile={"learning": {"weekly_minutes": 600}},
    )
    plan = nudge.plan(archive, sunday)
    assert plan is not None and plan["kind"] == "weekly"
    text = plan["text"]
    assert "1 张错题卡" in text and "1 张已到期" in text
    assert "2 个话题" in text
    assert "错题" in text          # 有欠账时建议先清错题
    assert "120 分钟" in text


def test_weekly_review_without_cards_suggests_target():
    sunday = _moment("2026-11-01T21:00:00+08:00")
    archive = _archive(
        emotion={"daily": [{"date": "2026-11-01", "focus_minutes": 30}]},
        profile={"learning": {"weekly_minutes": 600}},
    )
    text = nudge.plan(archive, sunday)["text"]
    assert "错题" not in text
    assert "每周目标" in text


def test_weekly_review_survives_junk_rows():
    sunday = _moment("2026-11-01T21:00:00+08:00")
    archive = _archive(
        emotion={"daily": [{"date": "坏的日期", "focus_minutes": "很多"}, "不是字典"]},
        reviews={"cards": [{"due": "没有时间"}, "坏卡"]},
        knowledge={"topics": {}},
    )
    plan = nudge.plan(archive, sunday)
    assert plan is not None and plan["kind"] == "weekly"
    assert "这周还没记录专注时间" in plan["text"]
