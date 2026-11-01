# -*- coding: utf-8 -*-
"""成长档案（阶段 B 陪伴型助手）的存储底座。

对外只暴露这一层：``GrowthStore`` 负责落盘，``GrowthService`` 负责在桌宠进程
里接线。schema / 迁移 / 周快照都在本包内，不散落到 ``config.py``。
"""
from __future__ import annotations

from .schema import SCHEMA_VERSION, archive_path, default_archive, normalize_archive
from .service import GrowthService
from .store import GrowthStore
from .snapshot import build_summary, snapshot_period, write_snapshot

__all__ = [
    "SCHEMA_VERSION",
    "GrowthService",
    "GrowthStore",
    "archive_path",
    "build_summary",
    "default_archive",
    "normalize_archive",
    "snapshot_period",
    "write_snapshot",
]
