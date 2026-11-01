# -*- coding: utf-8 -*-
"""外部形象包导入（兼容层）。

把**不是 PetWisp 原生格式**的形象素材，一次性转成运行时可播的形象包：

| 来源 | 形态 | 适配器 |
| --- | --- | --- |
| 原生 WebM/GIF | ``<角色>/videos/<动作>/*.webm`` | :mod:`petwisp.pack_import.video` |
| Petdex 包 | ``pet.json`` + ``spritesheet.webp`` | :mod:`petwisp.pack_import.petdex` |
| 通用精灵表 | 一张网格图 + 一份 ``petwisp-pack.json`` 描述 | :mod:`petwisp.pack_import.grid` |

三个适配器都**只做数据**：读源、算帧表、交给 :mod:`petwisp.pack_import.build`
落成 ``character.json`` + 无损 WebP 帧序列。它们不碰 Qt、不碰运行时，
产物一律走 :mod:`petwisp.character_pack` 的既有契约（``core`` 闭包、
``ENCODER_DESC`` 编码档、``content_sha`` 存储身份），
所以导入完不需要任何运行时改动就能被采纳。

命令行入口见 :mod:`petwisp.pack_import.cli`：``python -m petwisp.pack_import``。
"""

from __future__ import annotations

from .spec import (CANVAS, CORE_CATEGORIES, DEFAULT_FPS, FEET_Y, ImportError_,
                   PackSpec, load_spec, spec_from_dict)

__all__ = [
    "CANVAS", "CORE_CATEGORIES", "DEFAULT_FPS", "FEET_Y",
    "ImportError_", "PackSpec", "load_spec", "spec_from_dict",
]
