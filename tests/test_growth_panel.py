# -*- coding: utf-8 -*-
"""成长面板：能不能建起来、数字对不对、点按钮会不会把档案写坏。

Qt 一律跑 offscreen，测的是"逻辑挂没挂对"，不测像素。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pet.growth.panel import GrowthPanel
from pet.growth.service import GrowthService


class _Cfg:
    instance_id = ""

    def __init__(self, root):
        self.dir = root

    def get(self, key, default=None):
        return default


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def service(tmp_path):
    svc = GrowthService(_Cfg(tmp_path))
    svc.start()
    return svc


def test_panel_builds_without_service(qapp):
    panel = GrowthPanel(None)
    assert panel.countdown_label.text().startswith("还没设置考试日")
    assert panel.milestone_layout.count() == 1   # 只有一行空态提示
    panel.close()


def test_panel_reflects_archive(qapp, service):
    service.update_learning(goal="2028 上岸", exam_date="2027-12-25", weekly_minutes=600)
    service.record_day(focus_minutes=45, distractions=2, mood=1)
    service.add_milestone("408 第一轮", "2026-12-01")

    panel = GrowthPanel(service)
    assert "考试日 2027-12-25" in panel.countdown_label.text()
    assert "还有" in panel.countdown_label.text()
    assert panel.focus_value.text() == "45"
    assert panel.distract_value.text() == "2"
    assert panel.mood_value.text() == "+1"
    assert "600" in panel.week_label.text()
    assert panel.goal_edit.text() == "2028 上岸"
    assert panel.week_target_spin.value() == 600
    assert panel.milestone_layout.count() == 1
    panel.close()


def test_panel_add_milestone_button_writes(service, qapp):
    panel = GrowthPanel(service)
    panel.milestone_edit.setText("英语一阅读过完")
    panel._on_add_milestone()
    learning = service.store.load()["profile"]["learning"]
    assert learning["milestones"][-1]["title"] == "英语一阅读过完"
    assert panel.milestone_layout.count() == 1
    assert panel.milestone_edit.text() == ""
    panel.close()


def test_panel_add_subject_button_writes(service, qapp):
    panel = GrowthPanel(service)
    panel.subject_name_edit.setText("数学一")
    panel.subject_minutes_spin.setValue(300)
    panel.subject_weight_spin.setValue(50)
    panel._on_add_subject()
    subjects = service.store.load()["profile"]["learning"]["subjects"]
    assert subjects[0]["name"] == "数学一"
    assert subjects[0]["weight"] == 50
    panel.close()


def test_panel_save_goal_writes(service, qapp):
    panel = GrowthPanel(service)
    panel.goal_edit.setText("政治开始")
    panel.week_target_spin.setValue(480)
    panel._on_save_goal()
    learning = service.store.load()["profile"]["learning"]
    assert learning["goal"] == "政治开始"
    assert learning["weekly_minutes"] == 480
    panel.close()


def test_panel_readonly_mode_does_not_crash(qapp, tmp_path):
    panel = GrowthPanel(None)
    panel.milestone_edit.setText("随便写")
    panel._on_add_milestone()
    panel._on_save_goal()
    assert panel.status_label.text() == "预览模式，未接档案" or panel.status_label.text() == ""
    panel.close()


def test_panel_shows_review_cards_and_grade_writes(service, qapp):
    service.add_review_card("408：LRU 缺页次数", "看访问串")
    panel = GrowthPanel(service)
    assert panel.review_layout.count() == 1
    assert "1 张待复习" in panel.review_recap.text()
    card_id = service.review_cards()[0]["id"]
    panel._on_grade_review(card_id, "good")
    card = service.review_cards()[0]
    assert card["reps"] == 1
    assert panel.review_layout.count() == 1   # 排到未来后不再列在到期里，只剩空态提示
    assert "现在没有到期卡片" in panel.review_layout.itemAt(0).widget().text()
    panel.close()


def test_panel_add_review_button_writes(service, qapp):
    panel = GrowthPanel(service)
    panel.review_front_edit.setText("TCP 为什么三次握手")
    panel.review_back_edit.setText("防止旧连接")
    panel._on_add_review()
    cards = service.review_cards()
    assert cards[0]["front"] == "TCP 为什么三次握手"
    assert cards[0]["back"] == "防止旧连接"
    assert panel.review_front_edit.text() == ""
    panel.close()


def test_panel_shows_topics(service, qapp):
    service.harvest_knowledge([{"keywords": ["408", "lru"]}])
    panel = GrowthPanel(service)
    assert "408" in panel.topics_label.text()
    panel.close()


def test_panel_toggle_and_remove(service, qapp):
    archive = service.add_milestone("勾我", "")
    mid = archive["profile"]["learning"]["milestones"][-1]["id"]
    panel = GrowthPanel(service)
    panel._on_toggle_milestone(mid)
    assert service.store.load()["profile"]["learning"]["milestones"][-1]["done"] is True
    panel._on_remove_milestone(mid)
    assert service.store.load()["profile"]["learning"]["milestones"] == []
    panel.close()


# ------------------------------------------------------- 主动提醒开关（B3）
class _WriteCfg(_Cfg):
    """带 set/save 的假 config：只为验证"勾一下有没有真落库"。"""

    def __init__(self, root):
        super().__init__(root)
        self.data = {}
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


def test_panel_nudge_switches_default(tmp_path, qapp):
    service = GrowthService(_WriteCfg(tmp_path))
    service.start()
    panel = GrowthPanel(service)
    assert panel.nudge_master_check.isChecked() is True
    assert panel.nudge_kind_checks["chore"].isChecked() is True
    assert panel.nudge_kind_checks["review"].isChecked() is True
    assert panel.nudge_kind_checks["care"].isChecked() is False
    assert panel.nudge_kind_checks["chore"].isEnabled() is True
    panel.close()


def test_panel_nudge_switch_persists(tmp_path, qapp):
    cfg = _WriteCfg(tmp_path)
    service = GrowthService(cfg)
    service.start()
    panel = GrowthPanel(service)

    panel.nudge_kind_checks["care"].setChecked(True)
    assert cfg.data["growth_proactive_care_enabled"] is True
    assert cfg.saved >= 1

    panel.nudge_master_check.setChecked(False)
    assert cfg.data["growth_nudge_enabled"] is False
    # 总闸关了，三类一起置灰
    assert panel.nudge_kind_checks["chore"].isEnabled() is False
    panel.close()


def test_panel_nudge_readonly_when_no_service(qapp):
    """只读预览（没有 service）时不许点：点了也写不进去，就是骗人。"""
    panel = GrowthPanel(None)
    assert panel.nudge_master_check.isEnabled() is False
    assert all(not c.isEnabled() for c in panel.nudge_kind_checks.values())
    panel.close()
