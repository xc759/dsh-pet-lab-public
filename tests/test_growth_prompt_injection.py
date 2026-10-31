# -*- coding: utf-8 -*-
"""成长档案 → 对话提示词注入（``pet.chat.prompt`` 的 growth 钩子）。

红线：**未注册 provider 时输出必须逐字节不变**（上游有"无记忆 == 老版本"的
字节级断言），注册了但档案为空时同样不加任何字符。
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pet.chat.models import ChatSettings
from pet.chat.prompt import PromptBuilder, growth_text, set_growth_text_provider
from pet.growth.service import GrowthService


@pytest.fixture(autouse=True)
def _clean_registry():
    set_growth_text_provider(None)
    yield
    set_growth_text_provider(None)


@pytest.fixture()
def settings():
    return ChatSettings.defaults()


@pytest.fixture()
def builder():
    return PromptBuilder()


def _system(builder, settings, now=None):
    messages = builder.build_messages(settings, "dafeiyu", [], "在吗",
                                      now=now or datetime(2026, 10, 30, 12).astimezone())
    return messages[0]["content"]


def test_no_provider_means_no_growth_block(builder, settings):
    baseline = _system(builder, settings)
    assert "成长背景" not in baseline
    # 注册一个返回空串的 provider，输出必须与未注册完全一致
    set_growth_text_provider(lambda now=None: "")
    assert _system(builder, settings) == baseline


def test_registered_text_is_appended_after_memory(builder, settings):
    set_growth_text_provider(lambda now=None: "当前阶段目标：上岸 408\n距考试还有 414 天")
    system = _system(builder, settings)
    assert "【成长背景·来自本机档案" in system
    assert "当前阶段目标：上岸 408" in system
    assert system.index("成长背景") > system.index("当前时间")


def test_provider_exception_is_swallowed(builder, settings):
    def _boom(now=None):
        raise RuntimeError("档案坏了")

    set_growth_text_provider(_boom)
    baseline = _system(builder, settings)
    set_growth_text_provider(None)
    assert _system(builder, settings) == baseline


def test_provider_without_arguments_is_supported(builder, settings):
    set_growth_text_provider(lambda: "已连续使用 5 天")
    assert "已连续使用 5 天" in _system(builder, settings)


def test_growth_text_handles_none_and_blanks():
    set_growth_text_provider(lambda now=None: None)
    assert growth_text() == ""
    set_growth_text_provider(lambda now=None: "   \n  ")
    assert growth_text() == ""


def test_service_provider_end_to_end(tmp_path):
    class _Cfg:
        dir = tmp_path
        instance_id = ""
        def get(self, key, default=None):
            return default

    service = GrowthService(_Cfg())
    service.start()
    # 未填任何资料 → 注入为空，对话与老版本一致
    set_growth_text_provider(service.injection_text)
    builder = PromptBuilder()
    assert "成长背景" not in _system(builder, ChatSettings.defaults())

    service.store.update(lambda a: a["profile"]["learning"].update({
        "goal": "26 考研 408",
        "exam_date": "2027-12-18",
    }))
    system = _system(builder, ChatSettings.defaults(),
                     now=datetime(2026, 10, 30, 12).astimezone())
    assert "26 考研 408" in system
    assert "距考试还有 414 天" in system
