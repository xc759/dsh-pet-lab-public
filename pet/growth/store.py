# -*- coding: utf-8 -*-
"""成长档案的读写：原子写 + 跨进程锁 + 坏文件不静默吞掉。

纪律与 ``Config.save`` / ``TodoStore`` 一致，直接复用 ``pet.config_write_lock``：

* 写：``locked_update_json`` 在目录锁内"重读 → 合并 → 原子替换"，
  两个实例同时写不会互相整表覆盖；
* 读：``read_json_dict`` 给出 6 种状态，本模块对 ``corrupt`` / ``not-object`` /
  ``unreadable`` **先备份再重建**，并把备份路径记在日志里——用户手改坏了也找得回；
* 自愈：读到的档案经 ``normalize_archive`` 补齐字段后若与盘上不一致，回写一次。
  回写失败只记日志，不让读操作抛异常（桌宠启动路径不能因为档案写不进去就打不开）。
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import config_write_lock as cwl
from .schema import (
    SCHEMA_VERSION,
    archive_path,
    default_archive,
    growth_dir,
    normalize_archive,
    now_iso,
)

logger = logging.getLogger(__name__)

#: 坏文件备份保留的条数（超出删最旧）
MAX_CORRUPT_BACKUPS = 5


class GrowthStore:
    """单个档案文件的读写门面。多开实例各用各的文件。"""

    def __init__(self, config_dir, instance_id: str = "") -> None:
        self.config_dir = Path(config_dir)
        self.instance_id = (instance_id or "").strip()
        self.path = archive_path(self.config_dir, self.instance_id)
        self.dir = growth_dir(self.config_dir)

    # ------------------------------------------------------------ 读
    def load(self) -> dict:
        """读档案；不存在则创建，损坏则备份后重建。**不抛异常**。"""
        data, status = cwl.read_json_dict(self.path)
        if status == "ok":
            archive = self._normalized(data)
            if archive != data:
                self._rewrite(archive)
            return archive
        if status == "missing":
            archive = self._fresh()
            self._rewrite(archive)
            return archive
        # corrupt / not-object / unreadable：腾开坏文件再重建
        backup = self.backup_corrupt(reason=status)
        logger.warning("成长档案不可用（%s），已备份到 %s 并重建", status, backup)
        archive = self._fresh()
        self._rewrite(archive)
        return archive

    def ensure(self) -> dict:
        """启动期调用：确保档案存在且字段齐全。"""
        return self.load()

    def _fresh(self) -> dict:
        archive = default_archive()
        archive["device"] = {"instance_id": self.instance_id}
        return archive

    def _normalized(self, raw: dict) -> dict:
        return normalize_archive(raw)

    def _rewrite(self, archive: dict) -> bool:
        try:
            cwl.atomic_write_json(self.path, archive)
            return True
        except cwl.ConfigWriteError:
            logger.warning("成长档案回写失败: %s", self.path, exc_info=True)
            return False

    # ------------------------------------------------------------ 写
    def update(self, mutate: Callable[[dict], Any]) -> dict:
        """锁内更新：``mutate(archive)`` 可以就地改并返回 None，也可以返回新字典。

        返回**写下去的那份**归一档案。写盘失败会抛 ``ConfigWriteError``——调用方
        （UI / 服务）需要知道"这次没存住"，不能假装成功。
        """
        corrupt_backed_up = False

        def _mutator(current: dict) -> dict:
            nonlocal corrupt_backed_up
            archive = normalize_archive(current or {})
            changed = mutate(archive)
            result = normalize_archive(changed if isinstance(changed, dict) else archive)
            result["updated_at"] = now_iso()
            return result

        # 坏文件先备份：on_broken="replace" 会直接丢掉原内容
        _, status = cwl.read_json_dict(self.path)
        if status in ("corrupt", "not-object"):
            self.backup_corrupt(reason=status)
            corrupt_backed_up = True

        try:
            return cwl.locked_update_json(
                self.path, _mutator, on_broken="replace", default_factory=self._fresh)
        except cwl.ConfigWriteError:
            if corrupt_backed_up:
                logger.error("成长档案已在坏文件备份后重建失败: %s", self.path)
            raise

    # ------------------------------------------------------------ 坏文件备份
    def backup_corrupt(self, reason: str = "corrupt") -> Path | None:
        """把当前坏文件改名留档，返回备份路径（无文件可备份时 None）。"""
        if not self.path.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.stem}.{reason}-{stamp}.json")
        try:
            shutil.copy2(self.path, target)
        except OSError:
            logger.warning("坏文件备份失败: %s", self.path, exc_info=True)
            return None
        self._trim_backups(reason)
        return target

    def _trim_backups(self, reason: str) -> None:
        try:
            backups = sorted(
                self.path.parent.glob(f"{self.path.stem}.{reason}-*.json"),
                key=lambda p: p.stat().st_mtime,
            )
        except OSError:
            return
        for old in backups[:-MAX_CORRUPT_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------ 诊断
    def health(self) -> dict:
        """面板/日志用的轻量健康信息（不读全量档案）。"""
        data, status = cwl.read_json_dict(self.path)
        size = 0
        try:
            size = self.path.stat().st_size
        except OSError:
            pass
        return {
            "path": str(self.path),
            "status": status,
            "schema_version": (data or {}).get("schema_version", 0) if data else 0,
            "expected_version": SCHEMA_VERSION,
            "bytes": size,
        }
