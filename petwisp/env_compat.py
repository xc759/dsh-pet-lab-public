# -*- coding: utf-8 -*-
"""旧环境变量前缀兼容：``DSH_PET_*`` → ``PETWISP_*``。

改名（dsh-pet → petwisp）之前，进程间约定的环境变量一律用 ``DSH_PET_`` 前缀。
用户写在启动脚本、快捷方式、CI 里的旧变量，以及旧版本残留的父进程，仍可能设置
旧名；本模块在包初始化时把它们一次性镜像到新名，让新旧两种写法都能被读到。

镜像是一次性的、单向的（旧→新），且**只在同名新变量不存在时**才搬：新名出现即
以新名为准，包括显式设成空串的情形（空串在本项目里表示"清空该进程级覆盖"，见
``app.py`` 里 ``PETWISP_INSTANCE`` 的处理，不能被旧值又填回来）。

本模块只依赖 ``os``：它在 ``petwisp/__init__.py`` 里被调用，会出现在所有入口的
导入图最前端，必须保持零第三方依赖、零子模块依赖。
"""
from __future__ import annotations

import os

#: 改名前的环境变量前缀（拼接写法：避免本文件自身的旧名字面量被全仓改名脚本命中）。
LEGACY_ENV_PREFIX = "DSH_" + "PET_"
#: 现行环境变量前缀。
ENV_PREFIX = "PETWISP_"


def migrate_legacy_env(environ=None) -> list[str]:
    """把 ``DSH_PET_XXX`` 的值镜像到 ``PETWISP_XXX``（新名已存在则跳过）。

    返回被搬运的旧变量名列表，便于测试断言；生产路径不关心返回值。
    幂等：重复调用不会覆盖已经存在的新名。
    """
    env = os.environ if environ is None else environ
    moved: list[str] = []
    for key in list(env.keys()):
        if not key.startswith(LEGACY_ENV_PREFIX):
            continue
        new_key = ENV_PREFIX + key[len(LEGACY_ENV_PREFIX):]
        if new_key in env:
            continue
        env[new_key] = env[key]
        moved.append(key)
    return moved
