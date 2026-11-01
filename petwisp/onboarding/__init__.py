# -*- coding: utf-8 -*-
"""首次启动向导（阶段 C）：换机恢复的唯一入口。

桌宠的用户配置全部落在 ``%APPDATA%\\petwisp``（角色、考试日、模型
Key），换一台机器就等于从零开始。向导把这三件"不做就没法用"的事收进一次
三步式问答里，用户点完就能直接玩，不必先学会摸进设置页。

设计边界：
* 只依赖 Qt + catalog + growth，**不 import petwisp.chat 顶层**（no-chat 打包变体
  没有该模块，向导本身必须在两种变体里都能打开）；
* 判定与写盘拆开：``state`` 负责"该不该弹"，``wizard`` 负责"弹出来问什么"，
  两者都能被测试单独驱动（GUI 用例走 offscreen 平台）。
"""
from __future__ import annotations

from .state import FIRST_RUN_KEY, mark_first_run_done, needs_first_run
from .wizard import OnboardingWizard, maybe_start_first_run

__all__ = [
    "FIRST_RUN_KEY",
    "OnboardingWizard",
    "mark_first_run_done",
    "maybe_start_first_run",
    "needs_first_run",
]
