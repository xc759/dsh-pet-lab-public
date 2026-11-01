# -*- coding: utf-8 -*-
"""对话感知（``petwisp.growth.perceive`` + ``GrowthService.note_user_text``）。

两条红线在这里落成断言：
1. **不存原句**：写完档案，文件里不允许出现用户那句话；
2. **判不准不说**：模糊表述（"还行""正常"）不产生任何情绪标签、不写盘。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petwisp.chat.models import ChatSettings
from petwisp.chat.prompt import PromptBuilder, set_growth_text_observer, set_growth_text_provider
from petwisp.growth import perceive
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


# ------------------------------------------------------------------ classify
@pytest.mark.parametrize("text", [
    "今天数学刷了两套真题",
    "408 的数据结构好难",
    "背单词背到吐",
    "政治肖四还没背",
])
def test_study_text_is_detected(text):
    info = perceive.classify(text)
    assert info["study"] is True
    assert info["topics"]


def test_subject_is_merged_to_canonical_name():
    assert perceive.classify("高数看了两章")["subject"] == "数学"
    assert perceive.classify("操作系统进程调度")["subject"] == "408"
    assert perceive.classify("英语阅读理解错三个")["subject"] == "英语"
    assert perceive.classify("肖八选择才 28 分")["subject"] == "政治"


@pytest.mark.parametrize("text,state", [
    ("好累啊，眼睛睁不开", "tired"),
    ("又摸鱼一上午，看不进去", "distracted"),
    ("感觉考不上了，焦虑", "low"),
    ("今天效率高，学进去了", "energized"),
])
def test_emotion_words_map_to_states(text, state):
    assert perceive.classify(text)["emotion"] == state


@pytest.mark.parametrize("text", ["还行吧", "今天正常", "在的", "", "   "])
def test_vague_text_yields_nothing(text):
    info = perceive.classify(text)
    assert info["emotion"] == ""
    assert info["study"] is False
    assert info["topics"] == []


# ------------------------------------------------------------------ 写档案
def test_note_records_topics_and_emotion_without_raw_text(service, tmp_path):
    raw = "今天数学刷了两套真题，好累啊"
    assert service.note_user_text(raw) is not None

    archive = json.loads(service.store.path.read_text(encoding="utf-8"))
    assert archive["knowledge"]["topics"]["数学"] == 1
    assert archive["emotion"]["mood_hits"]["tired"] == 1
    assert archive["emotion"]["state"] == "tired"
    assert archive["emotion"]["daily"][-1]["mood"] == -1

    # 红线：原句不入档
    assert raw not in service.store.path.read_text(encoding="utf-8")


def test_note_without_signal_does_not_write(service):
    before = service.store.path.stat().st_mtime_ns
    assert service.note_user_text("嗯嗯好的") is None
    assert service.store.path.stat().st_mtime_ns == before


def test_duplicate_text_within_window_counted_once(service):
    text = "背单词 200 个"
    assert service.note_user_text(text) is not None
    assert service.note_user_text(text) is None
    archive = service.store.load()
    assert archive["knowledge"]["topics"].get("单词", 0) <= 1
    assert sum(archive["knowledge"]["topics"].values()) >= 1


def test_energy_moves_with_emotion_and_stays_in_range(service):
    for _ in range(30):
        service.set_emotion("calm", energy=100)
        service.note_user_text(f"好累啊 {_}")
    assert 0 <= service.store.load()["emotion"]["energy"] <= 100


def test_study_only_text_gives_small_positive_mood(service):
    service.note_user_text("今天复习了线代")
    emotion = service.store.load()["emotion"]
    assert emotion["mood_hits"] == {}
    assert emotion["daily"][-1]["mood"] == perceive.STUDY_MOOD_BONUS


def test_prompt_build_feeds_the_archive(service):
    set_growth_text_provider(None)
    set_growth_text_observer(service.note_user_text)
    try:
        PromptBuilder().build_messages(
            ChatSettings.defaults(), "dafeiyu", [], "408 真题刷完了，好累",
            now=datetime(2026, 10, 30, 21).astimezone(),
        )
    finally:
        set_growth_text_observer(None)
    archive = service.store.load()
    assert archive["knowledge"]["topics"]["408"] == 1
    assert archive["emotion"]["mood_hits"]["tired"] == 1
