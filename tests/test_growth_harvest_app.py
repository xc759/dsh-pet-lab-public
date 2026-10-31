# -*- coding: utf-8 -*-
"""app 层的知识提炼接线（``AppShell._harvest_growth_knowledge``）。

只用真 ``ChatMemory`` 写一个临时档案，再断言"并进成长档案的只有关键词"。
Qt 不需要起壳：用 ``AppShell.__new__`` 装一个最小实例。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pet.app import AppShell
from pet.chat.memory import ChatMemory, memory_path


class _Cfg:
    """``config`` 的最小替身：``dir`` 指向临时档案，``get`` 读开关。"""

    instance_id = ""

    def __init__(self, root, **values):
        self.dir = root
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _fire(**config):
    app = AppShell.__new__(AppShell)
    app.config = _Cfg(config.pop("root"), **config)
    app._identity_config = lambda: {"character": "dsh"}
    calls = []

    class _Svc:
        def harvest_knowledge(self, entries, moment=None):
            calls.append(list(entries))
            return {"knowledge": {}}

    app.growth_service = _Svc()
    return app, calls


def test_harvest_reads_memory_and_forwards_entries(tmp_path):
    ChatMemory(memory_path(tmp_path, "", "dsh")).add("408 的 LRU 页面置换怎么算")
    app, calls = _fire(root=tmp_path)
    app._harvest_growth_knowledge(force=True)
    assert len(calls) == 1
    assert calls[0] and calls[0][0]["keywords"]


def test_harvest_is_throttled_unless_forced(tmp_path):
    ChatMemory(memory_path(tmp_path, "", "dsh")).add("408 的 LRU")
    app, calls = _fire(root=tmp_path)
    app._harvest_growth_knowledge(force=True)
    app._harvest_growth_knowledge()   # 6 小时内只读一次
    assert len(calls) == 1


def test_harvest_respects_switch(tmp_path):
    ChatMemory(memory_path(tmp_path, "", "dsh")).add("408 的 LRU")
    app, calls = _fire(root=tmp_path, growth_harvest_enabled=False)
    app._harvest_growth_knowledge(force=True)
    assert calls == []


def test_harvest_survives_a_broken_memory_file(tmp_path):
    path = memory_path(tmp_path, "", "dsh")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是合法 JSON", encoding="utf-8")
    app, calls = _fire(root=tmp_path)
    app._harvest_growth_knowledge(force=True)   # 不抛
    assert calls == []


def test_harvest_without_service_is_a_noop(tmp_path):
    app, calls = _fire(root=tmp_path)
    app.growth_service = None
    app._harvest_growth_knowledge(force=True)
    assert calls == []
