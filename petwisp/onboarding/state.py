# -*- coding: utf-8 -*-
"""首次启动判定（纯逻辑，不依赖 Qt）。

判定为什么不能只看一个布尔键：从 v5.1-B 及更早版本升级上来的用户配置里
**没有** ``first_run_done`` 键，只看键会让全体老用户一升级就变"新人"、被
弹一次向导。所以拆两层——

1. ``first_run_done`` 为真：一定不是首次（走完向导，或主动跳过/关窗）；
2. 配置目录里已经有 ``config*.json``：老用户，视为已配置完成（升级路径）；
3. 两条都不成立（全新目录、连配置文件都没有）：这才是首次。

判定只读不写盘：真正落 ``first_run_done`` 是用户点「完成」或在向导里按
「跳过」/关窗的时候（``wizard`` 负责），因此中途崩溃不会吞掉向导。
"""
from __future__ import annotations

import logging
from pathlib import Path

#: 完成标记键名（写进 config.json；默认 False，见 pet/config.py 的 _initial_data）
FIRST_RUN_KEY = "first_run_done"


def _config_dir(config) -> Path | None:
    directory = getattr(config, "dir", None)
    return Path(directory) if directory is not None else None


def has_existing_config(config) -> bool:
    """配置目录里是否已经有配置文件（= 这台机器之前跑过桌宠）。

    认 ``config.json`` 与多开实例的 ``config-slot-*.json``：主配置被用户手动
    删掉、只剩副槽时同样算"老用户"，否则向导会在他多开的那只身上又弹一次。
    """
    directory = _config_dir(config)
    if directory is None or not directory.is_dir():
        return False
    if (directory / "config.json").is_file():
        return True
    try:
        return any(directory.glob("config-slot-*.json"))
    except OSError:
        return False


def needs_first_run(config) -> bool:
    """是否需要弹首次启动向导。

    ``config.get`` 在真实 Config 上读的是内存态（已合过盘上值），测试可传任何
    带 ``get`` 的替身；取值异常一律按"已完成"处理——向导本身不该成为启动路径
    上的新崩溃点。
    """
    try:
        if bool(config.get(FIRST_RUN_KEY, False)):
            return False
    except Exception:
        logging.exception("读取首次启动标记失败，按已完成处理")
        return False
    return not has_existing_config(config)


def mark_first_run_done(config, *, save: bool = True) -> bool:
    """写完成标记（幂等）；返回是否写成功。

    先改内存再落盘：即使写盘失败（磁盘只读、被杀毒锁文件），本次运行也不会
    再弹；下次启动会重弹一次，属于可接受的降级。
    """
    try:
        config.set(FIRST_RUN_KEY, True)
    except Exception:
        logging.exception("写入首次启动标记失败")
        return False
    if not save:
        return True
    try:
        return bool(config.save())
    except Exception:
        logging.exception("保存首次启动标记失败")
        return False
