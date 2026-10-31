# -*- coding: utf-8 -*-
"""一次性迁移：把"散落在旧结构里的存量信息"补进成长档案。

原则：

1. **只补不覆盖**——目标字段已有值就跳过；
2. **只搬结构不搬原文**——会话只统计条数与角色数，不导入对话内容；
3. **幂等**——同一份档案反复跑迁移，结果不变（迁移记录表按 ``from→to`` 去重）；
4. **可失败**——任何一步读不到就跳过该步，不影响档案本身。

迁移来源目前有两处：

* ``Config.data["character"]`` → ``identity.character_id``（当前形象）；
* ``<config>/sessions[-<instance>]/<character>/*.json`` → ``sessions.total`` /
  ``sessions.by_character`` / ``sessions.last_at``。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .schema import SCHEMA_VERSION, now_iso

logger = logging.getLogger(__name__)


def _session_root(config_dir, instance_id: str = "") -> Path:
    """旧会话目录，规则必须与 ``chat.session_store.SessionStore`` 完全一致。

    那边对 ``slot-0``（主实例）**不加后缀**、其它实例加 ``-<id>``；这里若按
    "有 id 就加后缀"处理，主实例会去扫一个不存在的 ``sessions-slot-0``，
    存量计数永远为 0（静默丢数据）。
    """
    identity = str(instance_id or "").strip()
    suffix = f"-{identity}" if identity and identity != "slot-0" else ""
    return Path(config_dir) / f"sessions{suffix}"


def scan_legacy_sessions(config_dir, instance_id: str = "") -> dict:
    """统计旧会话目录：总条数、按角色的条数、最近修改时间。

    只读 ``*.json`` 文件的数量与 mtime，**不解析内容**。
    """
    root = _session_root(config_dir, instance_id)
    total = 0
    by_character: dict[str, int] = {}
    last_at: str | None = None
    if not root.exists():
        return {"total": 0, "by_character": {}, "last_at": None}
    try:
        character_dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return {"total": 0, "by_character": {}, "last_at": None}
    for char_dir in character_dirs:
        try:
            files = [f for f in char_dir.glob("*.json") if f.is_file()]
        except OSError:
            continue
        if not files:
            continue
        count = len(files)
        total += count
        by_character[char_dir.name] = count
        try:
            newest = max(f.stat().st_mtime for f in files)
            stamp = datetime.fromtimestamp(newest).astimezone().replace(microsecond=0).isoformat()
            if last_at is None or stamp > last_at:
                last_at = stamp
        except OSError:
            pass
    return {"total": total, "by_character": by_character, "last_at": last_at}


def _already(archive: dict, source: int, target: int) -> bool:
    for row in archive.get("migrations") or []:
        if row.get("from") == source and row.get("to") == target:
            return True
    return False


def migrate(store, *, config=None, moment: datetime | None = None) -> dict:
    """执行迁移并返回迁移后的档案。已是最新且无缺口时原样返回。"""
    stamp = now_iso(moment)
    changes: list[str] = []

    def _mutate(current: dict) -> None:
        # --- 版本号：0（无版本字段）视为 v1 之前，补记一条迁移
        if int(current.get("schema_version") or 0) < SCHEMA_VERSION:
            from_version = int(current.get("schema_version") or 0)
            current["schema_version"] = SCHEMA_VERSION
            current.setdefault("migrations", []).append(
                {"from": from_version, "to": SCHEMA_VERSION, "at": stamp})
            changes.append(f"schema {from_version}->{SCHEMA_VERSION}")

        # --- 当前形象
        identity = current.setdefault("identity", {})
        if not identity.get("character_id") and config is not None:
            try:
                character = str(config.get("character") or "").strip()
            except Exception:  # noqa: BLE001 - 配置对象不可用就跳过
                character = ""
            if character:
                identity["character_id"] = character[:60]
                changes.append("identity.character_id")

        # --- 旧会话计数（只统计，不搬内容）。扫描过就打标记，避免每次启动重扫。
        device = current.setdefault("device", {})
        if not device.get("legacy_sessions_scanned"):
            legacy = scan_legacy_sessions(store.config_dir, store.instance_id)
            sessions = current.setdefault("sessions", {})
            if legacy["total"] and int(sessions.get("total") or 0) == 0:
                sessions["total"] = legacy["total"]
                sessions["by_character"] = dict(legacy["by_character"])
                sessions["last_at"] = legacy["last_at"]
                changes.append(f"sessions.total={legacy['total']}")
            device["legacy_sessions_scanned"] = True

    # 版本已最新时也走一次"补空"：形象/会话计数可能这次才首次可用（幂等）。
    result = store.update(_mutate)

    if changes:
        logger.info("成长档案迁移完成: %s", ", ".join(changes))
    return result


def needs_migration(store) -> bool:
    """版本落后或关键段为空 → 需要跑迁移（供启动期判断，避免每次全量 update）。"""
    archive = store.load()
    if int(archive.get("schema_version") or 0) < SCHEMA_VERSION:
        return True
    if not (archive.get("identity") or {}).get("character_id"):
        return True
    if not (archive.get("device") or {}).get("legacy_sessions_scanned"):
        return True
    return False
