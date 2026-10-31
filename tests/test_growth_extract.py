# -*- coding: utf-8 -*-
"""知识提炼（``pet.growth.extract``）与 ``GrowthService.harvest_knowledge``。

红线优先：**档案里不能出现聊天原句**。所以这里断言的是"落盘内容只由关键词组成"，
而不是"提炼得够不够多"。任何一条测试如果为了让用例通过而去读 ``text``，
就说明实现越界了。
"""
from __future__ import annotations

import json

import pytest

from pet.growth import extract
from pet.growth.schema import MAX_TOPICS
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


def _entry(keywords, text="不该被读到的整句原文", **extra):
    row = {"keywords": keywords, "text": text}
    row.update(extra)
    return row


# ---------------------------------------------------------------- 纯函数层
def test_entry_keywords_normalizes_and_dedupes():
    words = extract.entry_keywords(_entry(["408", " OS ", "os", "LRU", "LRU"]))
    assert words == ["408", "os", "lru"]


def test_entry_keywords_caps_per_entry():
    words = extract.entry_keywords(_entry([f"w{i}" for i in range(10)]))
    assert len(words) == extract.MAX_WORDS_PER_ENTRY


def test_entry_keywords_skips_archived_and_missing():
    assert extract.entry_keywords(_entry(["408"], archived=True)) == []
    assert extract.entry_keywords({"text": "没有关键词字段"}) == []
    assert extract.entry_keywords("不是字典") == []


def test_stopwords_are_dropped():
    assert extract.entry_keywords(_entry(["学习", "怎么", "408"])) == ["408"]


def test_harvest_topics_counts_and_accumulates():
    entries = [_entry(["408", "os"]), _entry(["408"])]
    counts = extract.harvest_topics(entries, {"408": 5})
    assert counts["408"] == 7
    assert counts["os"] == 1


def test_harvest_topics_keeps_ranking_within_limit():
    entries = [_entry([f"topic{i}"]) for i in range(MAX_TOPICS + 10)]
    counts = extract.harvest_topics(entries, {})
    assert len(counts) == MAX_TOPICS


def test_distill_never_copies_the_sentence():
    row = extract.distill([_entry(["408", "lru"])])[0]
    assert "原文" not in row["text"]
    assert set(row["tags"]) == {"408", "lru"}


def test_distill_skips_known_combinations():
    entries = [_entry(["408", "lru"])]
    first = extract.distill(entries)
    assert first
    assert extract.distill(entries, first) == []


def test_distill_respects_max_new():
    entries = [_entry([f"w{i}"]) for i in range(30)]
    assert len(extract.distill(entries, max_new=5)) == 5


# ---------------------------------------------------------------- 服务层
def test_harvest_writes_topics_and_increments(service):
    assert service.harvest_knowledge([_entry(["408", "lru"])]) is not None
    archive = service.store.load()
    assert archive["knowledge"]["topics"]["408"] == 1
    assert archive["knowledge"]["increments"][0]["tags"] == ["408", "lru"]
    assert archive["knowledge"]["last_extract_at"]


def test_harvest_leaves_no_raw_sentence_on_disk(service):
    service.harvest_knowledge([_entry(["tcp"], text="整句：讲讲 TCP 三次握手为什么要三次")])
    raw = service.store.path.read_text(encoding="utf-8")
    assert "整句" not in raw
    assert "三次握手为什么要三次" not in raw


def test_harvest_is_idempotent(service):
    entries = [_entry(["408", "lru"])]
    service.harvest_knowledge(entries)
    first = json.dumps(service.store.load()["knowledge"], ensure_ascii=False, sort_keys=True)
    service.harvest_knowledge(entries)
    second = json.dumps(service.store.load()["knowledge"], ensure_ascii=False, sort_keys=True)
    # 同样的输入再跑一遍：不新增摘要（话题计数可以累加，但摘要不能重复堆）
    assert first != second  # 计数变了
    assert len(service.store.load()["knowledge"]["increments"]) == 1


def test_harvest_without_keywords_returns_none(service):
    assert service.harvest_knowledge([{"text": "只有原句没有关键词"}]) is None
    assert service.store.load()["knowledge"]["increments"] == []


def test_top_topics_sorted(service):
    service.harvest_knowledge([_entry(["408"]), _entry(["408"]), _entry(["numpy"])])
    assert service.top_topics()[0] == ("408", 2)
