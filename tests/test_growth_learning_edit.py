# -*- coding: utf-8 -*-
"""学习档案编辑（``GrowthService.update_learning`` / 里程碑增删勾）。

面板是唯一的人肉写入口，所以这里的口径是：

* ``None`` = 这一项不动（面板不做"全量覆盖"，否则改目标会把科目表擦掉）；
* 脏值不落盘（考试日填错就当没填），但不能把已有的好值也冲掉；
* 操作不存在的 id 返回 ``None``，面板据此判断"没成功"。
"""
from __future__ import annotations

import json

import pytest

from pet.growth.service import GrowthService


class _Cfg:
    instance_id = ""

    def __init__(self, root):
        self.dir = root

    def get(self, key, default=None):
        return default


@pytest.fixture()
def service(tmp_path):
    svc = GrowthService(_Cfg(tmp_path))
    svc.start()
    return svc


def _learning(service) -> dict:
    return service.store.load()["profile"]["learning"]


# ---------------------------------------------------------------- update_learning
def test_update_learning_writes_all_fields(service):
    service.update_learning(
        goal="2028 上岸",
        exam_date="2027-12-25",
        weekly_minutes=900,
        subjects=[{"name": "数学一", "weekly_minutes": 300, "weight": 40}],
        target_schools=["上海交通大学", "深圳大学"],
    )
    learning = _learning(service)
    assert learning["goal"] == "2028 上岸"
    assert learning["exam_date"] == "2027-12-25"
    assert learning["weekly_minutes"] == 900
    assert learning["subjects"][0]["name"] == "数学一"
    assert learning["target_schools"] == ["上海交通大学", "深圳大学"]
    assert learning["started_at"]
    assert learning["updated_at"]


def test_update_learning_none_keeps_previous_value(service):
    service.update_learning(goal="旧目标", weekly_minutes=600)
    service.update_learning(weekly_minutes=300)
    learning = _learning(service)
    assert learning["goal"] == "旧目标"
    assert learning["weekly_minutes"] == 300


def test_update_learning_rejects_bad_date_without_erasing(service):
    service.update_learning(exam_date="2027-12-25")
    service.update_learning(exam_date="2027-02-30")   # 不存在的日期
    assert _learning(service)["exam_date"] == "2027-12-25"


def test_update_learning_empty_date_clears(service):
    service.update_learning(exam_date="2027-12-25")
    service.update_learning(exam_date="")
    assert _learning(service)["exam_date"] == ""


def test_update_learning_clamps_weekly_minutes(service):
    service.update_learning(weekly_minutes=10 ** 6)
    assert _learning(service)["weekly_minutes"] == 10080
    service.update_learning(weekly_minutes=-5)
    assert _learning(service)["weekly_minutes"] == 0


def test_update_learning_drops_blank_subjects(service):
    service.update_learning(subjects=[{"name": "  "}, {"name": "408", "weight": 60}])
    subjects = _learning(service)["subjects"]
    assert [s["name"] for s in subjects] == ["408"]
    assert subjects[0]["weight"] == 60


def test_update_learning_bumps_injection_cache(service):
    first = service.injection_text()
    service.update_learning(goal="新的阶段目标：408 第一轮")
    assert service.injection_text() != first


# ---------------------------------------------------------------- 里程碑
def test_add_milestone_persists(service):
    archive = service.add_milestone("408 第一轮过完", "2026-12-01")
    assert archive is not None
    rows = _learning(service)["milestones"]
    assert rows[-1]["title"] == "408 第一轮过完"
    assert rows[-1]["due"] == "2026-12-01"
    assert rows[-1]["done"] is False
    assert rows[-1]["id"]


def test_add_milestone_ignores_blank_title(service):
    assert service.add_milestone("   ") is None
    assert _learning(service)["milestones"] == []


def test_add_milestone_bad_due_falls_back_to_empty(service):
    service.add_milestone("第一轮", "下周吧")
    assert _learning(service)["milestones"][-1]["due"] == ""


def test_toggle_milestone_flips_done(service):
    archive = service.add_milestone("数学线代", "")
    mid = archive["profile"]["learning"]["milestones"][-1]["id"]
    service.toggle_milestone(mid)
    assert _learning(service)["milestones"][-1]["done"] is True
    service.toggle_milestone(mid)
    assert _learning(service)["milestones"][-1]["done"] is False


def test_toggle_unknown_milestone_returns_none(service):
    assert service.toggle_milestone("ms-not-exist") is None


def test_remove_milestone(service):
    archive = service.add_milestone("要被删掉", "")
    mid = archive["profile"]["learning"]["milestones"][-1]["id"]
    assert service.remove_milestone(mid) is not None
    assert _learning(service)["milestones"] == []
    assert service.remove_milestone(mid) is None


def test_milestone_archived_as_readable_json(service):
    service.add_milestone("第一轮", "2026-12-01")
    path = service.store.path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["profile"]["learning"]["milestones"][0]["title"] == "第一轮"
