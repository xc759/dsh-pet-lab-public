# -*- coding: utf-8 -*-
"""错题本排期（``petwisp.growth.review`` + ``GrowthService`` 的错题接口）。

口径：

* 评分只有四档；非法档位不写盘（返回 ``None``），不猜用户意思。
* 间隔必须**单调**：连着评 good，下一次到期只能更远，绝不会越来越近。
* ``again`` 要记账（lapses +1）并立刻拉回短期，这正是错题本的意义。
* 没装 ``fsrs`` 时降级为固定阶梯，功能不消失——测试要覆盖这条降级路径。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from petwisp.growth import review
from petwisp.growth.service import GrowthService


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


def _due_gap_days(updates: dict, moment: datetime) -> float:
    due = datetime.fromisoformat(updates["due"])
    return (due - moment).total_seconds() / 86400


# ---------------------------------------------------------------- 纯函数层
def test_fsrs_is_available_in_this_environment():
    """装了 fsrs 就该用真的排期；这条失败说明依赖没进环境（别静默降级）。"""
    assert review.available() is True


def test_new_card_graded_good_lands_in_the_future():
    now = datetime.now().astimezone()
    updates = review.schedule({"front": "408 里 LRU 怎么实现"}, "good", now)
    assert updates["reps"] == 1
    assert updates["lapses"] == 0
    assert updates["last_review"]
    assert _due_gap_days(updates, now) > 0


def test_intervals_grow_when_you_keep_saying_good():
    now = datetime.now().astimezone()
    row = {"front": "KMP 的 next 数组"}
    first = review.schedule(row, "good", now)
    row.update(first)
    later = now + timedelta(days=max(1, int(_due_gap_days(first, now))))
    second = review.schedule(row, "good", later)
    assert _due_gap_days(second, later) > _due_gap_days(first, now)


def test_again_counts_a_lapse_and_comes_back_sooner():
    now = datetime.now().astimezone()
    row = {"front": "进程与线程区别"}
    row.update(review.schedule(row, "good", now))
    good_gap = _due_gap_days(review.schedule(row, "good", now), now)
    graded_again = review.schedule(row, "again", now)
    assert graded_again["lapses"] == 1
    assert _due_gap_days(graded_again, now) < good_gap


@pytest.mark.parametrize("grade", ["", "perfect", "5", None])
def test_unknown_grade_is_refused(grade):
    assert review.schedule({"front": "x"}, grade) is None


def test_card_without_due_counts_as_due():
    cards = [
        {"front": "没评过", "due": None},
        {"front": "明天", "due": (datetime.now().astimezone() + timedelta(days=1)).isoformat()},
    ]
    due = review.due_rows(cards)
    assert [c["front"] for c in due] == ["没评过"]


def test_due_rows_sorted_by_due_time_and_limited():
    now = datetime.now().astimezone()
    cards = [
        {"front": "晚", "due": (now - timedelta(hours=1)).isoformat()},
        {"front": "早", "due": (now - timedelta(days=2)).isoformat()},
        {"front": "将来", "due": (now + timedelta(days=3)).isoformat()},
    ]
    assert [c["front"] for c in review.due_rows(cards)] == ["早", "晚"]
    assert len(review.due_rows(cards, limit=1)) == 1


def test_next_due_text_reads_naturally():
    assert review.next_due_text([]) == "还没有错题"
    now = datetime.now().astimezone()
    cards = [{"front": "x", "due": None}]
    assert "1 张待复习" in review.next_due_text(cards, now)


def test_fallback_works_when_fsrs_is_missing(monkeypatch):
    """模拟打包变体里没有 fsrs：排期降级，但间隔与到期日仍然合理。"""
    monkeypatch.setattr(review, "_scheduler", None)
    monkeypatch.setattr(review, "_scheduler_failed", True)
    now = datetime.now().astimezone()
    assert review.available() is False
    updates = review.schedule({"front": "降级"}, "good", now)
    assert updates is not None
    # 首级阶梯是 0 天，必须落到"10 分钟后"，否则卡片永远显示到期
    assert _due_gap_days(updates, now) > 0
    assert updates["reps"] == 1


# ---------------------------------------------------------------- 服务层
def test_add_card_then_it_is_immediately_due(service):
    assert service.add_review_card("虚拟内存的作用", "让大程序跑在小内存上") is not None
    cards = service.review_cards()
    assert len(cards) == 1
    assert cards[0]["front"] == "虚拟内存的作用"
    assert service.review_stats()["due"] == 1


def test_blank_front_is_refused(service):
    assert service.add_review_card("   ") is None
    assert service.review_cards() == []


def test_grade_review_persists_state_and_stats(service):
    service.add_review_card("TCP 三次握手")
    card_id = service.review_cards()[0]["id"]
    assert service.grade_review(card_id, "good") is not None
    card = service.review_cards()[0]
    assert card["reps"] == 1
    assert card["due"] is not None
    stats = service.review_stats()
    assert stats["done"] == 1
    assert stats["due"] == 0
    assert stats["cards"] == 1


def test_grade_unknown_card_returns_none(service):
    assert service.grade_review("rc-不存在", "good") is None


def test_again_is_counted_in_archive_stats(service):
    service.add_review_card("红黑树的五种性质")
    card_id = service.review_cards()[0]["id"]
    service.grade_review(card_id, "again")
    reviews = service.store.load()["reviews"]
    assert reviews["stats"]["again"] == 1
    assert reviews["cards"][0]["lapses"] == 1


def test_review_state_survives_a_reload(service):
    """排期写盘后重新读档，state/step 不能被归一清洗掉（否则每次都从头学）。"""
    service.add_review_card("PV 原语")
    card_id = service.review_cards()[0]["id"]
    service.grade_review(card_id, "easy")
    reloaded = GrowthService(_Cfg(service.store.config_dir)).store.load()
    card = reloaded["reviews"]["cards"][0]
    assert card["state"] in (1, 2, 3)
    assert card["state"] == 2 or card["step"] is not None
