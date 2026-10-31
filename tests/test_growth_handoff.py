# -*- coding: utf-8 -*-
"""半自动转交外部助手（``pet.growth.handoff``）。

验收口径：

* 超纲识别不能把"随口一问"也转走（否则桌宠形同虚设）；
* 打包内容必须带够上下文，让对方助手不用再问一遍；
* 剪贴板/唤起任一失败都不抛异常，且要如实说明用户自己能做什么。
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pet.growth import handoff as ho

NOW = datetime(2026, 10, 30, 15, 0, 0)


def _archive() -> dict:
    return {
        "profile": {
            "learning": {
                "goal": "上岸",
                "exam_date": "2027-12-25",
                "subjects": ["数学一", "英语一", "408"],
            }
        },
        "progress": {"focus_minutes": 45},
        "topics": {"2026-10-30": {"数学一": 3, "英语长难句": 2}},
    }


# ------------------------------------------------------------------ 超纲识别
@pytest.mark.parametrize(
    "text",
    [
        "帮我把这份 PDF 转成 Word",
        "写个脚本把下载文件夹里的图片按日期重命名",
        "搜一下上海交大 2028 年计算机专硕的复试线",
        "帮我做个 PPT 讲讲 408 复习计划",
    ],
)
def test_strong_requests_go_to_external(text):
    ok, got = ho.is_complex(text)
    assert ok is True, f"{text} 得分 {got} 被漏判"


@pytest.mark.parametrize(
    "text",
    ["在吗", "今天好困", "我今天学了 3 小时", "怎么啦", "陪我聊会", ""],
)
def test_small_talk_stays_in_pet(text):
    assert ho.is_complex(text)[0] is False


def test_short_question_with_weak_cue_stays():
    """「数学怎么学」这种短问句桌宠自己能答，不该弹转交气泡。"""
    assert ho.is_complex("数学怎么学")[0] is False


def test_long_text_gets_extra_weight():
    short = "帮我看看这个怎么弄"
    long = "帮我看看这个怎么弄，" + "我觉得现在的问题主要在数学一的线性代数部分，" * 3
    assert ho.score(long) > ho.score(short)


def test_threshold_is_configurable():
    text = "解释一下这个方法"
    assert ho.is_complex(text, threshold=1)[0] is True
    assert ho.is_complex(text, threshold=99)[0] is False


# ------------------------------------------------------------------ 上下文打包
def test_payload_carries_request_and_context():
    payload = ho.build_payload("帮我把 PDF 转 Word", _archive(), NOW)
    assert "帮我把 PDF 转 Word" in payload
    assert "上岸" in payload
    assert "2027-12-25" in payload
    assert "还剩 421 天" in payload
    assert "数学一" in payload
    assert "45 分钟" in payload


def test_payload_without_archive_is_still_valid():
    payload = ho.build_payload("查一下复试线", None, NOW)
    assert "查一下复试线" in payload
    assert "转交" in payload
    assert "近况" not in payload


def test_payload_survives_broken_archive():
    payload = ho.build_payload("帮我跑一下", {"profile": "坏数据", "topics": []}, NOW)
    assert "帮我跑一下" in payload


def test_payload_marks_past_exam_date():
    archive = {"profile": {"learning": {"exam_date": "2026-01-01"}}}
    payload = ho.build_payload("在吗", archive, NOW)
    assert "已过去" in payload


def test_proposal_text_quotes_and_truncates():
    text = "帮我把整个桌面按项目分类整理一遍然后把结果做个报告"
    shown = ho.proposal_text(text)
    assert "…" in shown
    assert ho.DEFAULT_LABEL in shown
    assert ho.proposal_text("") .startswith("这事")


# ------------------------------------------------------------------ 转交执行
def test_handoff_copies_then_launches():
    seen = {}

    def clip(text):
        seen["clip"] = text
        return True

    def launch():
        seen["launch"] = True
        return True

    result = ho.handoff("写个脚本整理图片", _archive(), now=NOW, clipboard=clip, launcher=launch)
    assert result == {
        "ok": True,
        "payload": result["payload"],
        "copied": True,
        "launched": True,
        "reason": "已复制并唤起外部助手，直接粘贴发送",
    }
    assert "写个脚本整理图片" in seen["clip"]
    assert seen["launch"] is True


def test_handoff_reports_manual_paste_when_launch_fails():
    result = ho.handoff(
        "写个脚本", None, now=NOW,
        clipboard=lambda text: True, launcher=lambda: False,
    )
    assert result["ok"] is True
    assert result["launched"] is False
    assert "粘贴" in result["reason"]


def test_handoff_does_not_launch_when_clipboard_fails():
    called = {"launch": False}

    def launch():
        called["launch"] = True
        return True

    result = ho.handoff(
        "写个脚本", None, now=NOW,
        clipboard=lambda text: False, launcher=launch,
    )
    assert result["ok"] is False
    assert result["copied"] is False
    assert result["launched"] is False
    assert "剪贴板" in result["reason"]
    assert called["launch"] is False


def test_handoff_swallows_exceptions():
    def boom(text):
        raise RuntimeError("剪贴板炸了")

    result = ho.handoff("写个脚本", None, now=NOW, clipboard=boom)
    assert result["ok"] is False
    assert "剪贴板" in result["reason"]


def test_handoff_allow_launch_false_keeps_copy_only():
    result = ho.handoff(
        "写个脚本", None, now=NOW,
        clipboard=lambda text: True, launcher=lambda: True, allow_launch=False,
    )
    assert result["copied"] is True
    assert result["launched"] is False


# ------------------------------------------------------------------ 可配置目标
def test_default_is_clipboard_only(monkeypatch):
    """公开版默认不绑定任何外部程序：未配置 scheme 时只复制，不唤起。"""
    monkeypatch.delenv(ho.SCHEME_ENV, raising=False)
    assert ho.handoff_scheme(None) == ""
    assert ho.launch_external("") is False


def test_scheme_and_label_come_from_env(monkeypatch):
    monkeypatch.setenv(ho.SCHEME_ENV, "myagent://open")
    monkeypatch.setenv(ho.LABEL_ENV, "小助手")
    assert ho.handoff_scheme(None) == "myagent://open"
    assert ho.handoff_label(None) == "小助手"
    assert ho.button_label("小助手") == "转给 小助手"


def test_scheme_rejects_non_url_garbage(monkeypatch):
    """配置脏数据（例如误填命令行）不能被当成目标执行。"""
    monkeypatch.setenv(ho.SCHEME_ENV, "calc.exe")
    assert ho.handoff_scheme(None) == ""
